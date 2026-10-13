"""Small helpers shared by the rest of the package."""

import itertools
import re
import shutil
import subprocess
import sys
from fractions import Fraction


class RushesError(Exception):
    """A user-facing error (bad project, missing file, ffmpeg failure)."""


def require_ffmpeg():
    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            raise RushesError(f"'{tool}' was not found on PATH. Install FFmpeg 6.0 or newer.")


def parse_rate(value):
    """'24', '23.976', '24000/1001' -> Fraction."""
    if isinstance(value, (int, float)):
        value = str(value)
    value = value.strip()
    if value in ("23.976", "23.98"):
        return Fraction(24000, 1001)
    if value == "29.97":
        return Fraction(30000, 1001)
    if value == "59.94":
        return Fraction(60000, 1001)
    return Fraction(value).limit_denominator(1001)


def parse_time(value, fps=None):
    """Seconds from a number, 'SS.ms', 'HH:MM:SS.ms' or SMPTE 'HH:MM:SS:FF'."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if re.fullmatch(r"-?\d+(\.\d+)?", text):
        return float(text)
    parts = text.replace(";", ":").split(":")
    if len(parts) == 4:
        if fps is None:
            raise RushesError(f"Timecode '{text}' needs a frame rate")
        h, m, s, f = (int(p) for p in parts)
        return h * 3600 + m * 60 + s + f / float(fps)
    if len(parts) in (2, 3):
        parts = [float(p) for p in parts]
        while len(parts) < 3:
            parts.insert(0, 0.0)
        h, m, s = parts
        return h * 3600 + m * 60 + s
    raise RushesError(f"Cannot parse time value '{text}'")


_uid_counter = itertools.count(1)


def uid(prefix):
    """A filtergraph label that is unique for the life of the process."""
    return f"{prefix}{next(_uid_counter)}"


def fmt_seconds(seconds):
    return f"{seconds:.6f}".rstrip("0").rstrip(".") or "0"


def escape_value(value):
    """Escape a string for use as a filter option value inside -filter_complex."""
    value = str(value)
    value = re.sub(r"([\\':])", r"\\\1", value)       # option level
    value = re.sub(r"([\\'\[\],;])", r"\\\1", value)  # graph level
    return value


def run(cmd, quiet=False):
    """Run a command, raising RushesError with ffmpeg's tail on failure."""
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if proc.returncode != 0:
        tail = "\n".join(proc.stderr.strip().splitlines()[-15:])
        raise RushesError(f"Command failed ({cmd[0]}):\n{tail}")
    if not quiet and proc.stdout:
        sys.stdout.write(proc.stdout)
    return proc


def run_with_progress(cmd, total_seconds, label="Rendering"):
    """Run ffmpeg with -progress on stdout and draw a progress bar."""
    cmd = cmd[:1] + ["-progress", "pipe:1", "-nostats"] + cmd[1:]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    stderr_lines = []

    import threading

    def drain():
        for line in proc.stderr:
            stderr_lines.append(line)

    t = threading.Thread(target=drain, daemon=True)
    t.start()
    interactive = sys.stderr.isatty()
    last_pct = -1
    for line in proc.stdout:
        key, _, val = line.strip().partition("=")
        if key == "out_time_us" and val.isdigit() and total_seconds > 0:
            pct = min(100, int(int(val) / 1e6 / total_seconds * 100))
            if pct != last_pct:
                last_pct = pct
                if interactive:
                    bar = "#" * (pct // 4)
                    sys.stderr.write(f"\r{label} [{bar:<25}] {pct:3d}%")
                    sys.stderr.flush()
    proc.wait()
    t.join()
    proc.stdout.close()
    proc.stderr.close()
    if interactive:
        sys.stderr.write("\n")
    if proc.returncode != 0:
        tail = "".join(stderr_lines[-15:])
        raise RushesError(f"ffmpeg failed:\n{tail}")
    return "".join(stderr_lines)
