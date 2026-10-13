"""Command-line entry point: `python -m rushes <command>` or `rushes <command>`."""

import argparse
import json
import os
import sys
import tempfile

from . import __version__, audiofx, captions, colormgmt, interchange, looks, timeline
from .graph import GraphBuilder
from .lut import Lut
from .presets import get_preset, list_presets
from .probe import probe
from .project import Project
from .render import FFMPEG, render
from .util import RushesError, fmt_seconds, parse_time, require_ffmpeg, run

VIDEO_EXTS = {".mov", ".mp4", ".mxf", ".mkv", ".avi", ".m4v", ".mts", ".m2ts",
              ".mpg", ".webm"}

EXAMPLE_PROJECT = {
    "settings": {"title": "My Edit", "width": 3840, "height": 2160, "fps": "24000/1001",
                 "start_timecode": "01:00:00:00"},
    "clips": [
        {"source": "footage/A001_C001.mov", "in": "00:00:02.000", "out": "00:00:08.000",
         "color": {"input_lut": "luts/SLog3_to_Rec709.cube", "exposure": 0.3, "contrast": 15,
                   "temperature": 5800, "saturation": 110, "lut": "luts/Film_Look.cube", "lut_intensity": 70},
         "effects": {"denoise": 20, "sharpen": 15},
         "fade_in": 1.0},
        {"source": "footage/A001_C002.mov", "in": 1.5, "duration": 5, "speed": 0.5,
         "interpolation": "frame_blending",
         "transition": {"type": "cross_dissolve", "duration": 1.0},
         "color": {"input_lut": "luts/SLog3_to_Rec709.cube", "lut": "luts/Film_Look.cube"},
         "effects": {"stabilize": 20}},
        {"source": "footage/A002_C004.mov", "in": 10, "out": 16, "zoom": 115, "position": [0, -40],
         "transition": {"type": "dip_to_black", "duration": 0.75},
         "color": {"input_lut": "luts/SLog3_to_Rec709.cube", "shadow_tint": [0, 0.02, 0.05],
                   "highlight_tint": [0.04, 0.02, 0]},
         "audio": {"volume_db": -3}},
    ],
    "overlays": [
        {"source": "graphics/logo.png", "start": 1, "duration": 4, "width": 0.15, "x": "right", "y": "top",
         "opacity": 0.85, "fade_in": 0.5, "fade_out": 0.5}
    ],
    "titles": [
        {"text": "Chapter One", "start": 1.5, "duration": 3, "position": "lower_third", "box": True}
    ],
    "audio": [
        {"source": "music/score.wav", "start": 0, "volume_db": -6, "fade_in": 2, "fade_out": 3, "duck": True}
    ],
    "master": {"loudness": {"target": -14, "true_peak": -1}, "fade_out": 1.5},
    "export": {"preset": "prores_4444xq", "output": "renders/my_edit.mov"},
}


def add_grade_args(p):
    g = p.add_argument_group("colour")
    g.add_argument("--input-lut", help="technical LUT applied first (e.g. Log -> Rec.709)")
    g.add_argument("--lut", help="creative LUT (.cube/.3dl/.dat/.m3d/.csp)")
    g.add_argument("--lut-intensity", type=float, help="creative LUT strength 0-100")
    g.add_argument("--output-lut", help="LUT applied last (e.g. display/monitor transform)")
    for name, helptext in (("exposure", "stops, -6..6"), ("contrast", "-100..100"), ("highlights", "-100..100"),
                           ("shadows", "-100..100"), ("whites", "-100..100"), ("blacks", "-100..100"),
                           ("temperature", "Kelvin, 6500 = neutral"), ("tint", "-100..100 (+ = magenta)"),
                           ("saturation", "0..300, 100 = unchanged"), ("vibrance", "-100..100")):
        g.add_argument(f"--{name}", type=float, help=helptext)
    e = p.add_argument_group("effects")
    e.add_argument("--denoise", type=float, help="temporal/spatial denoise strength 0-100")
    e.add_argument("--sharpen", type=float, help="sharpen amount 0-100")
    e.add_argument("--stabilize", type=int, nargs="?", const=15, help="warp stabiliser, optional smoothing (frames)")
    t = p.add_argument_group("timing / format")
    t.add_argument("--in", dest="in_point", help="source in point (seconds, HH:MM:SS.ms or timecode)")
    t.add_argument("--out", dest="out_point", help="source out point")
    t.add_argument("--speed", type=float, default=1.0)
    t.add_argument("--width", type=int, help="output width (default: source)")
    t.add_argument("--height", type=int, help="output height (default: source)")
    t.add_argument("--fps", help="output frame rate (default: source)")
    t.add_argument("--fit", choices=("fit", "fill", "stretch"), default="fit")
    t.add_argument("--loudness", type=float, help="normalise to this integrated loudness (LUFS), e.g. -14 or -23")


