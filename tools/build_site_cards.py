#!/usr/bin/env python3
"""Generate the LED-matrix backdrops for the landing page feature cards.

Matches the real BUSY Bar panel look (see site/img/device_*.png): round
LEDs at ~79% of pitch, nearly touching, with unlit LEDs faintly visible
as a dark dot grid. A corner glow in an MTA line color is quantized to a
few brightness steps with 4x4 Bayer ordered dithering, so the falloff
scatters into individual lit dots the way low brightness does on the
device.

Rendered @2x for retina; the CSS pins the image at BG_CSS_W x BG_CSS_H
css px anchored top-right, so dots stay round at any card size. The
unlit grid fades to nothing by the bottom so taller cards don't hit a
hard edge.

Pure stdlib. Writes site/img/card_glow_<name>-<hash8>.png (content-hashed
filenames for cache busting, like the app's device assets), removes stale
copies, and prints the CSS url() lines to paste into site/index.html.
"""
import hashlib
import math
import pathlib
import struct
import zlib

PITCH = 14          # css px between LED centers
DOT_R = 5.5         # css px dot radius (diameter 11 = 79% of pitch, like the device)
SCALE = 2           # render @2x for retina
COLS, ROWS = 62, 40
BG_CSS_W, BG_CSS_H = COLS * PITCH, ROWS * PITCH   # 868 x 560 css px

LEVELS = 6          # brightness quantization steps for the glow
RX, RY = 460.0, 310.0   # glow radii in css px from the top-right corner
UNLIT_ALPHA = 0.04  # white, over the #1a1b1f card = a barely-there dark grid
UNLIT_FADE = 0.6    # unlit grid is gone by this fraction of the height

BAYER4 = [
    [0, 8, 2, 10],
    [12, 4, 14, 6],
    [3, 11, 1, 9],
    [15, 7, 13, 5],
]

# (name, hex, peak alpha) -- darker colors get more alpha so every glow
# reads about equally bright on the near-black card.
COLORS = [
    ("yellow", "FCC30B", 0.30),   # N/Q/R/W
    ("blue",   "0039A6", 0.62),   # A/C/E
    ("orange", "FF6319", 0.34),   # B/D/F/M
    ("green",  "00933C", 0.46),   # G
]


def png_bytes(w: int, h: int, buf: bytearray) -> bytes:
    stride = w * 4
    raw = b"".join(
        b"\x00" + bytes(buf[y * stride : (y + 1) * stride]) for y in range(h)
    )

    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def dot_coverage() -> list:
    # anti-aliased circle mask for one cell, computed once (all dots identical)
    cell = PITCH * SCALE
    r = DOT_R * SCALE
    c = (cell - 1) / 2
    mask = []
    for j in range(cell):
        row = []
        for i in range(cell):
            d = math.hypot(i - c, j - c)
            row.append(min(1.0, max(0.0, r - d + 0.5)))
        mask.append(row)
    return mask


def glow_alpha(col: int, row: int, peak: float) -> float:
    # dithered corner falloff, in absolute css px so it looks the same
    # whatever the card size crops it to
    rx = BG_CSS_W - (col + 0.5) * PITCH
    ry = (row + 0.5) * PITCH
    a = max(0.0, 1.0 - math.hypot(rx / RX, ry / RY)) ** 1.5
    t = a * (LEVELS - 1)
    level = int(t)
    if t - level > (BAYER4[row % 4][col % 4] + 0.5) / 16:
        level += 1
    return level / (LEVELS - 1) * peak


def build(name: str, hex_color: str, peak: float, out_dir: pathlib.Path, mask) -> None:
    lit_rgb = tuple(int(hex_color[i : i + 2], 16) for i in (0, 2, 4))
    cell = PITCH * SCALE
    w, h = COLS * cell, ROWS * cell
    buf = bytearray(w * h * 4)
    for row in range(ROWS):
        for col in range(COLS):
            a = glow_alpha(col, row, peak)
            if a > 0:
                r, g, b = lit_rgb
            else:
                r, g, b = 255, 255, 255
                a = UNLIT_ALPHA * max(0.0, 1.0 - (row + 0.5) / ROWS / UNLIT_FADE)
            if a <= 0:
                continue
            x0, y0 = col * cell, row * cell
            for j in range(cell):
                base = ((y0 + j) * w + x0) * 4
                mrow = mask[j]
                for i in range(cell):
                    cov = mrow[i]
                    if cov:
                        o = base + i * 4
                        buf[o] = r
                        buf[o + 1] = g
                        buf[o + 2] = b
                        buf[o + 3] = round(255 * a * cov)
    data = png_bytes(w, h, buf)
    for stale in out_dir.glob(f"card_glow_{name}*.png"):
        stale.unlink()
    fname = f"card_glow_{name}-{hashlib.sha1(data).hexdigest()[:8]}.png"
    (out_dir / fname).write_bytes(data)
    return fname


def main() -> None:
    out_dir = pathlib.Path(__file__).resolve().parent.parent / "site" / "img"
    mask = dot_coverage()
    for i, (name, hex_color, peak) in enumerate(COLORS, 1):
        fname = build(name, hex_color, peak, out_dir, mask)
        print(f"  .grid .card:nth-child({i})::before {{ background-image: url(img/{fname}); }}")
    print(f"css background-size: {BG_CSS_W}px {BG_CSS_H}px")


if __name__ == "__main__":
    main()
