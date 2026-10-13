"""Frame previews for the Studio: the program frame at a timeline time, before/after, scopes."""

import base64
import copy
import os
import tempfile

from ..graph import GraphBuilder
from ..project import Project
from ..render import FFMPEG, stabilize_pass
from ..util import RushesError, fmt_seconds, run

SCOPES_GRAPH = (
    "[0]scale=960:540:force_original_aspect_ratio=decrease,pad=960:540:(ow-iw)/2:(oh-ih)/2,"
    "format=yuv444p,split=3[w][p][v];"
    "[w]waveform=mode=column:intensity=0.12:components=1:display=overlay:graticule=green:"
    "flags=numbers+dots:scale=ire,scale=960:540,format=gbrp[wf];"
    "[p]format=gbrp,waveform=mode=column:intensity=0.2:components=7:display=parade:graticule=green:"
    "flags=numbers,scale=960:540,format=gbrp[par];"
    "[v]vectorscope=mode=color3:graticule=color:flags=name,scale=540:540,"
    "pad=960:540:(ow-iw)/2:0,format=gbrp[vs];"
    "[wf][par][vs]hstack=inputs=3"
)


def clip_at(project, t):
    """Index of the main-track clip under timeline time t (the incoming clip during a transition)."""
    idx = 0
    for i, clip in enumerate(project.clips):
        if clip["timeline_start"] <= t + 1e-9:
            idx = i
    return idx


def _shift_items(items, offset, window):
    """Move timed items (overlays, titles, adjustment layers) by -offset; drop those outside the window."""
    out = []
    for item in items or []:
        item = copy.deepcopy(item)
        start = float(item.get("start", 0)) - offset
        dur = item.get("duration")
        if dur is not None:
            dur = float(dur)
            if start + dur <= 0 or start >= window:
                continue
            if start < 0:
                if "in" in item or item.get("source"):
                    item["in"] = float(item.get("in", 0) or 0) + (-start)
                dur += start
                start = 0.0
            item["duration"] = dur
        elif start < 0:
            start = 0.0
        item["start"] = start
        out.append(item)
    return out


def single_clip_data(data, project, idx, plain=False):
    """A project holding only clip idx (plus everything layered over it), re-timed to start at 0."""
    clip = copy.deepcopy(data["clips"][idx])
    clip.pop("transition", None)
    if plain:
        for key in ("color", "effects", "keyframes"):
            clip.pop(key, None)
    start = project.clips[idx]["timeline_start"]
    window = project.clips[idx]["duration"]
    out = {k: copy.deepcopy(v) for k, v in data.items()
           if k not in ("clips", "overlays", "titles", "audio", "adjustment_layers", "master", "export")}
    out["clips"] = [clip]
    if not plain:
        out["overlays"] = _shift_items(data.get("overlays"), start, window)
        out["titles"] = _shift_items(data.get("titles"), start, window)
        out["adjustment_layers"] = _shift_items(data.get("adjustment_layers"), start, window)
        master = data.get("master") or {}
        if master.get("color"):
            out["master"] = {"color": master["color"]}
        caps = data.get("captions")
        if isinstance(caps, dict):
            caps = dict(caps)
            caps["offset"] = float(caps.get("offset", 0) or 0) - start
            out["captions"] = caps
    return out, start


def _render_frame(data, base_dir, local_t, out_png, width, cache_dir):
    project = Project(data, base_dir)
    stab = stabilize_pass(project, cache_dir) if any(c["effects"].get("stabilize") for c in project.clips) else {}
    gb = GraphBuilder(project, preset={"pix_fmt": "rgb48le", "color": {}}, stab_files=stab, cache_dir=cache_dir)
    inputs, graph = gb.build(video=True, audio=False)
    graph += f";\n[vout]scale={int(width)}:-2:flags=lanczos,format=rgb24[pv]"
    local_t = min(max(0.0, local_t), max(0.0, project.duration - 1.0 / float(project.fps)))
    run(FFMPEG + ["-v", "error"] + inputs + ["-filter_complex", graph, "-map", "[pv]",
                                            "-ss", fmt_seconds(local_t), "-frames:v", "1", "-update", "1",
                                            "-c:v", "png", out_png], quiet=True)
    if not os.path.exists(out_png):
        raise RushesError("No frame was produced at this time")


def _data_uri(path):
    with open(path, "rb") as fh:
        return "data:image/png;base64," + base64.b64encode(fh.read()).decode("ascii")


def program_frame(data, base_dir, t, compare=False, scopes=False, width=960, cache_dir=None):
    """Render the program frame at timeline time t. Returns a dict of data: URIs."""
    project = Project(data, base_dir)
    cache_dir = cache_dir or project.cache_dir
    os.makedirs(cache_dir, exist_ok=True)
    t = min(max(0.0, float(t)), project.duration)
    idx = clip_at(project, t)
    result = {"clip_index": idx}
    with tempfile.TemporaryDirectory() as tmp:
        single, start = single_clip_data(data, project, idx)
        frame = os.path.join(tmp, "frame.png")
        _render_frame(single, base_dir, t - start, frame, width, cache_dir)
        if compare:
            before_data, _ = single_clip_data(data, project, idx, plain=True)
            before = os.path.join(tmp, "before.png")
            _render_frame(before_data, base_dir, t - start, before, width, cache_dir)
            both = os.path.join(tmp, "compare.png")
            run(FFMPEG + ["-v", "error", "-i", before, "-i", frame, "-filter_complex",
                          "[0][1]hstack=inputs=2", "-frames:v", "1", "-update", "1", both], quiet=True)
            result["image"] = _data_uri(both)
        else:
            result["image"] = _data_uri(frame)
        if scopes:
            sc = os.path.join(tmp, "scopes.png")
            run(FFMPEG + ["-v", "error", "-i", frame, "-filter_complex", SCOPES_GRAPH, "-frames:v", "1",
                          "-update", "1", sc], quiet=True)
            result["scopes"] = _data_uri(sc)
    return result
