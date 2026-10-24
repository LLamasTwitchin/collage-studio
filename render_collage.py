#!/usr/bin/env python3
"""
render_collage.py — turn a Collage Studio layout into a video file.

Design in the browser (index.html), export layout.json, drop your media in the
same folder, and render. Uses ffmpeg for the heavy lifting (via moviepy if it is
installed, otherwise straight ffmpeg filter graphs, which are faster).

    python render_collage.py layout.json
    python render_collage.py layout.json -o my_collage.mp4 --fps 30 --seconds 12
    python render_collage.py layout.json --media-dir ./assets --no-audio

Requires: ffmpeg on PATH.  Optional: moviepy + Pillow for the pure-Python path.

layout.json shape (format "collage-studio/v1"):

    {
      "stage":    { "width": 1280, "height": 720, "background": "#000000" },
      "timeline": { "duration": 20, "loop": true, "mute": true },
      "defaultFade": 0.45,
      "cells": [
        { "id": "c1", "rect": [x, y, w, h], "fit": "cover",
          "start": 0.0, "fade": 0.45,
          "media": { "kind": "video", "file": "clip.mp4",
                     "sourceWidth": 1920, "sourceHeight": 1080 } }
      ]
    }
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

# --------------------------------------------------------------------------- #
# Layout model
# --------------------------------------------------------------------------- #

VIDEO_EXT = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff"}


@dataclass
class Media:
    kind: str                 # "image" | "video"
    file: str
    path: Path | None = None  # resolved on disk
    source_width: int | None = None
    source_height: int | None = None

    @property
    def exists(self) -> bool:
        return self.path is not None and self.path.exists()


@dataclass
class Cell:
    id: str
    x: float
    y: float
    w: float
    h: float
    fit: str
    start: float
    fade: float
    media: Media | None

    @property
    def has_media(self) -> bool:
        return self.media is not None and self.media.exists


@dataclass
class Layout:
    width: int
    height: int
    background: str
    duration: float
    loop: bool
    mute: bool
    default_fade: float
    cells: list[Cell]

    @classmethod
    def load(cls, path: Path, media_dir: Path | None = None) -> "Layout":
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or "cells" not in raw:
            raise ValueError(f"{path} is not a Collage Studio layout (no 'cells' key).")

        stage = raw.get("stage") or {}
        timeline = raw.get("timeline") or {}
        base = media_dir or path.parent

        cells: list[Cell] = []
        for i, spec in enumerate(raw["cells"]):
            rect = spec.get("rect") or [0, 0, stage.get("width", 1280), stage.get("height", 720)]
            if len(rect) != 4:
                raise ValueError(f"cell {i}: 'rect' must be [x, y, w, h].")

            media = None
            m = spec.get("media")
            if m and m.get("file"):
                media = Media(
                    kind=(m.get("kind") or "image").lower(),
                    file=m["file"],
                    path=_resolve(base, m["file"]),
                    source_width=m.get("sourceWidth"),
                    source_height=m.get("sourceHeight"),
                )

            cells.append(Cell(
                id=str(spec.get("id") or f"c{i + 1}"),
                x=float(rect[0]), y=float(rect[1]),
                w=float(rect[2]), h=float(rect[3]),
                fit=(spec.get("fit") or "cover").lower(),
                start=float(spec.get("start") or 0.0),
                fade=float(spec.get("fade", raw.get("defaultFade", 0.45))),
                media=media,
            ))

        return cls(
            width=int(stage.get("width") or 1280),
            height=int(stage.get("height") or 720),
            background=stage.get("background") or "#000000",
            duration=float(timeline.get("duration") or 20.0),
            loop=bool(timeline.get("loop", True)),
            mute=bool(timeline.get("mute", True)),
            default_fade=float(raw.get("defaultFade", 0.45)),
            cells=cells,
        )

    def minimum_duration(self) -> float:
        """Timeline must at least cover every cell's fade-in."""
        need = 0.0
        for c in self.cells:
            if c.has_media:
                need = max(need, c.start + c.fade + 0.5)
        return need


def _resolve(base: Path, filename: str) -> Path | None:
    """Find a media file by name, checking the base dir, then a few fallbacks."""
    name = os.path.basename(filename)
    for cand in (base / name, base / filename, Path.cwd() / name):
        if cand.exists():
            return cand
    return base / name          # keep the intended path for a clear error later


# --------------------------------------------------------------------------- #
# ffmpeg plumbing
# --------------------------------------------------------------------------- #

def which_ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        sys.exit(
            "ffmpeg was not found on PATH.\n"
            "  macOS:   brew install ffmpeg\n"
            "  Ubuntu:  sudo apt install ffmpeg\n"
            "  Windows: winget install Gyan.FFmpeg   (then reopen your terminal)"
        )
    return exe


def hex_to_ffmpeg_color(hex_color: str) -> str:
    """#0a0b10 -> 0x0a0b10 (ffmpeg's colour syntax)."""
    h = hex_color.strip().lstrip("#")
    if len(h) == 3:
        h = "".join(ch * 2 for ch in h)
    return "0x" + h.lower()


