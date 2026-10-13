"""ffprobe wrapper with a small cache."""

import json
import os
import subprocess

from .util import RushesError, parse_rate

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".exr", ".dpx", ".bmp", ".webp", ".dng"}

_cache = {}


class MediaInfo:
    def __init__(self, path, data):
        self.path = path
        self.data = data
        streams = data.get("streams", [])
        self.video = next((s for s in streams if s.get("codec_type") == "video"
                           and not s.get("disposition", {}).get("attached_pic")), None)
        self.audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
        fmt = data.get("format", {})
        dur = fmt.get("duration") or (self.video or {}).get("duration") or (self.audio or {}).get("duration")
        self.duration = float(dur) if dur not in (None, "N/A") else None
        self.is_image = os.path.splitext(path)[1].lower() in IMAGE_EXTS

    @property
    def has_audio(self):
        return self.audio is not None

    @property
    def width(self):
        return int(self.video["width"]) if self.video else None

    @property
    def height(self):
        return int(self.video["height"]) if self.video else None

    @property
    def fps(self):
        if not self.video:
            return None
        rate = self.video.get("avg_frame_rate") or self.video.get("r_frame_rate")
        if not rate or rate.startswith("0"):
            rate = self.video.get("r_frame_rate", "0/1")
        try:
            r = parse_rate(rate)
        except (ValueError, ZeroDivisionError):
            return None
        return r if r > 0 else None

    def summary(self):
        lines = [f"File:      {self.path}"]
        fmt = self.data.get("format", {})
        lines.append(f"Container: {fmt.get('format_long_name', fmt.get('format_name', '?'))}")
        if self.duration is not None:
            lines.append(f"Duration:  {self.duration:.3f}s")
        if fmt.get("bit_rate"):
            lines.append(f"Bitrate:   {int(fmt['bit_rate']) / 1e6:.1f} Mb/s")
        v = self.video
        if v:
            lines.append(
                f"Video:     {v.get('codec_name')} {v.get('profile', '')}".rstrip()
                + f", {v['width']}x{v['height']}, {v.get('pix_fmt')}"
                + (f", {float(self.fps):.3f} fps" if self.fps else "")
            )
            color = [v.get(k) for k in ("color_range", "color_space", "color_primaries", "color_transfer")]
            if any(color):
                lines.append("Color:     range=%s matrix=%s primaries=%s transfer=%s" % tuple(c or "?" for c in color))
            tc = (v.get("tags") or {}).get("timecode") or (fmt.get("tags") or {}).get("timecode")
            if tc:
                lines.append(f"Timecode:  {tc}")
        a = self.audio
        if a:
            lines.append(
                f"Audio:     {a.get('codec_name')}, {a.get('sample_rate')} Hz, "
                f"{a.get('channels')} ch ({a.get('channel_layout', '?')}), {a.get('sample_fmt')}"
            )
        return "\n".join(lines)


def probe(path):
    path = os.path.abspath(path)
    if path in _cache:
        return _cache[path]
    if not os.path.exists(path):
        raise RushesError(f"Media not found: {path}")
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    if proc.returncode != 0:
        raise RushesError(f"ffprobe could not read {path}:\n{proc.stderr.strip()}")
    info = MediaInfo(path, json.loads(proc.stdout))
    if info.video is None and info.audio is None:
        raise RushesError(f"No audio or video streams in {path}")
    _cache[path] = info
    return info
