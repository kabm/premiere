"""Render a project: optional analysis passes, then the final encode."""

import hashlib
import json
import os
import re
import sys

from . import delivery
from .graph import GraphBuilder
from .presets import get_preset
from .util import RushesError, escape_value, fmt_seconds, run, run_with_progress

FFMPEG = ["ffmpeg", "-hide_banner", "-y"]


def log(msg):
    print(msg, file=sys.stderr)


def resolve_output(project, preset, output):
    out = output or project.export.get("output")
    if not out:
        out = (project.title or "export").replace(" ", "_") + preset["ext"]
    out = project.path(out) if not output else os.path.abspath(out)
    if preset["sequence"]:
        # A directory of frames plus a WAV of the mix.
        if os.path.splitext(out)[1]:
            out = os.path.splitext(out)[0]
        return out
    if not os.path.splitext(out)[1]:
        out += preset["ext"]
    return out


def stabilize_pass(project, cache_dir):
    files = {}
    for i, clip in enumerate(project.clips):
        if not clip["effects"].get("stabilize"):
            continue
        if clip.get("generator") or clip["is_image"]:
            raise RushesError(f"clips[{i}]: stabilisation needs a video source")
        key = f"{clip['source']}|{clip['in']}|{clip['src_len']}|{os.path.getmtime(clip['source'])}"
        trf = os.path.join(cache_dir, "stab_" + hashlib.sha1(key.encode()).hexdigest()[:16] + ".trf")
        if not os.path.exists(trf):
            log(f"Analysing motion for clips[{i}] ({os.path.basename(clip['source'])})...")
            cmd = FFMPEG + ["-v", "error"]
            if clip["in"] > 0:
                cmd += ["-ss", fmt_seconds(clip["in"])]
            cmd += ["-t", fmt_seconds(clip["src_len"]), "-i", clip["source"],
                    "-vf", f"vidstabdetect=shakiness=6:accuracy=15:result={escape_value(trf)}", "-f", "null", "-"]
            run(cmd, quiet=True)
        files[i] = trf
    return files


def measure_loudness(project):
    log("Measuring loudness (pass 1 of 2)...")
    gb = GraphBuilder(project, loudnorm_stats="measure")
    inputs, graph = gb.build(video=False, audio=True)
    cmd = FFMPEG + ["-nostats"] + inputs + ["-filter_complex", graph, "-map", "[aout]", "-f", "null", "-"]
    proc = run(cmd, quiet=True)
    blocks = re.findall(r"\{[^{}]*\"input_i\"[^{}]*\}", proc.stderr)
    if not blocks:
        raise RushesError("Could not read loudness measurement from ffmpeg")
    stats = json.loads(blocks[-1])
    if stats.get("input_i") in ("-inf", None) or stats.get("input_i", "").startswith("-inf"):
        return None  # silent timeline; nothing to normalise
    log(f"  integrated {stats['input_i']} LUFS, true peak {stats['input_tp']} dBTP, LRA {stats['input_lra']} LU")
    return stats


def build_command(project, preset_name=None, output=None, draft=False, cache_dir=None, run_analysis=True,
                  draft_format="mp4"):
    preset_name = preset_name or project.export.get("preset") or "prores_422hq"
    preset = get_preset(preset_name)
    out = resolve_output(project, preset, output)
    cache_dir = cache_dir or os.path.join(os.path.dirname(out) or ".", ".rushes_cache")
    os.makedirs(cache_dir, exist_ok=True)

    stab = stabilize_pass(project, cache_dir) if run_analysis else {
        i: os.path.join(cache_dir, f"clip{i}.trf") for i, c in enumerate(project.clips) if c["effects"].get("stabilize")}
    loud = project.master.get("loudness")
    stats = None
    if loud and run_analysis and (loud is True or loud.get("two_pass", True)):
        stats = measure_loudness(project)

    gb = GraphBuilder(project, preset=preset, stab_files=stab, loudnorm_stats=stats, draft=draft,
                      cache_dir=cache_dir)
    want_video = not preset["audio_only"]
    inputs, graph = gb.build(video=want_video, audio=True)

    extra_in, extra_out, sidecars = delivery.output_extras(project, preset, out, cache_dir, len(gb.inputs))
    cmd = FFMPEG + inputs + extra_in
    if len(graph) > 30000:
        script = os.path.join(cache_dir, "graph.txt")
        with open(script, "w") as fh:
            fh.write(graph)
        cmd += ["-filter_complex_script", script]
    else:
        cmd += ["-filter_complex", graph]

    meta = []
    if project.title:
        meta += ["-metadata", f"title={project.title}"]
    color = preset.get("color") or {}
    tags = []
    if color and preset["pix_fmt"] and not preset["pix_fmt"].startswith(("rgb", "gbr")):
        tags = ["-color_primaries", color["primaries"], "-color_trc", color["trc"],
                "-colorspace", color["matrix"], "-color_range", "tv"]
    rate = ["-r", str(project.fps)]

    if preset["audio_only"]:
        cmd += ["-map", "[aout]"] + preset["audio"] + ["-ar", str(project.sample_rate)] + meta + [out]
        outputs = [out]
    elif preset["sequence"]:
        os.makedirs(out, exist_ok=True)
        name = os.path.basename(out)
        pattern = os.path.join(out, f"{name}_%07d{preset['ext']}")
        wav = os.path.join(out, f"{name}.wav")
        cmd += ["-map", "[vout]"] + preset["video"] + rate + ["-start_number", "0", pattern]
        cmd += ["-map", "[aout]"] + preset["audio"] + ["-ar", str(project.sample_rate)] + [wav]
        outputs = [pattern, wav]
    else:
        if draft and draft_format == "webm":
            # VP9/Opus plays in every browser (the Studio's preview player).
            vargs = ["-c:v", "libvpx-vp9", "-deadline", "realtime", "-cpu-used", "8", "-row-mt", "1",
                     "-b:v", "6M", "-pix_fmt", "yuv420p"]
            aargs = ["-c:a", "libopus", "-b:a", "160k"]
            out = os.path.splitext(out)[0] + "_draft.webm"
            tags = []
        elif draft:
            vargs = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p"]
            aargs = ["-c:a", "aac", "-b:a", "192k"]
            out = os.path.splitext(out)[0] + "_draft.mp4"
        else:
            vargs, aargs = preset["video"], preset["audio"]
        ar = "48000" if (draft and draft_format == "webm") else str(project.sample_rate)  # Opus needs 48 kHz
        cmd += ["-map", "[vout]", "-map", "[aout]"] + vargs + rate + tags + aargs + ["-ar", ar]
        if project.start_timecode and out.endswith((".mov", ".mxf")):
            cmd += ["-timecode", project.start_timecode]
        cmd += meta + extra_out + [out]
        outputs = [out] + list(sidecars)
    return cmd, outputs, preset_name


def render(project, preset_name=None, output=None, draft=False, dry_run=False):
    cmd, outputs, preset_name = build_command(project, preset_name, output, draft, run_analysis=not dry_run)
    if dry_run:
        print(" ".join(_quote(c) for c in cmd))
        return outputs
    for o in outputs:
        d = os.path.dirname(o)
        if d:
            os.makedirs(d, exist_ok=True)
    log(f"Rendering {project.duration:.2f}s timeline with preset '{preset_name}'" + (" (draft)" if draft else ""))
    run_with_progress(cmd, project.duration)
    for o in outputs:
        log(f"Wrote {o}")
    return outputs


def _quote(s):
    if re.fullmatch(r"[\w@%+=:,./-]+", s):
        return s
    return "'" + s.replace("'", "'\\''") + "'"
