#!/usr/bin/env python3
"""Capture a real 720x160 preview GIF off BUSY Bar hardware.

Runs `app.py --demo` against the bar while polling the frame-dump endpoint
(`GET /api/screen?display=0` — base64 raw 72x16 BGR24), then assembles the
frames into the 10x nearest-neighbor GIF the busybar-apps gallery requires
("must be actual emulator or hardware output").

    python3 tools/capture_preview.py --out apps/nyc-subway/preview.gif \
        [--host 10.0.4.20] [--env STATION="Canal St" --env DIRECTION=uptown]

Needs Pillow (build tool only — the app itself has no such dependency).
"""
import argparse
import base64
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "apps" / "nyc-subway" / "app.py"


def grab(host: str) -> Image.Image:
    raw = base64.b64decode(urllib.request.urlopen(
        f"http://{host}/api/screen?display=0", timeout=5).read())
    img = Image.frombytes("RGB", (72, 16), raw)
    b, g, r = img.split()[0], img.split()[1], img.split()[2]
    return Image.merge("RGB", (r, g, b))  # frame dump is BGR


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host", default="10.0.4.20")
    ap.add_argument("--out", type=Path,
                    default=ROOT / "apps" / "nyc-subway" / "preview.gif")
    ap.add_argument("--env", action="append", default=[],
                    metavar="KEY=VALUE", help="env for the demo app")
    ap.add_argument("--fps", type=float, default=10.0)
    ap.add_argument("--scale", type=int, default=10)
    args = ap.parse_args()

    env = dict(os.environ, BUSYBAR_TARGET="usb")
    for kv in args.env:
        k, _, v = kv.partition("=")
        env[k] = v

    demo = subprocess.Popen([sys.executable, str(APP), "--demo"], env=env)
    frames, stamps = [], []
    interval = 1.0 / args.fps
    try:
        time.sleep(0.6)  # connect + asset upload
        while demo.poll() is None:
            t0 = time.time()
            try:
                frames.append(grab(args.host))
                stamps.append(t0)
            except OSError:
                pass
            rest = interval - (time.time() - t0)
            if rest > 0:
                time.sleep(rest)
    finally:
        demo.wait(timeout=30)

    if len(frames) < 5:
        raise SystemExit("too few frames captured — is the bar reachable?")

    # drop leading/trailing all-black frames, keep the sequence tight
    def lit(im):
        return im.getbbox() is not None
    while frames and not lit(frames[0]):
        frames.pop(0); stamps.pop(0)
    while frames and not lit(frames[-1]):
        frames.pop(); stamps.pop()

    big = [f.resize((72 * args.scale, 16 * args.scale), Image.NEAREST)
           for f in frames]
    durations = [max(20, round((stamps[i + 1] - stamps[i]) * 1000))
                 for i in range(len(stamps) - 1)] + [800]
    big[0].save(args.out, save_all=True, append_images=big[1:],
                duration=durations, loop=0, optimize=True)
    print(f"{args.out}: {len(big)} frames, {args.out.stat().st_size} bytes")


if __name__ == "__main__":
    main()