def grade_settings(args):
    color = {}
    for key in ("input_lut", "lut", "lut_intensity", "output_lut", "exposure", "contrast", "highlights",
                "shadows", "whites", "blacks", "temperature", "tint", "saturation", "vibrance"):
        val = getattr(args, key, None)
        if val is not None:
            color[key] = os.path.abspath(val) if key.endswith("lut") else val
    effects = {}
    for key in ("denoise", "sharpen", "stabilize"):
        val = getattr(args, key, None)
        if val is not None:
            effects[key] = val
    return color, effects


def single_clip_project(source, args, extra_clip=None):
    info = probe(source)
    if info.video is None:
        raise RushesError(f"{source} has no video stream")
    color, effects = grade_settings(args)
    w = args.width or info.width
    h = args.height or info.height
    if args.width and not args.height:
        h = int(round(info.height * args.width / info.width / 2)) * 2
    elif args.height and not args.width:
        w = int(round(info.width * args.height / info.height / 2)) * 2
    clip = {"source": os.path.abspath(source), "color": color, "effects": effects,
            "speed": args.speed, "fit": args.fit}
    if args.in_point is not None:
        clip["in"] = args.in_point
    if args.out_point is not None:
        clip["out"] = args.out_point
    if info.is_image:
        clip["duration"] = 5
    clip.update(extra_clip or {})
    fps = args.fps or (str(info.fps) if info.fps else "24")
    tc = None
    if info.video:
        tc = (info.video.get("tags") or {}).get("timecode") or (info.data.get("format", {}).get("tags") or {}).get("timecode")
    data = {"settings": {"width": w - w % 2, "height": h - h % 2, "fps": fps, "start_timecode": tc},
            "clips": [clip]}
    if getattr(args, "loudness", None) is not None:
        data["master"] = {"loudness": {"target": args.loudness}}
    return Project(data, os.getcwd())


# -- commands -----------------------------------------------------------------

def cmd_probe(args):
    for f in args.files:
        print(probe(f).summary())
        print()


def cmd_presets(args):
    print(list_presets())


def cmd_lut(args):
    for f in args.files:
        print(Lut(f).describe())
        print()


def cmd_init(args):
    if os.path.exists(args.path) and not args.force:
        raise RushesError(f"{args.path} already exists (use --force to overwrite)")
    with open(args.path, "w") as fh:
        json.dump(EXAMPLE_PROJECT, fh, indent=2)
        fh.write("\n")
    print(f"Wrote example project to {args.path}. Edit the paths, then: rushes render {args.path}")


def cmd_render(args):
    project = Project.load(args.project)
    render(project, args.preset, args.output, draft=args.draft, dry_run=args.dry_run)


def default_out(source, preset_name, suffix="_graded"):
    preset = get_preset(preset_name)
    base = os.path.splitext(os.path.basename(source))[0] + suffix
    return base + ("" if preset["sequence"] else preset["ext"])


