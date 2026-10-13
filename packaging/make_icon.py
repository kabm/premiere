"""Draw the Rushes app icon and write it as a multi-size .ico (standard library only).

Usage: python packaging/make_icon.py packaging/windows/rushes.ico [preview.png]
"""

import math
import struct
import sys
import zlib


def png_bytes(size, pixels):
    """pixels: list of rows of (r, g, b, a) tuples."""
    raw = b"".join(b"\x00" + bytes(c for px in row for c in px) for row in pixels)

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")


def rounded_rect_alpha(x, y, size, radius):
    """Coverage (0..1) of a rounded square, with a 1px anti-aliased edge."""
    cx = min(max(x, radius), size - radius)
    cy = min(max(y, radius), size - radius)
    d = math.hypot(x - cx, y - cy)
    return max(0.0, min(1.0, radius - d + 0.5))


def triangle_alpha(x, y, pts):
    """Anti-aliased coverage of a triangle via signed edge distances."""
    cov = 1.0
    for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1]):
        ex, ey = x2 - x1, y2 - y1
        length = math.hypot(ex, ey)
        dist = ((x - x1) * ey - (y - y1) * ex) / length
        cov = min(cov, max(0.0, min(1.0, 0.5 + dist)))
    return cov


def draw(size):
    s = size / 256.0
    rows = []
    for py in range(size):
        row = []
        for px in range(size):
            x, y = (px + 0.5) / s, (py + 0.5) / s
            a = rounded_rect_alpha(x, y, 256, 52)
            # dark teal-to-ink vertical gradient
            t = y / 256
            r, g, b = 18 + 10 * t, 28 + 30 * (1 - t), 40 + 40 * (1 - t)
            # film perforations along top and bottom
            for band_y in (30, 226):
                if abs(y - band_y) < 9:
                    for k in range(7):
                        hx = 40 + k * 29
                        if abs(x - hx) < 8:
                            a_hole = max(0.0, min(1.0, 8 - max(abs(x - hx), abs(y - band_y) * 0.9) + 0.5))
                            r, g, b = (r * (1 - a_hole) + 230 * a_hole, g * (1 - a_hole) + 236 * a_hole,
                                       b * (1 - a_hole) + 240 * a_hole)
            # warm "play" triangle (grade + playback)
            tri = triangle_alpha(x, y, [(98, 78), (98, 178), (186, 128)])
            r, g, b = (r * (1 - tri) + 255 * tri, g * (1 - tri) + 159 * tri, b * (1 - tri) + 67 * tri)
            row.append((int(r), int(g), int(b), int(255 * a)))
        rows.append(row)
    return rows


def write_ico(path, sizes=(256, 64, 48, 32, 16)):
    images = [(sz, png_bytes(sz, draw(sz))) for sz in sizes]
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries, blobs = b"", b""
    for sz, data in images:
        dim = 0 if sz >= 256 else sz
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
        blobs += data
    with open(path, "wb") as fh:
        fh.write(header + entries + blobs)
    return images[0][1]


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "rushes.ico"
    big = write_ico(out)
    if len(sys.argv) > 2:
        with open(sys.argv[2], "wb") as fh:
            fh.write(big)
    print(f"Wrote {out}")