def probe_duration(path: Path) -> float | None:
    """Ask ffprobe for a media file's duration in seconds."""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    try:
        out = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        return float(out)
    except (subprocess.CalledProcessError, ValueError):
        return None


# --------------------------------------------------------------------------- #
# Filter graph construction  (the fast path — one ffmpeg invocation)
# --------------------------------------------------------------------------- #

def _even(n: float) -> int:
    """ffmpeg wants even dimensions for most encoders; round down to even."""
    v = max(2, int(round(n)))
    return v - (v % 2)


def _scale_fit(fit: str, w: int, h: int) -> str:
    """
    Produce the scale/crop chain that places a source inside w×h.

    cover   -> scale up to fill, then crop the overflow  (no distortion)
    contain -> scale down to fit inside, pad the rest    (letterbox)
    fill    -> stretch to exact dimensions               (may distort)
    """
    if fit == "fill":
        return f"scale={w}:{h}"

    if fit == "contain":
        return (
            f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black"
        )

    # cover (default)
    return (
        f"scale={w}:{h}:force_original_aspect_ratio=increase,"
        f"crop={w}:{h}"
    )


def build_filtergraph(layout: Layout, seconds: float, fps: int) -> tuple[str, str]:
    """
    Returns (filter_complex, final_label).

    Every cell becomes its own input, is normalised to its target rectangle,
    then is overlaid onto a background canvas in cell order (later cells stack
    on top, matching the browser's z-order). Fade-ins are applied per cell with
    the `fade` filter on the alpha channel.
    """
    parts: list[str] = []

    W, H = _even(layout.width), _even(layout.height)

    # --- background canvas ------------------------------------------------- #
    bg = hex_to_ffmpeg_color(layout.background)
    parts.append(f"color=c={bg}:s={W}x{H}:r={fps}:d={seconds:.3f}[bg]")
    current = "bg"

    audio_inputs: list[tuple[int, float]] = []   # (input index, start offset)

    input_index = 0
    for i, cell in enumerate(layout.cells):
        if not cell.has_media:
            continue

        media = cell.media
        assert media is not None
        cw, ch = _even(cell.w), _even(cell.h)
        cx, cy = _even(cell.x), _even(cell.y)
        tag = f"cell{i}"

        is_video = media.kind == "video"

        if is_video:
            idx = input_index
            input_index += 1
            src = f"[{idx}:v]"
            if not layout.mute:
                audio_inputs.append((idx, cell.start))
        else:
            src = f"[{input_index}:v]"
            input_index += 1

        # normalise the source into its rectangle
        chain = (
            f"{src}scale={cw}:{ch}:force_original_aspect_ratio=increase,"
            f"crop={cw}:{ch},setsar=1,format=rgba"
        )
        if cell.fade > 0:
            # `fade` acts on alpha because the stream is rgba
            chain += f",fade=t=in:st={cell.start:.3f}:d={max(cell.fade, 0.01):.3f}:alpha=1"
        parts.append(chain + f"[{tag}]")

        # overlay onto the running canvas
        parts.append(
            f"[{current}][{tag}]overlay={cx}:{cy}:"
            f"enable='between(t,{cell.start:.3f},{seconds:.3f})'[{current}o]"
        )
        current = f"{current}o"

    # --- output label ------------------------------------------------------ #
    parts.append(f"[{current}]format=yuv420p[vout]")

    graph = ";".join(parts)
    if audio_inputs and not layout.mute:
        idx, offset = audio_inputs[0]
        ms = int(offset * 1000)
        graph += (
            f";[{idx}:a]atrim=0:{seconds:.3f},asetpts=PTS-STARTPTS,"
            f"adelay={ms}|{ms}[aout]"
        )
        return graph, "[vout][aout]"

    return graph, "[vout]"


def _input_args_for(layout: Layout, seconds: float) -> list[str]:
    """Build the per-cell input flags, in the same order the graph expects."""
    args: list[str] = []
    for cell in layout.cells:
        if not cell.has_media:
            continue
        media = cell.media
        assert media is not None
        if media.kind == "video":
            # keep short clips running for the whole timeline
            args += ["-stream_loop", "-1", "-t", f"{seconds:.3f}", "-i", str(media.path)]
        else:
            # a still becomes a looped video stream of the right length
            args += ["-loop", "1", "-t", f"{seconds:.3f}", "-i", str(media.path)]
    return args


def render_ffmpeg(layout: Layout, out: Path, seconds: float, fps: int, crf: int, preset: str) -> None:
    if not any(c.has_media for c in layout.cells):
        sys.exit("No renderable cells — every cell is empty or its media is missing.")

    missing = [c.media.file for c in layout.cells
               if c.media and not c.media.exists]           # type: ignore[union-attr]
    if missing:
        print("Warning: these files were not found and will be skipped:")
        for m in missing:
            print(f"   - {m}")
        print("  (point --media-dir at the folder that holds them)")

    graph, out_label = build_filtergraph(layout, seconds, fps)

    cmd = [which_ffmpeg(), "-y", "-hide_banner", "-loglevel", "error", "-stats"]
    cmd += _input_args_for(layout, seconds)
    cmd += [
        "-filter_complex", graph,
        "-map", out_label,
        "-t", f"{seconds:.3f}",
        "-r", str(fps),
        "-c:v", "libx264",
        "-preset", preset,
        "-crf", str(crf),
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
    ]
    if "[aout]" in out_label:
        cmd += ["-c:a", "aac", "-b:a", "192k"]
    cmd += [str(out)]

    print(f"Rendering {layout.width}x{layout.height} -> {out}")
    print(f"  {seconds:.2f}s @ {fps}fps, "
          f"{sum(1 for c in layout.cells if c.has_media)} active cells")
    subprocess.run(cmd, check=True)
    size_mb = out.stat().st_size / 1_048_576
    print(f"Done: {out}  ({size_mb:.1f} MB)")


