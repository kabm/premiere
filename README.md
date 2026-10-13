# rushes

Colour-managed editing, LUT grading and high-end mastering for camera footage, built on FFmpeg.

`rushes` covers the core of a Premiere Pro workflow from the command line: you cut a sequence,
grade it with LUTs and Lumetri-style controls, add titles, overlays, music and transitions, then
export to ProRes 4444 XQ, DNxHR 444, 16-bit lossless, EXR/DPX/TIFF sequences or delivery codecs.
It is original open-source code. It is not Adobe software and is not affiliated with Adobe.

## Image quality

* Every clip is decoded and conformed straight into **16-bit 4:4:4**, and grading runs in **16-bit RGB**.
  Nothing is quantised to 8 bits unless you ask for an 8-bit output.
* LUTs use **tetrahedral interpolation** (3D) or cubic interpolation (1D).
* Scaling uses Lanczos with accurate rounding and full chroma interpolation.
* Rec.709 or Rec.2020 matrices are handled explicitly, and outputs carry correct colour tags.
* Loudness is normalised to EBU R128 / streaming targets with a **two-pass**, linear `loudnorm`.
* Outputs are written once from the master timeline, so there is no generation loss between steps.

Known precision limits: overlays are composited at 10-bit, and motion stabilisation and
frame-blending/optical-flow retiming run at 8-bit. Clips that use none of these stay at 16 bits end to end.

## Requirements

* Python 3.8+ (standard library only)
* FFmpeg 6.0+ with `libx264`, `libx265`, `libsvtav1`, `libvidstab` (for stabilisation) and `libfreetype` (for titles).
  Most distro and Homebrew builds include all of them. Run `ffmpeg -encoders` to check.

```bash
pip install .          # gives you the `rushes` command
# or run in place:
python -m rushes --help
```

## Camera RAW and log footage

Log footage works directly when it's recorded to a codec FFmpeg can read: S-Log2/3, V-Log, C-Log,
LogC, F-Log, N-Log, BMD Film and so on, in ProRes, DNxHR, XAVC/H.264, H.265, CineForm or MXF.
Put the manufacturer's Log to Rec.709 LUT in `input_lut` and your look in `lut`.

FFmpeg **cannot decode** proprietary RAW codecs: Blackmagic RAW (`.braw`), REDCODE (`.r3d`),
ARRIRAW, Canon Cinema RAW Light (`.crm`) or ProRes RAW. First convert those to ProRes 4444 XQ or a
16-bit DPX/EXR sequence in log, using the camera maker's free tool (Blackmagic RAW Player /
DaVinci Resolve, REDCINE-X PRO, ARRI Reference Tool, Canon RAW Development). Then grade in `rushes`.
At that point the RAW debayer is done and you still have the full log latitude.

## Quick start

```bash
rushes probe A001_C001.mov                     # codec, bit depth, colour tags, timecode
rushes lut SLog3_to_709.cube MyLook.cube       # validate LUTs

# Grade one clip and export ProRes 4444 XQ
rushes grade A001_C001.mov --input-lut SLog3_to_709.cube --lut MyLook.cube --lut-intensity 80 \
    --exposure 0.3 --contrast 10 --temperature 5600 --preset prores_4444xq

# Look at a frame before committing: graded 16-bit PNG, before/after, scopes
rushes still  A001_C001.mov -t 00:00:04 --input-lut SLog3_to_709.cube --lut MyLook.cube --compare
rushes scopes A001_C001.mov -t 00:00:04 --input-lut SLog3_to_709.cube --lut MyLook.cube

# Same grade on a whole card
rushes batch /Volumes/CARD/CLIPS -o graded --input-lut SLog3_to_709.cube --preset prores_422hq

# Editing proxies with a viewing LUT baked in
rushes proxy footage/*.mov --input-lut SLog3_to_709.cube --height 720

# Full edit from a project file
rushes init project.json           # writes a commented starting point
rushes render project.json --draft # fast half-res H.264 preview
rushes render project.json         # the real master
rushes render project.json --preset hevc_10bit -o deliver/web.mp4
```

## Export presets (`rushes presets`)

| Category | Presets |
|---|---|
| Mastering | `prores_4444xq`, `prores_4444`, `prores_422hq`, `dnxhr_444`, `dnxhr_hqx` |
| Intermediate | `prores_422`, `prores_lt`, `dnxhr_hq`, `cineform_film` |
| Archive | `ffv1_lossless` (16-bit 4:4:4, lossless copy of the timeline) |
| Delivery | `h264_master`, `h264_web`, `hevc_10bit`, `hevc_hdr10`, `av1_10bit` |
| Image sequence | `tiff_16bit`, `exr_half`, `dpx_10bit`, `png_16bit` (each with a 24-bit WAV) |
| Proxy | `prores_proxy`, `dnxhr_lb` |
| Audio | `wav_24bit`, `flac` |

For the best master, use `prores_4444xq` or `ffv1_lossless`. Make delivery files from the same project with `--preset`.

## Project files

A project is JSON. Times can be seconds (`2.5`), `"MM:SS.ms"`, `"HH:MM:SS.ms"` or SMPTE timecode `"HH:MM:SS:FF"`.
Relative paths are resolved from the project file's folder.

