"""Project loading and validation.

A project is a JSON file describing a sequence: settings, the main video track
(V1, edited back to back with optional transitions), overlay tracks, titles,
extra audio tracks, a master bus and the export target. See README.md.
"""

import json
import os

from .probe import probe
from .util import RushesError, parse_rate, parse_time

TRANSITIONS = {
    "cross_dissolve": "fade", "film_dissolve": "fade", "dip_to_black": "fadeblack",
    "dip_to_white": "fadewhite", "additive_dissolve": "fadefast",
}
XFADE_TYPES = {
    "fade", "fadeblack", "fadewhite", "fadegrays", "fadefast", "fadeslow", "dissolve", "distance",
    "wipeleft", "wiperight", "wipeup", "wipedown", "wipetl", "wipetr", "wipebl", "wipebr",
    "slideleft", "slideright", "slideup", "slidedown", "smoothleft", "smoothright", "smoothup",
    "smoothdown", "circlecrop", "rectcrop", "circleclose", "circleopen", "horzclose", "horzopen",
    "vertclose", "vertopen", "diagbl", "diagbr", "diagtl", "diagtr", "hlslice", "hrslice",
    "vuslice", "vdslice", "pixelize", "radial", "hblur", "squeezeh", "squeezev", "zoomin",
    "coverleft", "coverright", "coverup", "coverdown", "revealleft", "revealright", "revealup",
    "revealdown",
}
GENERATORS = {"black", "white", "bars", "testsrc"}

CLIP_KEYS = {"source", "generator", "in", "out", "duration", "speed", "reverse", "interpolation",
             "fit", "crop", "zoom", "position", "rotation", "flip", "color", "effects", "transition",
             "fade_in", "fade_out", "audio", "label"}
OVERLAY_KEYS = {"source", "generator", "in", "out", "duration", "start", "width", "x", "y", "opacity",
                "fade_in", "fade_out", "color", "chroma_key", "label"}
TITLE_KEYS = {"text", "start", "duration", "size", "color", "font", "x", "y", "position", "box",
              "box_color", "shadow", "fade", "label"}
AUDIO_KEYS = {"source", "start", "in", "out", "duration", "volume_db", "fade_in", "fade_out",
              "duck", "loop", "label"}
EFFECT_KEYS = {"denoise", "sharpen", "blur", "stabilize"}


def _check_keys(obj, allowed, where):
    if not isinstance(obj, dict):
        raise RushesError(f"{where} must be an object")
    bad = set(obj) - allowed
    if bad:
        raise RushesError(f"{where}: unknown keys {', '.join(sorted(bad))}")


