# Collage Studio

A drag-and-drop collage board for images **and** MP4s with live playback, plus a
Python renderer that exports the same layout to a shareable video file.

Two halves, one shared format:

| | What it does | Needs |
|---|---|---|
| `index.html` | Interactive board — drop media onto cells, arrange, hit play, scrub, save the layout | A browser. Nothing else. |
| `render_collage.py` | Renders a `layout.json` to an MP4 collage | `ffmpeg` on PATH |

Design in the browser, export `layout.json`, render in Python. The two use the
same JSON so a layout round-trips cleanly.

---

## 1. The browser board

Open `index.html` — double-click it, or drag it into a browser tab. No server, no
build step, no dependencies. Everything runs locally; your media never leaves the
machine.

### Using it

- **Drop files.** Drop an image or MP4 anywhere and it lands in the first empty
  cell. Drop directly onto a specific cell to target it. Drop several at once and
  the first fills the cell you aimed at while the rest spill into new cells.
- **Pick a layout.** The preset grid on the right sets you up with 1×1, 2×2, 3×3,
  hero-left, strip, diamond and a few other splits. Existing media is carried over
  in reading order, so you can audition layouts without losing your images.
- **Play.** Hit **Play** (or `Space`). Each cell fades in at its own *start* time;
  videos loop while the timeline runs. Drag the scrubber to preview any moment.
- **Adjust.** Select any cell to set its exact position, size, `object-fit`, start
  time and fade. Bring-to-front / send-to-back controls the stacking order.

### Keyboard

| Key | Action |
|---|---|
| `Space` | Play / pause |
| `←` `→` `↑` `↓` | Nudge the selected cell 1px |
| `Shift` + arrows | Nudge 10px |
| `Delete` | Remove media from the selected cell |
| `Esc` | Close the layout dialog |

### Save and load

**Save layout** exports the arrangement as JSON — copy it, or download it as
`layout.json`. **Load layout** takes that JSON back and rebuilds the board.
Media is re-attached by filename, so a layout reloaded in the same session keeps
what you can see.

> Note on the browser side: the board holds your media as in-memory objects for
the session, and JSON can't carry those. So a saved layout records each file's
*name* — that's what the Python renderer uses to find them on disk.

---

## 2. The Python renderer

Takes a `layout.json` and produces an actual MP4 — cells composited, fades
applied, videos looped to fill the timeline.

```bash
python render_collage.py layout.json
python render_collage.py layout.json -o my_collage.mp4 --fps 30 --seconds 12
python render_collage.py layout.json --media-dir ./assets
python render_collage.py layout.json --dry-run        # print the graph, render nothing
```

Put `layout.json` in the same folder as your media, or point `--media-dir` at the
folder that holds them.

### Options

| Flag | Default | Meaning |
|---|---|---|
| `-o, --out` | `collage.mp4` | Output file |
| `--media-dir` | layout's folder | Where the media files live |
| `--fps` | `30` | Frame rate |
| `--seconds` | timeline duration | Override the length |
| `--crf` | `20` | x264 quality, lower = better |
| `--preset` | `medium` | x264 speed preset |
| `--no-audio` | off | Drop all audio |
| `--moviepy` | off | Force the moviepy path instead of ffmpeg |
| `--dry-run` | off | Print the ffmpeg filter graph and exit |

### Requirements

`ffmpeg` must be on your PATH:

```bash
# macOS
brew install ffmpeg

# Ubuntu / Debian
sudo apt install ffmpeg

# Windows
winget install Gyan.FFmpeg
```

`moviepy` and `Pillow` are optional — only needed for the `--moviepy` path, which
is a readable alternative to raw filter graphs but noticeably slower. The default
ffmpeg path is one invocation and much faster.

No third-party Python packages are required for the default path.

---

## How the render works

Each cell becomes an ffmpeg input, normalised into its target rectangle and
overlaid onto a background canvas in cell order — so the JSON's cell order is the
z-order, matching what you arranged in the browser.

- **`cover`** scales to fill the rectangle and crops the overflow. No distortion.
- **`contain`** scales to fit inside and letterboxes the remainder.
- **`fill`** stretches to the exact rectangle, distorting if the aspect differs.

Fade-ins are applied per cell with ffmpeg's `fade` filter on the alpha channel, and
`enable='between(t,start,end)'` gates each overlay to its window on the timeline.
Videos are pulled in with `-stream_loop -1` so a short clip keeps running for the
full duration instead of freezing on its last frame. Images use `-loop 1` to become
still streams of the right length.

---

## Layout format

```json
{
  "format": "collage-studio/v1",
  "stage":    { "width": 1280, "height": 720, "background": "#000000" },
  "timeline": { "duration": 12, "loop": true, "mute": true },
  "defaultFade": 0.45,
  "cells": [
    {
      "id": "c1",
      "rect": [0, 0, 794, 720],
      "fit": "cover",
      "start": 0.0,
      "fade": 0.45,
      "media": { "kind": "video", "file": "hero.mp4",
                 "sourceWidth": 1920, "sourceHeight": 1080 }
    }
  ]
}
```

`rect` is `[x, y, width, height]` in stage pixels. `start` is when the cell fades
in, in seconds. `media.kind` is `"image"` or `"video"`. `sourceWidth`/`sourceHeight`
are recorded for reference; the renderer measures the real files itself.

The format is plain JSON — hand-edit it, generate it from another script, or build
layouts programmatically and skip the browser entirely.

---

## Repo layout

```
index.html                    interactive board (self-contained)
render_collage.py             Python → MP4 renderer
examples/hero-layout.json     a sample layout to render against
README.md
```

## Troubleshooting

**Black frames or missing cells** — the renderer either didn't find the media or
the cell starts after the timeline ends. Check the folder for the filenames in the
JSON; a warning lists anything it couldn't find. `--seconds` extends the render.

**A video freezes partway through** — the clip is shorter than the timeline and
should have been looped. Confirm `kind` is `"video"` in the JSON, not `"image"`.

**Output won't open in some players** — the renderer already writes `yuv420p` with
`+faststart`, which is the broadly compatible combination. If you hand-tweak the
ffmpeg command, keep both flags.

**Fade looks abrupt** — `fade` is in seconds. Raise it per cell in the browser
before exporting, or edit the JSON.