def cmd_grade(args):
    project = single_clip_project(args.input, args)
    out = args.output or default_out(args.input, args.preset)
    render(project, args.preset, out, draft=args.draft, dry_run=args.dry_run)


def cmd_batch(args):
    files = sorted(f for f in os.listdir(args.folder)
                   if os.path.splitext(f)[1].lower() in VIDEO_EXTS and not f.startswith("."))
    if not files:
        raise RushesError(f"No video files found in {args.folder}")
    os.makedirs(args.output_dir, exist_ok=True)
    failures = []
    for n, name in enumerate(files, 1):
        src = os.path.join(args.folder, name)
        print(f"[{n}/{len(files)}] {name}", file=sys.stderr)
        try:
            project = single_clip_project(src, args)
            out = os.path.join(args.output_dir, default_out(src, args.preset, args.suffix))
            render(project, args.preset, out, dry_run=args.dry_run)
        except RushesError as exc:
            failures.append(name)
            print(f"  failed: {exc}", file=sys.stderr)
    if failures:
        raise RushesError(f"{len(failures)} of {len(files)} files failed: {', '.join(failures)}")


def cmd_proxy(args):
    os.makedirs(args.output_dir, exist_ok=True)
    for src in args.files:
        args.width, args.fps, args.fit = None, None, "fit"
        project = single_clip_project(src, args)
        out = os.path.join(args.output_dir, default_out(src, args.preset, "_proxy"))
        render(project, args.preset, out, dry_run=args.dry_run)


def _render_still(project, out, pix="rgb48be"):
    preset = {"pix_fmt": pix, "color": {}}
    gb = GraphBuilder(project, preset=preset)
    inputs, graph = gb.build(video=True, audio=False)
    run(FFMPEG + ["-v", "error"] + inputs + ["-filter_complex", graph, "-map", "[vout]",
                                            "-frames:v", "1", "-update", "1", out], quiet=True)


def _still_project(args):
    at = parse_time(args.time) or 0.0
    info = probe(args.input)
    one_frame = 1.0 / float(info.fps or 24)
    return single_clip_project(args.input, args, {"in": at, "out": None, "duration": one_frame * 2})


def cmd_still(args):
    for attr in ("in_point", "out_point"):
        setattr(args, attr, None)
    project = _still_project(args)
    out = args.output or os.path.splitext(os.path.basename(args.input))[0] + "_still.png"
    if not args.compare:
        _render_still(project, out)
    else:
        with tempfile.TemporaryDirectory() as tmp:
            graded = os.path.join(tmp, "graded.png")
            _render_still(project, graded)
            project.clips[0]["color"] = {}
            project.clips[0]["effects"] = {}
            raw = os.path.join(tmp, "raw.png")
            _render_still(project, raw)
            run(FFMPEG + ["-v", "error", "-i", raw, "-i", graded, "-filter_complex",
                          "[0][1]hstack=inputs=2", "-frames:v", "1", "-update", "1", out], quiet=True)
    print(f"Wrote {out}")


def cmd_scopes(args):
    for attr in ("in_point", "out_point"):
        setattr(args, attr, None)
    project = _still_project(args)
    out = args.output or os.path.splitext(os.path.basename(args.input))[0] + "_scopes.png"
    with tempfile.TemporaryDirectory() as tmp:
        frame = os.path.join(tmp, "frame.png")
        _render_still(project, frame)
        graph = ("[0]scale=960:540:force_original_aspect_ratio=decrease,pad=960:540:(ow-iw)/2:(oh-ih)/2,"
                 "format=gbrp,split=4[img][w][p][v];"
                 "[w]waveform=mode=column:intensity=0.1:graticule=green:flags=numbers+dots:display=overlay,"
                 "scale=960:540,format=gbrp[wf];"
                 "[p]waveform=mode=column:intensity=0.1:graticule=green:flags=numbers:display=parade:"
                 "components=7,scale=960:540,format=gbrp[par];"
                 "[v]format=yuv444p,vectorscope=mode=color3:graticule=color:flags=name,scale=540:540,"
                 "pad=960:540:(ow-iw)/2:0,format=gbrp[vs];"
                 "[img][wf][par][vs]xstack=inputs=4:layout=0_0|w0_0|0_h0|w0_h0")
        run(FFMPEG + ["-v", "error", "-i", frame, "-filter_complex", graph, "-frames:v", "1",
                      "-update", "1", out], quiet=True)
    print(f"Wrote {out}  (top-left: frame, top-right: luma waveform, bottom-left: RGB parade, "
          f"bottom-right: vectorscope)")