# --------------------------------------------------------------------------- #
# moviepy fallback path
# --------------------------------------------------------------------------- #

def render_moviepy(layout: Layout, out: Path, seconds: float, fps: int, crf: int, preset: str) -> None:
    try:
        from moviepy.editor import (  # type: ignore
            ColorClip, CompositeVideoClip, ImageClip, VideoFileClip,
        )
    except ImportError:
        sys.exit("moviepy is not installed. `pip install moviepy` or use the ffmpeg path.")

    from PIL import Image  # noqa: F401  (Pillow is a moviepy dependency)

    bg = ColorClip(size=(layout.width, layout.height),
                   color=_hex_rgb(layout.background), duration=seconds)
    layers = [bg]

    for cell in layout.cells:
        if not cell.has_media:
            continue
        media = cell.media
        assert media is not None

        if media.kind == "video":
            clip = VideoFileClip(str(media.path), audio=not layout.mute)
            if clip.duration and clip.duration < seconds:
                n = math.ceil(seconds / clip.duration)
                from moviepy.editor import concatenate_videoclips  # type: ignore
                clip = concatenate_videoclips([clip] * n)
            clip = clip.subclip(0, min(seconds, clip.duration or seconds))
        else:
            clip = ImageClip(str(media.path)).set_duration(seconds)

        clip = _fit_moviepy(clip, int(cell.w), int(cell.h), cell.fit)
        clip = clip.set_start(cell.start)
        if cell.fade > 0:
            clip = clip.crossfadein(cell.fade)
        layers.append(clip.set_position((int(cell.x), int(cell.y))))

    final = CompositeVideoClip(layers, size=(layout.width, layout.height))
    final.subclip(0, seconds).write_videofile(
        str(out), fps=fps, codec="libx264", audio_codec="aac",
        preset=preset, ffmpeg_params=["-crf", str(crf), "-pix_fmt", "yuv420p"],
        logger=None,
    )
    print(f"Done: {out}")


def _fit_moviepy(clip, w: int, h: int, fit: str):
    from moviepy.video.fx.all import resize  # type: ignore
    sw, sh = clip.size
    if not sw or not sh:
        return clip
    if fit == "fill":
        return resize(clip, (w, h))
    ratio = max(w / sw, h / sh) if fit == "cover" else min(w / sw, h / sh)
    resized = resize(clip, (int(sw * ratio), int(sh * ratio)))
    if fit == "cover":
        return resized.crop(x_center=resized.w / 2, y_center=resized.h / 2,
                            width=w, height=h)
    return resized


def _hex_rgb(h: str) -> tuple[int, int, int]:
    h = h.strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def main() -> None:
    ap = argparse.ArgumentParser(
        description="Render a Collage Studio layout.json to a video file.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("layout", type=Path, help="path to layout.json exported from the browser tool")
    ap.add_argument("-o", "--out", type=Path, default=None, help="output file (default: collage.mp4)")
    ap.add_argument("--media-dir", type=Path, default=None,
                    help="folder holding the media files (default: the layout's own folder)")
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--seconds", type=float, default=None,
                    help="override the timeline duration")
    ap.add_argument("--crf", type=int, default=20, help="x264 quality, lower = better (default 20)")
    ap.add_argument("--preset", default="medium", help="x264 speed preset (default medium)")
    ap.add_argument("--no-audio", action="store_true", help="drop all audio")
    ap.add_argument("--moviepy", action="store_true",
                    help="force the moviepy renderer instead of ffmpeg filter graphs")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the ffmpeg filter graph without running it")
    args = ap.parse_args()

    layout = Layout.load(args.layout, args.media_dir)

    seconds = args.seconds or layout.duration
    if args.seconds is None:
        need = layout.minimum_duration()
        if need > seconds:
            print(f"Note: timeline extended to {need:.2f}s so every cell can fade in.")
            seconds = need

    if args.no_audio:
        layout.mute = True

    out = args.out or Path("collage.mp4")
    out.parent.mkdir(parents=True, exist_ok=True)

    if args.dry_run:
        graph, label = build_filtergraph(layout, seconds, args.fps)
        print(graph)
        return

    if args.moviepy:
        render_moviepy(layout, out, seconds, args.fps, args.crf, args.preset)
    else:
        render_ffmpeg(layout, out, seconds, args.fps, args.crf, args.preset)


if __name__ == "__main__":
    main()
