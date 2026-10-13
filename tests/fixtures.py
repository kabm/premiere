"""Generates small synthetic media and LUTs for the tests."""

import os
import subprocess

from rushes.lut import write_cube


def ffmpeg(*args):
    subprocess.run(["ffmpeg", "-hide_banner", "-v", "error", "-y", *args], check=True)


def make_fixtures(root):
    os.makedirs(root, exist_ok=True)
    clip_a = os.path.join(root, "clip_a.mov")
    clip_b = os.path.join(root, "clip b (log).mov")  # spaces and parentheses on purpose
    music = os.path.join(root, "music.wav")
    logo = os.path.join(root, "logo.png")
    if not os.path.exists(clip_a):
        # 10-bit 4:2:2 ProRes with a tone, like a camera original
        ffmpeg("-f", "lavfi", "-i", "testsrc2=s=640x360:r=24:d=3", "-f", "lavfi", "-i", "sine=f=440:d=3:r=48000",
               "-c:v", "prores_ks", "-profile:v", "3", "-pix_fmt", "yuv422p10le", "-c:a", "pcm_s24le",
               "-timecode", "10:00:00:00", clip_a)
        # Flat "log-looking" clip, different size and frame rate, no audio
        ffmpeg("-f", "lavfi", "-i", "smptehdbars=s=800x600:r=30:d=3",
               "-vf", "eq=contrast=0.5:saturation=0.4", "-c:v", "prores_ks", "-pix_fmt", "yuv422p10le", clip_b)
        ffmpeg("-f", "lavfi", "-i", "sine=f=220:d=6:r=44100", "-ac", "2", music)
        ffmpeg("-f", "lavfi", "-i", "color=c=orange:s=200x100,format=rgba", "-frames:v", "1", logo)
    luts = {
        "identity": os.path.join(root, "identity.cube"),
        "log_to_709": os.path.join(root, "log_to_709.cube"),
        "warm": os.path.join(root, "warm look.cube"),
    }
    if not os.path.exists(luts["warm"]):
        write_cube(luts["identity"], 17, lambda r, g, b: (r, g, b), "Identity")
        # crude "log to display": expand contrast around mid grey
        write_cube(luts["log_to_709"], 33,
                   lambda r, g, b: tuple(0.5 + (c - 0.5) * 1.6 for c in (r, g, b)), "Log to 709")
        write_cube(luts["warm"], 33, lambda r, g, b: (r * 1.08 + 0.02, g * 1.01, b * 0.9), "Warm")
    return {"clip_a": clip_a, "clip_b": clip_b, "music": music, "logo": logo, **luts}