```json
{
  "settings": {"title": "My Edit", "width": 3840, "height": 2160, "fps": "24000/1001",
               "color_matrix": "bt709", "sample_rate": 48000, "audio_channels": 2,
               "start_timecode": "01:00:00:00"},
  "clips":    [ ... V1, played back to back ... ],
  "overlays": [ ... V2+, picture-in-picture / logos / keyed footage ... ],
  "titles":   [ ... text ... ],
  "audio":    [ ... music / VO / SFX tracks ... ],
  "master":   { ... adjustment layer + master bus ... },
  "export":   {"preset": "prores_4444xq", "output": "renders/my_edit.mov"}
}
```

### Clips (main track)

| Key | Meaning |
|---|---|
| `source` / `generator` | Media file (video or still), or `"black"`, `"white"`, `"bars"`, `"testsrc"`, `"#RRGGBB"` |
| `in`, `out`, `duration` | Source in/out. `duration` is timeline length (stills and generators need it) |
| `speed`, `reverse` | Speed/duration (0.05–20×). Audio pitch is preserved |
| `interpolation` | `frame_sampling` (default), `frame_blending`, `optical_flow` |
| `fit` | `fit` (letter/pillarbox), `fill` (crop to fill), `stretch` |
| `crop` | `{"left": %, "right": %, "top": %, "bottom": %}` of the source |
| `zoom`, `position`, `rotation`, `flip` | Motion: scale %, `[x, y]` px offset, degrees, `"h"`/`"v"`/`"hv"` |
| `color` | Lumetri-style grade, see below |
| `effects` | `denoise` 0–100, `sharpen` 0–100, `blur` (sigma), `stabilize` (`true` or smoothing frames) |
| `transition` | Into this clip: `{"type": "cross_dissolve", "duration": 1}`. Types: `cross_dissolve`, `film_dissolve`, `dip_to_black`, `dip_to_white`, `additive_dissolve`, plus all FFmpeg `xfade` types (`wipeleft`, `slideup`, `circleopen`, `zoomin`, `radial`, ...) |
| `fade_in`, `fade_out` | Fade from/to black at the clip's edges |
| `audio` | `{"volume_db": -3, "mute": false, "fade_in": 0.2, "fade_out": 0.5}` |

### Colour (`color`, on clips, overlays and `master`)

The order matches the Lumetri panel:

1. `input_lut`: technical LUT (Log to Rec.709, camera to display)
2. `temperature` (Kelvin, 6500 = neutral), `tint` (−100..100)
3. `exposure` (stops), then `contrast`, `highlights`, `shadows`, `whites`, `blacks` (−100..100)
4. `saturation` (0..300, 100 = unchanged), `vibrance` (−100..100)
5. `lut` + `lut_intensity` (0–100): creative look
6. `shadow_tint`, `midtone_tint`, `highlight_tint`: colour wheels as `[r, g, b]` offsets (−1..1)
7. `curves`: `{"master": "0/0 0.5/0.55 1/1", "red": "...", "green": "...", "blue": "..."}`
8. `output_lut`: last LUT in the chain (e.g. a show LUT or a Rec.709 to Rec.2020 PQ transform)

Supported LUT formats: `.cube` (1D and 3D, validated on load), `.3dl`, `.dat`, `.m3d`, `.csp`.

### Overlays, titles, audio, master

```json
"overlays": [{"source": "logo.png", "start": 1, "duration": 4, "width": 0.15, "x": "right", "y": "top",
              "opacity": 0.85, "fade_in": 0.5, "fade_out": 0.5,
              "chroma_key": {"color": "#00FF00", "similarity": 0.12, "blend": 0.04}}],
"titles":   [{"text": "Chapter One", "start": 1.5, "duration": 3, "position": "lower_third",
              "size": 96, "color": "white", "font": "fonts/MyFont.otf", "box": true, "fade": 0.5}],
"audio":    [{"source": "score.wav", "start": 0, "in": 12, "volume_db": -6,
              "fade_in": 2, "fade_out": 3, "duck": true, "loop": false}],
"master":   {"color": {"lut": "show_look.cube", "lut_intensity": 50},
             "fade_in": 0.5, "fade_out": 1.5, "volume_db": 0,
             "loudness": {"target": -14, "true_peak": -1, "lra": 11, "two_pass": true}}
```

* Overlay `x`/`y` accept `center`, `left`/`right`, `top`/`bottom`, pixels or FFmpeg expressions (`W`, `H`, `w`, `h`).
  `width` is a fraction of the frame width (≤1) or pixels.
* Title `position`: `center`, `lower_third`, `top`, `bottom`, or set `x`/`y` expressions (`w`, `h`, `text_w`, `text_h`).
* `duck: true` lowers that track under the dialogue (sidechain compression). A number sets the ratio.
* Common loudness targets: −14 LUFS (YouTube/Spotify), −16 (Apple), −23 (EBU R128 broadcast), −24 (ATSC A/85).

## What it doesn't do

There's no GUI timeline, keyframed motion, multicam, Essential Graphics templates, or
speech-to-text captions. Stabilisation uses vid.stab, which is good but isn't Warp Stabilizer.
Preview with `--draft`, `still --compare` and `scopes`, then render the master.

## Tests

```bash
python -m unittest -v
```

The tests generate synthetic camera clips and LUTs, then render every preset family. They check
codecs, bit depth, colour tags, timecode, durations, LUT accuracy (an identity LUT stays transparent),
and loudness hitting its target.
