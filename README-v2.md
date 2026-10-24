
# Collage Studio v2 — extended renderer (UNVERIFIED)

> **Read this before using.** This branch carries the extended renderer. Its
> Python parses cleanly and its layout model is exercised, but the **generated
> ffmpeg filter expressions have not been executed even once** — there was no
> Python or ffmpeg runtime available when it was written. Treat it as a strong
> draft to test, not as code you can rely on. `main` still holds the working v1.
>
> The first thing to do is run the smoke test at the bottom of this file.

## What v2 adds over `main`

### Layout model
- **`transition`** — `fade` (default), `none`/`cut`, `slide-left|right|up|down`,
  `wipe-left|right|up|down`, `zoom-in`, `zoom-out`, `pop`, `flip-h`, `flip-v`
- **`transitionSeconds`** — per-cell transition length, independent of `fade`
- **`opacity`** 0–1 · **`rotation`** degrees · **`radius`** corner rounding (px)
- **`blend`** — `normal`, `multiply`, `screen`, `overlay`, `darken`, `lighten`,
  `color-dodge`, `color-burn`, `hard-light`, `soft-light`, `difference`,
  `exclusion`, `addition`
- **`z`** — explicit draw order; JSON order is the tiebreak
- **`trim`** — `[in, out]` seconds for video clips
- **`cropOffset`** — `[x, y]` in 0–1, choosing which part of a `cover` crop survives
- **`kenBurns`** — `{ from, to, pan: [x, y] }` slow drift across a still
- **`motion`** — keyframe tracks, e.g. `{ "x": [{"t":0,"v":0},{"t":2,"v":400}] }`

### Tooling
- `validate()` — flags mismatched media kinds, unknown transitions/blends,
  trims past the end of a clip; notes print before rendering
- `--dry-run` — print the filter graph
- `--show-command` — print the full ffmpeg invocation
- `--contact-sheet` — tile six frames across the timeline into one PNG
- `--quiet` — suppress ffmpeg progress

v1 layouts load unchanged. Every v2 field is optional.

## Smoke test — run this first

```bash
mkdir -p /tmp/cs && cd /tmp/cs

# a still and a short clip
ffmpeg -f lavfi -i testsrc=size=1280x720:rate=30:duration=4 -frames:v 1 still.png
ffmpeg -f lavfi -i testsrc2=size=640x360:rate=30:duration=3 -pix_fmt yuv420p clip.mp4
```

Then a layout exercising the risky paths:

```bash
cat > layout.json <<'JSON'
{
  "format": "collage-studio/v2",
  "stage": { "width": 1280, "height": 720, "background": "#000000" },
  "timeline": { "duration": 6, "loop": true, "mute": true },
  "cells": [
    { "id": "a", "rect": [0,0,853,720], "fit": "cover", "start": 0, "fade": 0.5,
      "transition": "ken-burns-placeholder", "kenBurns": { "from": 1.0, "to": 1.2 },
      "radius": 24, "media": { "kind": "image", "file": "still.png" } },
    { "id": "b", "rect": [853,0,427,360], "fit": "cover", "start": 0.8, "fade": 0.6,
      "transition": "slide-left",
      "media": { "kind": "video", "file": "clip.mp4" } },
    { "id": "c", "rect": [853,360,427,360], "fit": "cover", "start": 1.6, "fade": 0.6,
      "transition": "fade", "opacity": 0.85, "blend": "screen",
      "motion": { "x": [{"t":0,"v":853},{"t":3,"v":700}] },
      "media": { "kind": "image", "file": "still.png" } }
  ]
}
JSON
```

```bash
# 1. does the graph at least build?
python render_collage_v2.py layout.json --dry-run

# 2. does ffmpeg accept it?  <- the real test
python render_collage_v2.py layout.json -o out.mp4 --quiet

# 3. is the motion where it should be?
python render_collage_v2.py layout.json -o out.mp4 --contact-sheet
```

If step 2 fails, the error will name the filter that broke. The usual suspects:

| Symptom | Likely cause |
|---|---|
| `No such filter: ''` | a filter chain joined with a stray comma — check `_full_cell_chain` step 2, which appends an empty string when no content scaling applies |
| `Invalid argument` on `overlay` | the comma escaping in `_overlay_xy` — expressions containing commas must be quoted, which `_esc` handles, but nested `if()` may need the expression wrapped in `'…'` instead of escaped |
| `geq` errors | the corner-radius expression in `_full_cell_chain` step 3 is the most fragile part of the file |
| `blend` mode rejected | some modes need ffmpeg's `all_mode` set to the lower-case form; check `OPACITY_BLEND_MODES` against your ffmpeg's `help blend` |
| Keyframes land in the wrong place | `keyframe_expr` builds `if(lt(t,…))` nesting in reverse; verify the first threshold is the *earliest* keyframe, not the latest |

## Known rough edges, stated plainly

1. **Rotation snaps to 90°.** Arbitrary angles would need `rotate` plus a
   bounding-box recompute and edge handling. Only multiples of 90 are exact;
   other values round to the nearest. This is a real limitation, not a bug.
2. **Blend modes composite the whole cell, not just media.** An affected cell's
   background rectangle takes part in the blend.
3. **`wipe-*` transitions are declared and parsed but not yet implemented** in
   the filter graph — they currently behave as a plain fade. The parser accepts
   them so layouts stay valid while the filter work lands.
4. **moviepy path does not implement transitions, blends, rotation or radius.**
   It handles position, fade, opacity, Ken Burns and motion only. Use the
   ffmpeg path for anything beyond that.
5. **Trim uses input-side `-ss`/`-t` with `-stream_loop -1`.** Loop-plus-seek
   interaction is one of the things most likely to surprise you; verify trim
   behaviour on a short clip specifically.