def build_parser():
    p = argparse.ArgumentParser(prog="rushes", description="Colour-managed editing and mastering for camera footage.")
    p.add_argument("--version", action="version", version=f"rushes {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("probe", help="show technical info for media files")
    s.add_argument("files", nargs="+")
    s.set_defaults(func=cmd_probe)

    s = sub.add_parser("presets", help="list export presets")
    s.set_defaults(func=cmd_presets)

    s = sub.add_parser("lut", help="validate and describe LUT files")
    s.add_argument("files", nargs="+")
    s.set_defaults(func=cmd_lut)

    s = sub.add_parser("init", help="write an example project file")
    s.add_argument("path", nargs="?", default="project.json")
    s.add_argument("--force", action="store_true")
    s.set_defaults(func=cmd_init)

    s = sub.add_parser("render", help="render a project file")
    s.add_argument("project")
    s.add_argument("--preset", help="override export preset")
    s.add_argument("-o", "--output", help="override output path")
    s.add_argument("--draft", action="store_true", help="half resolution H.264 preview")
    s.add_argument("--dry-run", action="store_true", help="print the ffmpeg command instead of running it")
    s.set_defaults(func=cmd_render)

    s = sub.add_parser("grade", help="grade and export a single clip")
    s.add_argument("input")
    s.add_argument("-o", "--output")
    s.add_argument("--preset", default="prores_422hq")
    s.add_argument("--draft", action="store_true")
    s.add_argument("--dry-run", action="store_true")
    add_grade_args(s)
    s.set_defaults(func=cmd_grade)

    s = sub.add_parser("batch", help="apply the same grade/export to every clip in a folder")
    s.add_argument("folder")
    s.add_argument("-o", "--output-dir", default="graded")
    s.add_argument("--preset", default="prores_422hq")
    s.add_argument("--suffix", default="_graded")
    s.add_argument("--dry-run", action="store_true")
    add_grade_args(s)
    s.set_defaults(func=cmd_batch)

    s = sub.add_parser("proxy", help="make lightweight editing proxies")
    s.add_argument("files", nargs="+")
    s.add_argument("-o", "--output-dir", default="proxies")
    s.add_argument("--preset", default="prores_proxy")
    s.add_argument("--height", type=int, default=720)
    s.add_argument("--input-lut", help="bake a viewing LUT into the proxies")
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(func=cmd_proxy, in_point=None, out_point=None, speed=1.0, loudness=None)

    for name, func, helptext in (("still", cmd_still, "export a graded 16-bit PNG frame"),
                                 ("scopes", cmd_scopes, "waveform / parade / vectorscope of a graded frame")):
        s = sub.add_parser(name, help=helptext)
        s.add_argument("input")
        s.add_argument("-t", "--time", default="0", help="frame time in the source")
        s.add_argument("-o", "--output")
        if name == "still":
            s.add_argument("--compare", action="store_true", help="side-by-side before/after")
        add_grade_args(s)
        s.set_defaults(func=func)

    # Feature modules contribute their own commands.
    for module in (colormgmt, looks, audiofx, timeline, captions, interchange):
        module.register(sub)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        require_ffmpeg()
        args.func(args)
    except RushesError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0
