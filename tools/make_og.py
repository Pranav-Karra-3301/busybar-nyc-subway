#!/usr/bin/env python3
"""Render the 1200x630 OpenGraph preview image for the landing page.

Dark canvas, Helvetica, the device-framed hardware capture front and
center. Output is palette-quantized to stay under the 300 KB budget.

    python3 tools/make_og.py            # writes site/og.png
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
DEVICE_SHOT = ROOT / "docs" / "img" / "device_card_nq.png"
OUT = ROOT / "site" / "og.png"

W, H = 1200, 630
BG = (14, 14, 16)
ACCENT = (234, 82, 18)
INK = (255, 255, 255)
FAINT = (130, 130, 140)

HELVETICA = "/System/Library/Fonts/Helvetica.ttc"


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(HELVETICA, size, index=1 if bold else 0)


def tracked(d: ImageDraw.ImageDraw, xy, text, f, fill, tracking=0):
    """Draw text with letter-spacing; returns end x."""
    x, y = xy
    for ch in text:
        d.text((x, y), ch, font=f, fill=fill)
        x += d.textlength(ch, font=f) + tracking
    return x


def centered_tracked(d, cx, y, text, f, fill, tracking=0):
    total = sum(d.textlength(c, font=f) + tracking for c in text) - tracking
    return tracked(d, (cx - total / 2, y), text, f, fill, tracking)


def main() -> None:
    im = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(im)

    centered_tracked(d, W / 2, 78, "BUSY BAR COMMUNITY APP",
                     font(22, bold=True), ACCENT, tracking=7)

    title = "NYC Subway"
    f_title = font(104, bold=True)
    tw = d.textlength(title, font=f_title)
    d.text(((W - tw) / 2, 116), title, font=f_title, fill=INK)

    device = Image.open(DEVICE_SHOT).convert("RGBA")
    dw = 880
    dh = round(device.size[1] * dw / device.size[0])
    device = device.resize((dw, dh), Image.LANCZOS)
    im.paste(device, ((W - dw) // 2, 296), device)

    q = im.convert("P", palette=Image.ADAPTIVE, colors=256)
    q.save(OUT, optimize=True)
    print(f"{OUT}: {OUT.stat().st_size} bytes")


if __name__ == "__main__":
    main()
