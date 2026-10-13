"""Colour management: camera log/gamut input transforms and HDR/SDR output transforms.

Hook contract (called from color.build_color_chain):
    KEYS: set of colour-config keys this module owns (merged into color.COLOR_KEYS)
    input_filters(cfg, ctx) -> list[str]   RGB-domain filters run first in the colour chain
    register(subparsers)                   optional CLI commands
ctx is a dict: cache_dir, base_dir, matrix, source, in, src_len, fps, width, height (some may be None).
"""

KEYS = set()


def input_filters(cfg, ctx):
    return []


def register(sub):
    pass
