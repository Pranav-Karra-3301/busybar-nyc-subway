#!/usr/bin/env python3
"""Regenerate docs/img/bullets.png + site/img/bullets.png — every route
bullet (express diamonds included) through the real app.py pipeline, then
LED-dot rendered by device_frame.py --strip so it matches the gallery look.
Run after any art-pipeline or letter-tuning change.

    ~/busybar/app/.venv/bin/python tools/make_bullets_strip.py
"""
import importlib.util
import io
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "nyc_subway_app", ROOT / "apps" / "nyc-subway" / "app.py")
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)

ORDER = ["1", "2", "3", "4", "5", "6", "6X", "7", "7X",
         "A", "C", "E", "B", "D", "F", "FX", "M", "G",
         "J", "Z", "L", "N", "Q", "R", "W", "S", "SIR"]
GAP = 3  # unlit LED columns between bullets

w = len(ORDER) * 15 + (len(ORDER) - 1) * GAP
plain = Image.new("RGBA", (w, 15), (0, 0, 0, 0))
x0 = 0
for desig in ORDER:
    art = Image.open(io.BytesIO(app.make_bullet(desig))).convert("RGBA")
    plain.paste(art, (x0, 0), art)
    x0 += 15 + GAP

with tempfile.NamedTemporaryFile(suffix=".png") as tmp:
    plain.save(tmp.name)
    for out in (ROOT / "docs" / "img" / "bullets.png",
                ROOT / "site" / "img" / "bullets.png"):
        subprocess.run([sys.executable, str(ROOT / "tools" / "device_frame.py"),
                        "--strip", tmp.name, "-o", str(out)], check=True)
        size = Image.open(out).size
        print(f"wrote {out.relative_to(ROOT)} {size[0]}x{size[1]}")
