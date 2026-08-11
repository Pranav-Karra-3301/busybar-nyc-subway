#!/usr/bin/env python3
"""Composite 72x16 captures into the official BUSY Bar device render with a
dotted LED-matrix look.

Geometry mirrors the busybar-apps gallery exactly (AppCard.astro's
`.device-screen`: width 93.5% of the device image, centered at x 50% /
y 56.85%). Each of the 72x16 pixels becomes a round LED dot — lit pixels get
a soft halo, unlit ones a faint dark dot so the matrix reads on the glass —
drawn 16x supersampled and Lanczos-downscaled for smooth circles.

    device_frame.py IN.png|IN.gif -o OUT.png|OUT.gif
        IN: a 72x16 capture, or a 720x160 one (10x frame dump), or a GIF of
        either size. GIFs keep their per-frame timing.
    device_frame.py --strip IN.png -o OUT.png
        No device: just re-render any small pixel-art image as LED dots on a
        rounded near-black strip (used for the bullet sheet).

Device render: docs/brand/busybar-device.png, from maxswinkels/busybar-apps
(MIT). Needs Pillow (build tool only).
"""
import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageSequence

ROOT = Path(__file__).resolve().parent.parent
DEVICE = ROOT / "docs" / "brand" / "busybar-device.png"

SS = 16            # supersample: px per LED cell while drawing
DOT_R = 0.42       # lit-dot radius, in cells
HALO_R = 0.66      # lit-dot halo radius, in cells
OFF_R = 0.34       # unlit-dot radius, in cells
OFF_COLOR = (26, 26, 29, 255)
SCREEN_W_FRAC = 0.935   # from the gallery's .device-screen CSS
SCREEN_CY_FRAC = 0.5685


def to_led_grid(im: Image.Image) -> Image.Image:
    """Accept a 72x16 or 720x160 frame; return the 72x16 RGB grid."""
    im = im.convert("RGB")
    if im.size[0] % 72 == 0 and im.size[1] % 16 == 0 and im.size[0] // 72 == im.size[1] // 16:
        if im.size != (72, 16):
            im = im.resize((72, 16), Image.NEAREST)
        return im
    raise SystemExit(f"expected a 72x16 (or 10x) frame, got {im.size}")


def dotted(grid: Image.Image, w: int, h: int) -> Image.Image:
    """Render the 72x16 grid as round LEDs on transparency at w x h."""
    gw, gh = grid.size
    big = Image.new("RGBA", (gw * SS, gh * SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(big)
    px = grid.load()

    def dot(cx, cy, r, fill):
        d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=fill)

    for y in range(gh):
        for x in range(gw):
            c = px[x, y]
            cx, cy = (x + 0.5) * SS, (y + 0.5) * SS
            if max(c) <= 8:
                dot(cx, cy, OFF_R * SS, OFF_COLOR)
            else:
                dot(cx, cy, HALO_R * SS, (*c, 70))
                dot(cx, cy, DOT_R * SS, (*c, 255))
    return big.resize((w, h), Image.LANCZOS)


def frame_on_device(grid: Image.Image) -> Image.Image:
    device = Image.open(DEVICE).convert("RGBA")
    dw, dh = device.size
    sw = round(dw * SCREEN_W_FRAC)
    sh = round(sw * grid.size[1] / grid.size[0])
    screen = dotted(grid, sw, sh)
    out = device.copy()
    left = round((dw - sw) / 2)
    top = round(dh * SCREEN_CY_FRAC - sh / 2)
    out.alpha_composite(screen, (left, top))
    return out


def strip(im: Image.Image, cell: int = 10, pad: int = 2) -> Image.Image:
    """LED-dot rendering of arbitrary pixel art on a rounded dark strip.
    Denser art than the 72x16 screen (1px letter strokes inside 15px
    bullets), so cores are bigger and halos much fainter — halo bleed is
    what smears thin strokes."""
    im = im.convert("RGBA")
    w, h = im.size
    big = Image.new("RGBA", ((w + 2 * pad) * SS, (h + 2 * pad) * SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(big)
    d.rounded_rectangle((0, 0, big.size[0] - 1, big.size[1] - 1),
                        radius=(pad + 2) * SS, fill=(10, 10, 12, 255))
    px = im.load()
    core, halo = 0.46, 0.56
    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            cx, cy = (x + pad + 0.5) * SS, (y + pad + 0.5) * SS
            if a < 32 or max(r, g, b) <= 8:
                d.ellipse((cx - OFF_R * SS, cy - OFF_R * SS,
                           cx + OFF_R * SS, cy + OFF_R * SS), fill=(22, 22, 25, 255))
            else:
                d.ellipse((cx - halo * SS, cy - halo * SS,
                           cx + halo * SS, cy + halo * SS), fill=(r, g, b, 34))
                d.ellipse((cx - core * SS, cy - core * SS,
                           cx + core * SS, cy + core * SS), fill=(r, g, b, 255))
    return big.resize(((w + 2 * pad) * cell, (h + 2 * pad) * cell), Image.LANCZOS)


def gif_transparent_p(frame: Image.Image) -> Image.Image:
    alpha = frame.split()[3]
    p = frame.convert("RGB").convert("P", palette=Image.ADAPTIVE, colors=255)
    p.paste(255, alpha.point(lambda a: 255 if a < 128 else 0))
    return p


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("input", type=Path)
    ap.add_argument("-o", "--output", type=Path, required=True)
    ap.add_argument("--strip", action="store_true",
                    help="LED strip only, no device frame")
    args = ap.parse_args()

    src = Image.open(args.input)
    if args.strip:
        strip(src).save(args.output)
    elif getattr(src, "is_animated", False):
        frames, durations = [], []
        for f in ImageSequence.Iterator(src):
            durations.append(f.info.get("duration", 100))
            frames.append(gif_transparent_p(
                frame_on_device(to_led_grid(f.convert("RGB")))))
        frames[0].save(args.output, save_all=True, append_images=frames[1:],
                       duration=durations, loop=0, transparency=255,
                       disposal=2, optimize=True)
    else:
        frame_on_device(to_led_grid(src)).save(args.output)
    print(f"{args.output}: {args.output.stat().st_size} bytes")


if __name__ == "__main__":
    main()
