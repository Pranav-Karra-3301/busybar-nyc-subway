#!/usr/bin/env python3
"""Prove the art pipeline still reproduces the legacy hand-tuned assets —
modulo the ONE deliberate change: letters are white with the firmware's
baked drop shadow instead of black.

For N / Q / G against the legacy apps' embedded art:
  - make_bullet(): pixel-classified. The letter ink must sit on exactly the
    legacy letter pixels (shape + position parity), now white; each shadow
    pixel must equal the legacy pixel times its GLYPH_SHADOW ratio; every
    other pixel must match the legacy PNG exactly.
  - flash anim, legacy mode: flash_card(legacy=True) run through the full
    sweep/hold/fade/RLE pipeline must be BYTE-identical to the legacy blobs
    (field shading, geometry and the .anim container are all still exact).
  - flash card, white mode: may differ from legacy mode only at the letter
    and its shadow footprint.
Plus a smoke pass over the express diamonds (6X/7X/FX).

Legacy sources default to ~/busybar/app; pass --legacy DIR to point
elsewhere. Exits non-zero on any mismatch.
"""
import argparse
import base64
import importlib.util
import io
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# the offsets/sizes the legacy art was tuned at — pinned so later editor
# nudges don't shake this proof
LEGACY_OFFSETS = {"bullet": {"G": (1, 1), "Q": (0, 1)}, "flash": {}}


