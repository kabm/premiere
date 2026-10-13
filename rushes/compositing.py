"""Compositing of overlay tracks: blend modes, masks, keyframed overlay motion.

Hook contract:
    composite(base_label, overlay_label, ov, project, out_label) -> str
        Returns one filtergraph chain that combines [base] and [overlay] into [out].
"""

from .color import WORKING_YUV
from .util import escape_value


def composite(base, ol, ov, project, out):
    x = {"center": "(W-w)/2", "left": "W*0.05", "right": "W*0.95-w"}.get(str(ov.get("x", "center")), ov.get("x"))
    y = {"center": "(H-h)/2", "top": "H*0.05", "bottom": "H*0.95-h"}.get(str(ov.get("y", "center")), ov.get("y"))
    return (f"[{base}][{ol}]overlay=x={escape_value(x)}:y={escape_value(y)}:eof_action=pass:"
            f"format=yuv444p10,format={WORKING_YUV}[{out}]")
