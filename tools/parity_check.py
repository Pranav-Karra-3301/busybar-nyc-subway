#!/usr/bin/env python3
"""Prove the app's runtime art generation reproduces the legacy hand-tuned
assets byte-for-byte.

Compares, for N / Q / G:
  - make_bullet()      vs the 15x15 PNGs embedded in the legacy apps
  - flash anim bytes   vs the FLASH_ANIMS blobs embedded in the legacy apps
    (the real test: the pure-stdlib field/sweep/fade/RLE pipeline vs the
    original PIL + build_anim.py one)

Legacy sources default to ~/busybar/app; pass --legacy DIR to point
elsewhere. Exits non-zero on any mismatch.
"""
import argparse
import base64
import importlib.util
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_app():
    spec = importlib.util.spec_from_file_location(
        "nyc_subway_app", ROOT / "apps" / "nyc-subway" / "app.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def extract_b64(text: str, pattern: str) -> bytes:
    m = re.search(pattern, text)
    if not m:
        raise SystemExit(f"pattern not found: {pattern[:60]}")
    return base64.b64decode("".join(re.findall(r'"([^"]*)"', m.group(1))))


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
        got_bullet = app.make_bullet(route)
        ok = got_bullet == want_bullet
        print(f"bullet_{route}: {'OK' if ok else 'MISMATCH'} "
              f"({len(got_bullet)} vs {len(want_bullet)} bytes)")
        failures += not ok

        anim_re = (rf'FLASH_ANIMS = \{{[^{{}}]*?"{route}": base64\.b64decode\(\n'
                   rf'((?:\s+"[^"]*"\n?)+)\)')
        want_anim = extract_b64(src, anim_re)
        got_anim = app.anim_encode(app.flash_anim_frames(route), 72, 16)
        ok = got_anim == want_anim
        print(f"flash_{route}.anim: {'OK' if ok else 'MISMATCH'} "
              f"({len(got_anim)} vs {len(want_anim)} bytes)")
        if not ok and len(got_anim) == len(want_anim):
            diff = next(i for i, (a, b) in enumerate(zip(got_anim, want_anim))
                        if a != b)
            print(f"  first differing byte at offset {diff}")
        failures += not ok

    if failures:
        raise SystemExit(f"{failures} parity failure(s)")
    print("parity: all N/Q/G art matches the legacy apps byte-for-byte")


if __name__ == "__main__":
    main()
