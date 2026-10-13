"""LUT loading, validation and filter generation."""

import os

from .util import RushesError, escape_value

LUT_EXTS = {".cube", ".3dl", ".dat", ".m3d", ".csp"}


class Lut:
    def __init__(self, path):
        self.path = os.path.abspath(path)
        if not os.path.isfile(self.path):
            raise RushesError(f"LUT not found: {path}")
        ext = os.path.splitext(self.path)[1].lower()
        if ext not in LUT_EXTS:
            raise RushesError(f"Unsupported LUT format '{ext}' (supported: {', '.join(sorted(LUT_EXTS))})")
        self.ext = ext
        self.title = None
        self.size = None
        self.kind = "3d"
        self.domain_min = [0.0, 0.0, 0.0]
        self.domain_max = [1.0, 1.0, 1.0]
        self.entries = None
        if ext == ".cube":
            self._parse_cube()

    def _parse_cube(self):
        entries = 0
        with open(self.path, "r", encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                key = line.split()[0].upper()
                if key == "TITLE":
                    self.title = line[5:].strip().strip('"')
                elif key == "LUT_3D_SIZE":
                    self.kind, self.size = "3d", int(line.split()[1])
                elif key == "LUT_1D_SIZE":
                    self.kind, self.size = "1d", int(line.split()[1])
                elif key == "DOMAIN_MIN":
                    self.domain_min = [float(v) for v in line.split()[1:4]]
                elif key == "DOMAIN_MAX":
                    self.domain_max = [float(v) for v in line.split()[1:4]]
                elif key[0].isdigit() or key[0] in "-.":
                    parts = line.split()
                    if len(parts) != 3:
                        raise RushesError(f"Malformed LUT row in {self.path}: '{line}'")
                    entries += 1
        if self.size is None:
            raise RushesError(f"{self.path}: missing LUT_3D_SIZE / LUT_1D_SIZE")
        expected = self.size ** 3 if self.kind == "3d" else self.size
        if entries != expected:
            raise RushesError(f"{self.path}: expected {expected} entries for size {self.size}, found {entries}")
        self.entries = entries

    def filter(self):
        f = escape_value(self.path)
        if self.kind == "1d":
            return f"lut1d=file={f}:interp=cubic"
        return f"lut3d=file={f}:interp=tetrahedral"

    def describe(self):
        lines = [f"LUT:     {self.path}", f"Type:    {self.kind.upper()} ({self.ext})"]
        if self.title:
            lines.append(f"Title:   {self.title}")
        if self.size:
            n = f"{self.size}^3" if self.kind == "3d" else str(self.size)
            lines.append(f"Size:    {n} ({self.entries} entries)")
        if self.domain_min != [0.0, 0.0, 0.0] or self.domain_max != [1.0, 1.0, 1.0]:
            lines.append(f"Domain:  {self.domain_min} -> {self.domain_max}")
        lines.append("Status:  OK")
        return "\n".join(lines)


def write_cube(path, size, fn, title=None):
    """Write a 3D .cube LUT where fn(r, g, b) -> (r, g, b) on 0..1 values."""
    with open(path, "w") as fh:
        if title:
            fh.write(f'TITLE "{title}"\n')
        fh.write(f"LUT_3D_SIZE {size}\n")
        step = size - 1
        for b in range(size):
            for g in range(size):
                for r in range(size):
                    out = fn(r / step, g / step, b / step)
                    fh.write("%.6f %.6f %.6f\n" % tuple(min(1.0, max(0.0, v)) for v in out))