def load_app():
    spec = importlib.util.spec_from_file_location(
        "nyc_subway_app", ROOT / "apps" / "nyc-subway" / "app.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.LETTER_OFFSETS = LEGACY_OFFSETS
    mod.LETTER_SIZES = {"bullet": {}, "flash": {}}
    return mod


def extract_b64(text: str, pattern: str) -> bytes:
    m = re.search(pattern, text)
    if not m:
        raise SystemExit(f"pattern not found: {pattern[:60]}")
    return base64.b64decode("".join(re.findall(r'"([^"]*)"', m.group(1))))


def png_pixels(png: bytes):
    from PIL import Image
    img = Image.open(io.BytesIO(png)).convert("RGBA")
    w, h = img.size
    px = img.load()
    return [[px[x, y] for x in range(w)] for y in range(h)], w, h


def letter_geometry(app, route):
    """(ink, shadow{pos: factor}) for a bullet at the legacy offsets."""
    glyph = app.bullet_glyph(route)
    gw, gh = len(glyph[0]), len(glyph)
    dx, dy = app.letter_offset("bullet", route)
    x0, y0 = (15 - gw) // 2 + dx, (15 - gh) // 2 + dy
    inside = lambda x, y: (0 <= x < 15 and 0 <= y < 15  # noqa: E731
                           and app.DISK_MASK[y][x] == "#")
    ink = {(x0 + gx, y0 + gy)
           for gy, grow in enumerate(glyph)
           for gx, ch in enumerate(grow) if ch == "#"}
    ink = {p for p in ink if inside(*p)}
    shadow = {}
    for sdx, sdy, f in app.GLYPH_SHADOW:
        for x, y in ink:
            t = (x + sdx, y + sdy)
            if t in ink or t in shadow or not inside(*t):
                continue
            shadow[t] = f
    return ink, shadow


def check_bullet(app, route, legacy_png) -> int:
    legacy, w, h = png_pixels(legacy_png)
    new, _, _ = png_pixels(app.make_bullet(route))
    ink, shadow = letter_geometry(app, route)

    legacy_ink = {(x, y) for y in range(h) for x in range(w)
                  if legacy[y][x] == (0, 0, 0, 255)}
    bad = []
    if ink != legacy_ink:
        bad.append(f"letter shape/position drift: +{sorted(ink - legacy_ink)}"
                   f" -{sorted(legacy_ink - ink)}")
    for y in range(h):
        for x in range(w):
            l, n = legacy[y][x], new[y][x]
            if (x, y) in ink:
                want = (255, 255, 255, 255)
            elif (x, y) in shadow:
                f = shadow[(x, y)]
                want = tuple(round(v * f) for v in l[:3]) + (255,)
            else:
                want = l
            if n != want:
                bad.append(f"({x},{y}) got {n} want {want} (legacy {l})")
    for b in bad[:6]:
        print(f"    {b}")
    return len(bad)


def check_flash(app, route, legacy_blob) -> int:
    bad = 0
    got = app.anim_encode(app.flash_anim_frames(route, legacy=True), 72, 16)
    ok = got == legacy_blob
    print(f"flash_{route}.anim (legacy mode): "
          f"{'OK' if ok else 'MISMATCH'} ({len(got)} vs {len(legacy_blob)} b)")
    bad += not ok

    old_card = app.flash_card(route, legacy=True)
    new_card = app.flash_card(route)
    glyph = app.XL_GLYPHS[app.letter_for(route)]
    gw, gh = len(glyph[0]), len(glyph)
    x0, y0 = (72 - gw) // 2, (16 - gh) // 2
    ink = {(x0 + gx, y0 + gy)
           for gy, grow in enumerate(glyph)
           for gx, ch in enumerate(grow) if ch == "#"}
    allowed = set(ink)
    for dx, dy, _f in app.GLYPH_SHADOW:
        allowed |= {(x + dx, y + dy) for x, y in ink}
    stray = [(x, y) for y in range(16) for x in range(72)
             if old_card[y][x] != new_card[y][x] and (x, y) not in allowed]
    ok = not stray
    print(f"flash_{route} white mode: "
          f"{'diffs confined to letter+shadow' if ok else f'STRAY {stray[:6]}'}")
    return bad + (not ok)


def check_express(app) -> int:
    bad = 0
    for desig in app.EXPRESS_OF.values():
        px, w, h = png_pixels(app.make_bullet(desig))
        shape = ["".join("#" if px[y][x][3] else "." for x in range(w))
                 for y in range(h)]
        ok = shape == app.DIAMOND_MASK
        white = any(px[y][x][:3] == (255, 255, 255)
                    for y in range(h) for x in range(w))
        card = app.flash_card(desig)
        ok_card = any(card[y][x] == (255, 255, 255)
                      for y in range(16) for x in range(72))
        print(f"express {desig}: diamond {'OK' if ok else 'WRONG SHAPE'}, "
              f"letter {'white' if white else 'MISSING'}, "
              f"flash mark {'OK' if ok_card else 'MISSING'}")
        bad += (not ok) + (not white) + (not ok_card)
    return bad


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--legacy", type=Path,
                    default=Path.home() / "busybar" / "app")
    args = ap.parse_args()

    canal = (args.legacy / "canal_trains.py").read_text()
    g = (args.legacy / "g_trains.py").read_text()
    legacy_src = {"N": canal, "Q": canal, "G": g}

    app = load_app()
    failures = 0
    for route, src in legacy_src.items():
        want_bullet = extract_b64(
            src, rf'"{route}": base64\.b64decode\(\n((?:\s+"[^"]*"\n?)+)\)')
        n = check_bullet(app, route, want_bullet)
        print(f"bullet_{route}: "
              f"{'OK (white letter + shadow over legacy art)' if not n else f'{n} bad pixels'}")
        failures += n > 0

        anim_re = (rf'FLASH_ANIMS = \{{[^{{}}]*?"{route}": base64\.b64decode\(\n'
                   rf'((?:\s+"[^"]*"\n?)+)\)')
        failures += check_flash(app, route, extract_b64(src, anim_re))

    failures += check_express(app)
    if failures:
        raise SystemExit(f"{failures} parity failure(s)")
    print("parity: pipeline exact; letters differ only by the intended "
          "white-ink + firmware-shadow treatment")


if __name__ == "__main__":
    main()
