"""Check that the FFmpeg on PATH has what rushes needs. Exit code 1 if something required is missing.

Usage: python packaging/check_ffmpeg.py
"""

import subprocess
import sys

REQUIRED_ENCODERS = ["prores_ks", "dnxhd", "ffv1", "libx264", "libx265", "cfhd", "tiff", "exr", "dpx", "png",
                     "pcm_s24le", "flac", "aac", "mov_text"]
OPTIONAL_ENCODERS = ["libsvtav1", "libvpx-vp9", "libopus"]
REQUIRED_FILTERS = ["lut3d", "lut1d", "xfade", "acrossfade", "loudnorm", "drawtext", "colortemperature",
                    "colorbalance", "colorchannelmixer", "curves", "vibrance", "hqdn3d", "unsharp", "zscale",
                    "sidechaincompress", "afftdn", "equalizer", "acompressor", "alimiter", "scdet", "blend",
                    "overlay", "geq", "minterpolate", "showwavespic", "ebur128"]
OPTIONAL_FILTERS = ["vidstabdetect", "vidstabtransform", "rubberband", "whisper", "subtitles"]


def names(kind):
    out = subprocess.run(["ffmpeg", "-hide_banner", f"-{kind}"], capture_output=True, text=True).stdout
    found = set()
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            found.add(parts[1])
    return found


def main():
    version = subprocess.run(["ffmpeg", "-hide_banner", "-version"], capture_output=True, text=True).stdout
    print(version.splitlines()[0] if version else "ffmpeg not found")
    encoders, filters = names("encoders"), names("filters")
    missing = [e for e in REQUIRED_ENCODERS if e not in encoders] + [f for f in REQUIRED_FILTERS if f not in filters]
    optional = [e for e in OPTIONAL_ENCODERS if e not in encoders] + [f for f in OPTIONAL_FILTERS if f not in filters]
    for name in optional:
        print(f"optional, not available: {name}")
    if missing:
        print("MISSING (required): " + ", ".join(missing))
        return 1
    print("All required encoders and filters are present.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
