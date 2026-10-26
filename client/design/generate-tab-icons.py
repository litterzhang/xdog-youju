"""Generate small deterministic monochrome native-tab PNGs without external packages."""

import math
import struct
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "src/assets/tabbar"
SIZE = 64


def distance(x, y, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    t = max(0, min(1, ((x - a[0]) * dx + (y - a[1]) * dy) / (dx * dx + dy * dy)))
    return math.hypot(x - a[0] - t * dx, y - a[1] - t * dy)


def png(name, paths, circles, *, output=ROOT):
    for selected in (False, True):
        color, width = (23, 3.5) if selected else (138, 3)
        rows = []
        for y in range(SIZE):
            row = bytearray([0])
            for x in range(SIZE):
                coverage = 0
                for sx, sy in ((.25, .25), (.75, .25), (.25, .75), (.75, .75)):
                    d = min([distance(x + sx, y + sy, a, b) for path in paths for a, b in zip(path, path[1:])] +
                            [abs(math.hypot(x + sx - cx, y + sy - cy) - radius) for cx, cy, radius in circles])
                    coverage += d <= width / 2
                row.extend((color, color, color, coverage * 255 // 4))
            rows.append(row)
        def chunk(kind, data):
            return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
        raw = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", SIZE, SIZE, 8, 6, 0, 0, 0))
        raw += chunk(b"IDAT", zlib.compress(b"".join(rows), 9)) + chunk(b"IEND", b"")
        (output / (name + ("-selected" if selected else "") + ".png")).write_bytes(raw)


if __name__ == '__main__':
    png("home", [[(10, 29), (32, 10), (54, 29)], [(16, 25), (16, 53), (26, 53), (26, 37), (38, 37), (38, 53), (48, 53), (48, 25)]], [])
    png("jobs", [[(12, 24), (52, 24), (52, 51), (12, 51), (12, 24)], [(24, 24), (24, 15), (40, 15), (40, 24)], [(12, 34), (52, 34)], [(28, 32), (36, 32), (36, 38), (28, 38), (28, 32)]], [])
    png("me", [[(12, 53), (15, 43), (23, 37), (41, 37), (49, 43), (52, 53)]], [(32, 21, 10)])
