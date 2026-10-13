"""Colour pipeline: input LUT -> basic correction -> creative LUT -> colour wheels -> curves.

The order mirrors a Lumetri-style panel. Everything here runs on 16-bit planar
RGB (gbrp16le), so no step is quantised to 8 bits.
"""

import math
import os

from . import colormgmt, looks
from .lut import Lut
from .util import RushesError, escape_value, uid

WORKING_YUV = "yuv444p16le"
WORKING_RGB = "gbrp16le"
SCALE_FLAGS = "flags=lanczos+accurate_rnd+full_chroma_int+full_chroma_inp"

LUMA = {"bt709": (0.2126, 0.7152, 0.0722), "bt2020": (0.2627, 0.6780, 0.0593)}

COLOR_KEYS = {
    "input_lut", "temperature", "tint", "exposure", "contrast", "highlights", "shadows",
    "whites", "blacks", "saturation", "vibrance", "lut", "lut_intensity",
    "shadow_tint", "midtone_tint", "highlight_tint", "curves", "output_lut",
} | colormgmt.KEYS | looks.KEYS


def to_rgb(matrix="bt709"):
    return f"scale=in_color_matrix={matrix}:in_range=tv:{SCALE_FLAGS},format={WORKING_RGB}"


def to_yuv(matrix="bt709"):
    return f"scale=out_color_matrix={matrix}:out_range=tv:{SCALE_FLAGS},format={WORKING_YUV}"


def _num(cfg, key, default, lo, hi):
    v = cfg.get(key, default)
    try:
        v = float(v)
    except (TypeError, ValueError):
        raise RushesError(f"color.{key} must be a number, got {v!r}") from None
    if not lo <= v <= hi:
        raise RushesError(f"color.{key}={v} is outside {lo}..{hi}")
    return v


def _f(v):
    return f"{v:.5f}".rstrip("0").rstrip(".") if v != 0 else "0"


def tone_curve(contrast=0.0, highlights=0.0, shadows=0.0, whites=0.0, blacks=0.0):
    """Build a master curve from Lumetri-style sliders (each -100..100)."""
    c, h, s, w, b = (x / 100.0 for x in (contrast, highlights, shadows, whites, blacks))
    xs = [0.0, 0.1, 0.25, 0.5, 0.75, 0.9, 1.0]
    ys = []
    for x in xs:
        y = x + c * (x - 0.5) * math.sin(math.pi * x) * 0.8
        ys.append(y)
    ys[0] += b * 0.1
    ys[1] += b * 0.05 + s * 0.04
    ys[2] += s * 0.1
    ys[4] += h * 0.1
    ys[5] += h * 0.04 + w * 0.05
    ys[6] += w * 0.1
    out, prev = [], 0.0
    for x, y in zip(xs, ys):
        y = min(1.0, max(prev, y))  # clamp and keep monotonic
        prev = y
        out.append(f"{_f(x)}/{_f(y)}")
    return " ".join(out)


def _tint_triplet(value, name):
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise RushesError(f"color.{name} must be [r, g, b] with values -1..1")
    vals = [float(v) for v in value]
    if any(abs(v) > 1 for v in vals):
        raise RushesError(f"color.{name} values must be within -1..1")
    return vals


def _resolve(path, base_dir):
    return path if os.path.isabs(path) else os.path.join(base_dir, path)


def build_color_chain(cfg, base_dir, matrix="bt709", ctx=None):
    """Return a list of filters (RGB domain). Empty list when there is nothing to do.

    ctx carries context for analysis-based tools (see colormgmt/looks hook contracts).
    """
    if not cfg:
        return []
    unknown = set(cfg) - COLOR_KEYS
    if unknown:
        raise RushesError(f"Unknown color settings: {', '.join(sorted(unknown))}")
    ctx = dict(ctx or {})
    ctx.setdefault("base_dir", base_dir)
    ctx.setdefault("matrix", matrix)
    chain = list(colormgmt.input_filters(cfg, ctx))

    if cfg.get("input_lut"):
        chain.append(Lut(_resolve(cfg["input_lut"], base_dir)).filter())

    chain += looks.early_filters(cfg, ctx)

    temp = _num(cfg, "temperature", 6500, 1000, 40000)
    if temp != 6500:
        chain.append(f"colortemperature=temperature={_f(temp)}")

    tint = _num(cfg, "tint", 0, -100, 100) / 100.0
    if tint:
        g = -tint * 0.15
        chain.append(f"colorbalance=rm={_f(-g / 2)}:gm={_f(g)}:bm={_f(-g / 2)}")

    exposure = _num(cfg, "exposure", 0, -6, 6)
    if exposure:
        gain = _f(2 ** exposure)
        chain.append(f"colorchannelmixer=rr={gain}:gg={gain}:bb={gain}")

    tone = {k: _num(cfg, k, 0, -100, 100) for k in ("contrast", "highlights", "shadows", "whites", "blacks")}
    if any(tone.values()):
        chain.append(f"curves=master={escape_value(tone_curve(**tone))}")

    sat = _num(cfg, "saturation", 100, 0, 300) / 100.0
    if sat != 1:
        wr, wg, wb = LUMA.get(matrix, LUMA["bt709"])
        m = []
        for i, row in enumerate("rgb"):
            for j, (col, w) in enumerate(zip("rgb", (wr, wg, wb))):
                m.append(f"{row}{col}={_f((1 - sat) * w + (sat if i == j else 0))}")
        chain.append("colorchannelmixer=" + ":".join(m))

    vib = _num(cfg, "vibrance", 0, -100, 100)
    if vib:
        chain.append(f"vibrance=intensity={_f(vib / 100.0)}")

    if cfg.get("lut"):
        lut = Lut(_resolve(cfg["lut"], base_dir)).filter()
        intensity = _num(cfg, "lut_intensity", 100, 0, 100) / 100.0
        if intensity >= 1:
            chain.append(lut)
        elif intensity > 0:
            # Mix the graded image with the ungraded one, like Lumetri's "Intensity".
            a, b, c = uid("lut"), uid("lut"), uid("lut")
            chain.append(f"split[{a}][{b}];[{b}]{lut}[{c}];"
                         f"[{a}][{c}]blend=all_expr=A*(1-{_f(intensity)})+B*{_f(intensity)}")

    wheels = {}
    for prefix, key in (("s", "shadow_tint"), ("m", "midtone_tint"), ("h", "highlight_tint")):
        if cfg.get(key) is not None:
            r, g, b = _tint_triplet(cfg[key], key)
            for ch, v in zip("rgb", (r, g, b)):
                if v:
                    wheels[f"{ch}{prefix}"] = _f(v)
    if wheels:
        chain.append("colorbalance=" + ":".join(f"{k}={v}" for k, v in wheels.items()) + ":pl=1")

    curves = cfg.get("curves")
    if curves:
        allowed = {"master", "red", "green", "blue", "preset"}
        bad = set(curves) - allowed
        if bad:
            raise RushesError(f"Unknown curves keys: {', '.join(sorted(bad))}")
        chain.append("curves=" + ":".join(f"{k}={escape_value(v)}" for k, v in curves.items()))

    chain += looks.late_filters(cfg, ctx)

    if cfg.get("output_lut"):
        chain.append(Lut(_resolve(cfg["output_lut"], base_dir)).filter())

    return chain


def wrap_rgb(chain, matrix="bt709"):
    """Bracket an RGB chain with YUV<->RGB conversions; returns a filter string or ''."""
    if not chain:
        return ""
    return ",".join([to_rgb(matrix)] + chain + [to_yuv(matrix)])
