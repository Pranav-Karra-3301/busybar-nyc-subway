#!/usr/bin/env python3
"""Regenerate docs/img/bullets.png + site/img/bullets.png — every route
bullet (express diamonds included) rendered LED-dot style off the real
app.py pipeline. Run after any art-pipeline change.

    ~/busybar/app/.venv/bin/python tools/make_bullets_strip.py
"""
import importlib.util
import io
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "nyc_subway_app", ROOT / "apps" / "nyc-subway" / "app.py")
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)

ORDER = ["1", "2", "3", "4", "5", "6", "6X", "7", "7X",
         "A", "C", "E", "B", "D", "F", "FX", "M", "G",
         "J", "Z", "L", "N", "Q", "R", "W", "S", "SIR"]
S = 12          # px per LED
GAP = 6         # px between bullets
PAD = 5

w = len(ORDER) * 15 * S + (len(ORDER) - 1) * GAP + PAD * 2
h = 15 * S + PAD * 2
img = Image.new("RGBA", (w, h), (5, 5, 6, 255))
draw = ImageDraw.Draw(img)

x0 = PAD
for desig in ORDER:
    art = Image.open(io.BytesIO(app.make_bullet(desig))).convert("RGBA")
    px = art.load()
    for y in range(15):
        for x in range(15):
            r, g, b, a = px[x, y]
            if not a or (r, g, b) == (0, 0, 0):
                continue
            cx, cy = x0 + x * S + S / 2, PAD + y * S + S / 2
            rad = S * 0.42
            draw.ellipse((cx - rad, cy - rad, cx + rad, cy + rad),
                         fill=(r, g, b, 255))
    x0 += 15 * S + GAP

for out in (ROOT / "docs" / "img" / "bullets.png",
            ROOT / "site" / "img" / "bullets.png"):
    img.save(out)
    print(f"wrote {out.relative_to(ROOT)} ({img.width}x{img.height})")