class Project:
    def __init__(self, data, base_dir):
        if not isinstance(data, dict):
            raise RushesError("Project file must contain a JSON object")
        self.base_dir = base_dir
        s = data.get("settings", {})
        self.width = int(s.get("width", 1920))
        self.height = int(s.get("height", 1080))
        if self.width % 2 or self.height % 2:
            raise RushesError("settings.width and settings.height must be even")
        self.fps = parse_rate(s.get("fps", "24"))
        self.matrix = s.get("color_matrix", "bt709")
        if self.matrix not in ("bt709", "bt2020"):
            raise RushesError("settings.color_matrix must be 'bt709' or 'bt2020'")
        self.sample_rate = int(s.get("sample_rate", 48000))
        self.channels = int(s.get("audio_channels", 2))
        if self.channels not in (1, 2, 6):
            raise RushesError("settings.audio_channels must be 1, 2 or 6")
        self.start_timecode = s.get("start_timecode")
        self.title = s.get("title")

        self.export = dict(data.get("export", {}))
        self.master = dict(data.get("master", {}))
        _check_keys(self.master, {"color", "fade_in", "fade_out", "loudness", "volume_db"}, "master")

        clips = data.get("clips") or []
        if not clips:
            raise RushesError("Project has no clips")
        self.clips = [self._clip(c, i) for i, c in enumerate(clips)]
        self.overlays = [self._overlay(o, i) for i, o in enumerate(data.get("overlays") or [])]
        self.titles = [self._title(t, i) for i, t in enumerate(data.get("titles") or [])]
        self.audio_tracks = [self._audio(a, i) for i, a in enumerate(data.get("audio") or [])]

        total = 0.0
        for i, clip in enumerate(self.clips):
            t = clip["transition"]
            if i == 0 and t:
                raise RushesError("clips[0] cannot have a transition (nothing to transition from)")
            if t:
                t["duration"] = min(t["duration"], clip["duration"], total)
                total -= t["duration"]
            clip["timeline_start"] = total
            total += clip["duration"]
        self.duration = total

    @classmethod
    def load(cls, path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except FileNotFoundError:
            raise RushesError(f"Project file not found: {path}") from None
        except json.JSONDecodeError as exc:
            raise RushesError(f"{path} is not valid JSON: {exc}") from None
        return cls(data, os.path.dirname(os.path.abspath(path)))

    def path(self, p):
        return p if os.path.isabs(p) else os.path.normpath(os.path.join(self.base_dir, p))

    def t(self, value):
        return parse_time(value, self.fps)

    # -- media ranges -------------------------------------------------------

    def _media(self, item, where, need_video):
        """Resolve source/generator and the source in/out range."""
        if bool(item.get("source")) == bool(item.get("generator")):
            raise RushesError(f"{where}: needs exactly one of 'source' or 'generator'")
        src_in = self.t(item.get("in", 0)) or 0.0
        if item.get("generator"):
            gen = item["generator"]
            if gen not in GENERATORS and not str(gen).startswith("#"):
                raise RushesError(f"{where}: generator must be one of {sorted(GENERATORS)} or '#RRGGBB'")
            dur = self.t(item.get("duration"))
            if not dur:
                raise RushesError(f"{where}: generators need a 'duration'")
            return {"generator": gen, "info": None, "in": 0.0, "src_len": dur, "is_image": False}
        path = self.path(item["source"])
        info = probe(path)
        if need_video and info.video is None:
            raise RushesError(f"{where}: {path} has no video stream")
        if not need_video and info.audio is None:
            raise RushesError(f"{where}: {path} has no audio stream")
        if info.is_image:
            dur = self.t(item.get("duration"))
            if not dur:
                raise RushesError(f"{where}: still images need a 'duration'")
            return {"source": path, "info": info, "in": 0.0, "src_len": dur, "is_image": True}
        src_out = self.t(item.get("out"))
        if src_out is None and item.get("duration") is not None:
            src_out = None  # resolved by the caller (depends on speed)
        if src_out is None:
            src_out = info.duration
        if src_out is None:
            raise RushesError(f"{where}: cannot determine media duration, set 'out'")
        if info.duration is not None and src_out > info.duration + 1e-3:
            raise RushesError(f"{where}: out point {src_out:.3f}s is past the end of the media ({info.duration:.3f}s)")
        if src_out <= src_in:
            raise RushesError(f"{where}: out point must be after in point")
        return {"source": path, "info": info, "in": src_in, "src_len": src_out - src_in, "is_image": False}

    def _clip(self, c, i):
        where = f"clips[{i}]"
        _check_keys(c, CLIP_KEYS, where)
        clip = dict(c)
        speed = float(c.get("speed", 1.0))
        if not 0.05 <= speed <= 20:
            raise RushesError(f"{where}: speed must be within 0.05..20")
        clip["speed"] = speed
        clip.update(self._media(c, where, need_video=True))
        if c.get("duration") is not None and c.get("out") is None and not clip["is_image"] and not c.get("generator"):
            clip["src_len"] = min(clip["src_len"], self.t(c["duration"]) * speed)
        clip["duration"] = clip["src_len"] / speed if not (clip["is_image"] or c.get("generator")) else clip["src_len"]
        clip["fit"] = c.get("fit", "fit")
        if clip["fit"] not in ("fit", "fill", "stretch"):
            raise RushesError(f"{where}: fit must be fit, fill or stretch")
        interp = c.get("interpolation", "frame_sampling")
        if interp not in ("frame_sampling", "frame_blending", "optical_flow"):
            raise RushesError(f"{where}: interpolation must be frame_sampling, frame_blending or optical_flow")
        clip["interpolation"] = interp
        _check_keys(c.get("effects", {}), EFFECT_KEYS, f"{where}.effects")
        clip["effects"] = dict(c.get("effects", {}))
        audio = dict(c.get("audio", {}))
        _check_keys(audio, {"volume_db", "mute", "fade_in", "fade_out"}, f"{where}.audio")
        clip["audio"] = audio
        if c.get("flip") not in (None, "h", "v", "hv"):
            raise RushesError(f"{where}: flip must be 'h', 'v' or 'hv'")
        tr = c.get("transition")
        if tr:
            if isinstance(tr, (int, float, str)) and not isinstance(tr, bool):
                tr = {"type": "cross_dissolve", "duration": tr}
            _check_keys(tr, {"type", "duration"}, f"{where}.transition")
            kind = tr.get("type", "cross_dissolve")
            kind = TRANSITIONS.get(kind, kind)
            if kind not in XFADE_TYPES:
                raise RushesError(f"{where}: unknown transition '{tr.get('type')}'")
            dur = self.t(tr.get("duration", 1.0))
            clip["transition"] = {"type": kind, "duration": dur} if dur and dur > 0 else None
        else:
            clip["transition"] = None
        return clip

    def _overlay(self, o, i):
        where = f"overlays[{i}]"
        _check_keys(o, OVERLAY_KEYS, where)
        ov = dict(o)
        ov.update(self._media(o, where, need_video=True))
        if o.get("duration") is not None and not ov["is_image"] and not o.get("generator") and o.get("out") is None:
            ov["src_len"] = min(ov["src_len"], self.t(o["duration"]))
        ov["duration"] = ov["src_len"]
        ov["start"] = self.t(o.get("start", 0)) or 0.0
        ov["opacity"] = float(o.get("opacity", 1.0))
        if not 0 <= ov["opacity"] <= 1:
            raise RushesError(f"{where}: opacity must be within 0..1")
        return ov

    def _title(self, t, i):
        where = f"titles[{i}]"
        _check_keys(t, TITLE_KEYS, where)
        if not t.get("text"):
            raise RushesError(f"{where}: needs 'text'")
        title = dict(t)
        title["start"] = self.t(t.get("start", 0)) or 0.0
        title["duration"] = self.t(t.get("duration", 4))
        return title

    def _audio(self, a, i):
        where = f"audio[{i}]"
        _check_keys(a, AUDIO_KEYS, where)
        track = dict(a)
        path = self.path(a["source"]) if a.get("source") else None
        if not path:
            raise RushesError(f"{where}: needs 'source'")
        info = probe(path)
        if info.audio is None:
            raise RushesError(f"{where}: {path} has no audio stream")
        track["source"] = path
        track["in"] = self.t(a.get("in", 0)) or 0.0
        track["start"] = self.t(a.get("start", 0)) or 0.0
        out = self.t(a.get("out"))
        if a.get("loop"):
            track["length"] = self.t(a.get("duration")) or None  # None -> until end of timeline
        else:
            end = out if out is not None else info.duration
            length = end - track["in"]
            if a.get("duration") is not None:
                length = min(length, self.t(a["duration"]))
            if length <= 0:
                raise RushesError(f"{where}: empty audio range")
            track["length"] = length
        return track
