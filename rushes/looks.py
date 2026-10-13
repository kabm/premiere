"""Creative look tools and image analysis (HSL secondaries, vignette, grain, auto tone, colour match...).

Hook contract (called from color.build_color_chain):
    KEYS: set of colour-config keys this module owns
    early_filters(cfg, ctx) -> list[str]   after the input transform/LUT, before white balance
    late_filters(cfg, ctx) -> list[str]    after curves, before output_lut
    register(subparsers)                   optional CLI commands
"""

KEYS = set()


def early_filters(cfg, ctx):
    return []


def late_filters(cfg, ctx):
    return []


def register(sub):
    pass
