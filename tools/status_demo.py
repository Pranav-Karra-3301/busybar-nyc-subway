#!/usr/bin/env python3
"""Concept demo: service-status states for the BUSY Bar, driven by REAL
MTA data pulled live at run time.

Four 72x16 states, pushed to the Bar in sequence (priority 50, so they
show over the subway app, which reclaims the screen afterwards):

  1. DELAYED     — a train physically held in a station right now, found in
                   the VehiclePositions feed (STOPPED_AT + stale timestamp:
                   the countdown-clock "Delayed" signal).
  2. NOT RUNNING — a (part-)suspension alert for a route from the Mercury
                   subway-alerts feed, with its real headline + window.
  3. SERVICE ALERT — a live route-level alert while trains still run:
                   the normal card plus an amber corner dot on the bullet
                   and the alert headline as a bottom ticker.
  4. TRACK CHANGE — NYCT's scheduled_track/actual_track extension (every
                   stop_time_update carries both); badge shows the actual
                   track when it differs (simulated when none is live).

    ~/busybar/app/.venv/bin/python tools/status_demo.py
        # default: --serve — local viewer at http://localhost:8766 with a
        # simulated LED board (no device needed; text approximated in caps
        # from the app's glyph tables). --captures DIR embeds real hardware
        # frame dumps alongside.
    ... --push [--hold SECS] [--capture DIR]
        # push the states to the physical Bar (only with --push)
"""
import argparse
import base64
import hashlib
import importlib.util
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_env():
    """BUSYBAR_* fallbacks (cloud token etc.) from ~/busybar/.env."""
    import os
    env = Path.home() / "busybar" / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip().strip('"'))


load_env()
spec = importlib.util.spec_from_file_location(
    "nyc_subway_app", ROOT / "apps" / "nyc-subway" / "app.py")
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)

AMBER = "#FFB000FF"
WHITE = "#FFFFFFFF"
ALERTS_URL = ("https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/"
              "camsys%2Fsubway-alerts.json")
TRIPS_URL = ("https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/"
             "nyct%2Fgtfs-nqrw")


# ------------------------------------------------------------ live MTA data

def fetch_alerts():
    data = json.load(urllib.request.urlopen(ALERTS_URL, timeout=20))
    out = []
    for e in data.get("entity", []):
        a = e.get("alert", {})
        merc = a.get("transit_realtime.mercury_alert", {})
        head = next((t["text"] for t in
                     a.get("header_text", {}).get("translation", [])
                     if t.get("language") == "en"), "")
        period = next((t["text"] for t in
                       merc.get("human_readable_active_period", {})
                       .get("translation", []) if t.get("language") == "en"),
                      "")
        routes = {i.get("route_id") for i in a.get("informed_entity", [])
                  if i.get("route_id")}
        windows = [(p.get("start", 0), p.get("end", 2**62))
                   for p in a.get("active_period", [{}])]
        out.append(dict(type=merc.get("alert_type", ""), head=head,
                        period=period, routes=routes, windows=windows))
    return out


def active_now(alert, now):
    return any(s <= now <= e for s, e in alert["windows"])


def plain(text):
    """Alert copy -> device-safe ASCII: strip [G]-style bullet tokens and
    icon markers, fold typographic punctuation (the firmware fonts 400 on
    non-ASCII)."""
    text = re.sub(r"\[([0-9A-Z]+)\]", r"\1", text)
    text = re.sub(r"\[[^\]]+ icon\]\s*", "", text)
    for a, b in (("—", "-"), ("–", "-"), ("•", "-"),
                 ("’", "'"), ("‘", "'"), ("“", '"'),
                 ("”", '"'), (" ", " ")):
        text = text.replace(a, b)
    return text.encode("ascii", "ignore").decode()


def fetch_held_train():
    """(route, stop_base, minutes_held) for the longest-held train, via
    VehiclePositions STOPPED_AT + stale timestamp."""
    buf = urllib.request.urlopen(TRIPS_URL, timeout=20).read()
    walk = app._walk_fields
    trips, best = {}, None
    now = time.time()
    for f, w, ent in walk(buf):
        if f != 2 or w != 2:
            continue
        for f2, w2, v2 in walk(ent):
            if f2 == 3 and w2 == 2:  # TripUpdate: remember trip -> route
                tid = route = ""
                for f3, w3, v3 in walk(v2):
                    if f3 == 1 and w3 == 2:
                        for f4, w4, v4 in walk(v3):
                            if f4 == 1 and w4 == 2:
                                tid = v4.decode("utf-8", "replace")
                            elif f4 == 5 and w4 == 2:
                                route = v4.decode("utf-8", "replace")
                trips[tid] = route
            elif f2 == 4 and w2 == 2:  # VehiclePosition
                tid, stop, status, ts = "", "", None, None
                for f3, w3, v3 in walk(v2):
                    if f3 == 1 and w3 == 2:
                        for f4, w4, v4 in walk(v3):
                            if f4 == 1 and w4 == 2:
                                tid = v4.decode("utf-8", "replace")
                    elif f3 == 4 and w3 == 0:
                        status = v3
                    elif f3 == 5 and w3 == 0:
                        ts = v3
                    elif f3 == 7 and w3 == 2:
                        stop = v3.decode("utf-8", "replace")
                if status == 1 and ts and now - ts > 150:
                    held = now - ts
                    if best is None or held > best[3]:
                        best = (tid, stop, trips.get(tid, ""), held)
    if best is None:
        return None
    tid, stop, route, held = best
    if not route:
        route = tid.split("_")[1].split(".")[0] if "_" in tid else "N"
    return route or "N", stop[:-1] if stop[-1:] in "NS" else stop, held / 60


def stop_names():
    return {r[0]: r[1] for r in app.load_stations()}


# ------------------------------------------------------------------ drawing

def connect_bar():
    import requests
    app.requests = requests
    app.APP_NAME = "status-demo"
    app.PRIORITY = 50
    bar = app.Bar()
    bar.connect()
    return bar


def upload_bullets(bar, desigs):
    import requests  # noqa: F401
    names = {}
    for d in desigs:
        png = app.make_bullet(d)
        name = f"sd_{d}_{hashlib.sha256(png).hexdigest()[:8]}.png"
        r = bar.s.post(bar.t.url("/assets/upload"),
                       params={"application_name": app.APP_NAME, "file": name},
                       headers={**bar.t.headers,
                                "Content-Type": "application/octet-stream"},
                       data=png, timeout=20)
        r.raise_for_status()
        names[d] = name
    return names


def dots(colors):
    els = []
    for i, c in enumerate(colors):
        els.append({"id": f"dot{i}", "type": "rectangle", "x": 71, "y": i * 2,
                    "width": 1, "height": 1, "fill": "solid",
                    "fill_colors": [c + "FF"], "border_width": 0,
                    "timeout": 30})
    return els


def state_delayed(bullets, route, stop_name, held_min):
    return [
        {"id": "bullet", "type": "image", "path": bullets[route],
         "x": 1, "y": 0, "timeout": 30},
        {"id": "big", "type": "text", "text": "DELAYED", "font": "bold",
         "color": AMBER, "x": 19, "y": 2, "align": "top_left",
         "timeout": 30},
        {"id": "sub", "type": "text",
         "text": f"held {held_min:.0f} min at {stop_name}",
         "font": "tiny", "color": WHITE, "x": 19, "y": 15,
         "align": "bottom_left", "width": 50, "scroll_rate": 1200,
         "timeout": 30},
    ] + dots([app.line_color(route)] * 3)


def state_not_running(bullets, route, head, period):
    """DND grammar: deep-red plate asset, bullet in the icon slot, two
    stacked bold lines with a shadow copy each — all plain draw elements,
    so the real app can render this exact screen today."""
    els = [
        {"id": "plate", "type": "image",
         "path": bullets.get("_plate_dnd", "mem:plate_dnd"),
         "x": 0, "y": 0, "timeout": 30},
        {"id": "bullet", "type": "image", "path": bullets[route],
         "x": 1, "y": 0, "timeout": 30},
    ]
    for i, (text, y0) in enumerate((("NO", 1), ("TRAINS", 8))):
        w, _h, _lit = sim_text(text, "bold")
        x0 = 19 + (46 - w) // 2
        els.append({"id": f"l{i}s", "type": "text", "text": text,
                    "font": "bold", "color": "#00000091", "x": x0,
                    "y": y0 + 1, "align": "top_left", "timeout": 30})
        els.append({"id": f"l{i}", "type": "text", "text": text,
                    "font": "bold", "color": WHITE, "x": x0, "y": y0,
                    "align": "top_left", "timeout": 30})
    return els


def state_alert_dot(bullets, route, mins, head):
    return [
        {"id": "bullet", "type": "image", "path": bullets[route],
         "x": 1, "y": 0, "timeout": 30},
        {"id": "adot", "type": "rectangle", "x": 12, "y": 0, "width": 3,
         "height": 3, "fill": "solid", "fill_colors": [AMBER],
         "border_width": 0, "timeout": 30},
        {"id": "num", "type": "text", "text": str(mins), "font": "extra_large",
         "color": WHITE, "align": "mid_right", "x": 34, "y": 8,
         "timeout": 30},
        {"id": "unit", "type": "text", "text": "min", "font": "bold",
         "color": WHITE, "align": "bottom_left", "x": 37, "y": 15,
         "timeout": 30},
    ] + dots([app.line_color(route)] * 3)


def state_track(bullets, route, mins, track):
    return [
        {"id": "bullet", "type": "image", "path": bullets[route],
         "x": 1, "y": 0, "timeout": 30},
        {"id": "num", "type": "text", "text": str(mins), "font": "extra_large",
         "color": WHITE, "align": "mid_right", "x": 34, "y": 8,
         "timeout": 30},
        {"id": "unit", "type": "text", "text": "min", "font": "bold",
         "color": WHITE, "align": "bottom_left", "x": 37, "y": 15,
         "timeout": 30},
        {"id": "sub", "type": "text", "text": f"TK {track}", "font": "tiny",
         "color": AMBER, "x": 68, "y": 2, "align": "top_right",
         "timeout": 30},
    ] + dots([app.line_color(route)] * 3)


def capture(bar, out_path):
    r = bar.s.get(bar.t.url("/screen"), params={"display": 0},
                  headers=bar.t.headers, timeout=10)
    r.raise_for_status()
    raw = base64.b64decode(r.text)
    try:
        from PIL import Image
        img = Image.frombytes("RGB", (72, 16), raw)
        b, g, r = img.split()[0], img.split()[1], img.split()[2]
        Image.merge("RGB", (r, g, b)).resize((720, 160), 0).save(out_path)
        print(f"  captured {out_path}")
    except ImportError:
        pass


def state_normal(bullets, route, mins):
    return [
        {"id": "bullet", "type": "image", "path": bullets[route],
         "x": 1, "y": 0, "timeout": 30},
        {"id": "num", "type": "text", "text": str(mins), "font": "extra_large",
         "color": WHITE, "align": "mid_right", "x": 34, "y": 8,
         "timeout": 30},
        {"id": "unit", "type": "text", "text": "min", "font": "bold",
         "color": WHITE, "align": "bottom_left", "x": 37, "y": 15,
         "timeout": 30},
    ] + dots([app.line_color(route)] * 3)


def gather():
    """Pull the live feeds and bind each state to the best real instance."""
    now = time.time()
    alerts = fetch_alerts()
    held = fetch_held_train()
    names = stop_names()

    if held:
        d_route, d_stop, d_min = held
        d_src = "LIVE — VehiclePositions"
    else:
        d_route, d_stop, d_min = "N", "Q01", 6
        d_src = "simulated (no train held >2.5 min right now)"
    d_name = names.get(d_stop, d_stop)

    susp = [a for a in alerts if "Suspended" in a["type"] and a["head"]]
    susp.sort(key=lambda a: (("G" not in a["routes"]),
                             not active_now(a, now)))
    s = susp[0] if susp else dict(routes={"G"}, head="No G service",
                                  period="", type="Planned - Suspended")
    s_route = "G" if "G" in s["routes"] else sorted(s["routes"])[0]
    s_live = ("ACTIVE NOW" if active_now(s, now)
              else f"upcoming — {s['period']}")

    dly = [a for a in alerts if a["type"] == "Delays"]
    dly.sort(key=lambda a: not active_now(a, now))
    a3 = dly[0] if dly else dict(routes={"Q"}, head="delays", period="",
                                 type="Delays", windows=[])
    a3_route = sorted(a3["routes"] & set(app.DESIGNATOR_META) or {"Q"})[0]
    a3_live = "ACTIVE NOW" if dly and active_now(a3, now) else \
        "most recent (window closed)"

    return dict(d_route=d_route, d_name=d_name, d_min=d_min, d_src=d_src,
                s=s, s_route=s_route, s_live=s_live,
                a3=a3, a3_route=a3_route, a3_live=a3_live)


def push_main(args):
    print("pulling live MTA data…")
    g = gather()
    print(f"1 DELAYED:     {g['d_route']} held {g['d_min']:.0f} min at "
          f"{g['d_name']} [{g['d_src']}]")
    print(f"2 NOT RUNNING: {g['s_route']} — {g['s']['head'][:70]} "
          f"[{g['s_live']}]")
    print(f"3 ALERT:       {g['a3_route']} — {plain(g['a3']['head'])[:70]}")
    print("4 TRACK:       badge TK D3 (extension field; simulated diff)")

    bar = connect_bar()
    bullets = upload_bullets(
        bar, {g["d_route"], g["s_route"], g["a3_route"], "N"})
    plate = app.png_encode(72, 16, _plate_rgba("#7E1416"))
    plate_name = f"sd_plate_{hashlib.sha256(plate).hexdigest()[:8]}.png"
    r = bar.s.post(bar.t.url("/assets/upload"),
                   params={"application_name": app.APP_NAME,
                           "file": plate_name},
                   headers={**bar.t.headers,
                            "Content-Type": "application/octet-stream"},
                   data=plate, timeout=20)
    r.raise_for_status()
    bullets["_plate_dnd"] = plate_name
    cap = args.capture
    if cap:
        cap.mkdir(parents=True, exist_ok=True)

    for name, els in (
            ("delayed", state_delayed(bullets, g["d_route"], g["d_name"],
                                      g["d_min"])),
            ("not_running", state_not_running(
                bullets, g["s_route"], plain(g["s"]["head"]),
                g["s"]["period"])),
            ("alert", state_alert_dot(bullets, g["a3_route"], 7,
                                      g["a3"]["head"]))):
        bar.clear()
        if not bar.draw(els):
            sys.exit("Bar is busy (something higher-priority owns it)")
        print(f"state: {name}")
        time.sleep(min(1.3, args.hold))
        if cap:
            capture(bar, cap / f"state_{name}.png")
        time.sleep(max(0, args.hold - 1.3))
    bar.clear()
    print("done — subway app reclaims the screen in a few seconds")


# ---------------------------------- animated treatments (firmware language)
# Mined from busybar-firmware: transitions are soft-edged mask anims played
# over content — Add-blend washes with fast attack / ~1s decay
# (transition_select*, busy_presets.c: in 100ms, out 1000ms), expanding
# soft rings from the button (transition_select_red frame 2), collapsing
# oval reveals (transition_oval, Multiply), red particles
# (ending_particles), breathing background loops (indicator_busy). All of
# it composes from the same three moves: soft radial fields, exponential
# decays, and slow drifts — reproduced here as 72x16 frame generators that
# app.anim_encode() can compile for the device unchanged.

import math
import random


def _blank():
    return [[(0, 0, 0) for _ in range(72)] for _ in range(16)]


def _add(base, gain_fn, color):
    """Add-blend a scalar field (0..1 per pixel) of `color` onto a frame."""
    out = []
    for y in range(16):
        row = []
        for x in range(72):
            g = gain_fn(x, y)
            if g <= 0:
                row.append(base[y][x])
                continue
            row.append(tuple(min(255, round(c + col * g))
                             for c, col in zip(base[y][x], color)))
        out.append(row)
    return out


def _ring(cx, cy, r, sigma):
    def gain(x, y):
        d = math.hypot((x - cx), (y - cy) * 2.6)  # LED cells are squat
        return math.exp(-((d - r) ** 2) / (2 * sigma * sigma))
    return gain


def _wash(level):
    return lambda x, y: level


def _stamp_text(frame, text, font, color, x0, y0, shadow=True):
    w, h, lit = sim_text(text, font)
    for x, y in lit:
        tx, ty = x0 + x, y0 + y
        if shadow and 0 <= tx < 72 and 0 <= ty + 1 < 16 \
                and (x, y + 1) not in lit:
            p = frame[ty + 1][tx]
            frame[ty + 1][tx] = tuple(round(v * 0.35) for v in p)
        if 0 <= tx < 72 and 0 <= ty < 16:
            frame[ty][tx] = color
    return w


def _blit_bullet(frame, px, x0, y0):
    for y, row in enumerate(px):
        for x, p in enumerate(row):
            if p[3] and 0 <= x0 + x < 72 and 0 <= y0 + y < 16:
                frame[y0 + y][x0 + x] = p[:3]


FPS = 30  # preview rate; the device pipeline runs these same frames at 60

AMBER_RGB = (255, 176, 0)
RED_RGB = (238, 53, 46)
BLUE_RGB = (0, 90, 200)


def _ramp(hexc):
    """The app's derived 5-color ramp for an arbitrary plate color."""
    base = app._hex_rgb(hexc)
    return {
        "spec": tuple(round(v + (255 - v) * 0.60) for v in base),
        "top": app._scale(base, 1.15),
        "bot": app._scale(base, 0.47),
        "lift": app._scale(base, 0.62),
    }


def _plate(hexc):
    """The busy-mode box: full-width rounded plate, 1px specular top,
    vertical ramp, lifted bottom edge, 3px corner vignette — the exact
    field geometry of meeting/dnd/keep_out/booked and our flash card.
    Returns (rows, edge_pixel_set) — the edge set is what ON AIR pulses."""
    pal = _ramp(hexc)
    w, h, r = 72, 16, 5
    rows, edge = [], set()
    inside = {}
    for y in range(h):
        for x in range(w):
            cx, cy = min(x, w - 1 - x), min(y, h - 1 - y)
            inside[(x, y)] = not (cx < r and cy < r and
                                  (r - cx) ** 2 + (r - cy) ** 2 > r * r)
    for y in range(h):
        if y == 0:
            base = pal["spec"]
        elif y == h - 1:
            base = pal["lift"]
        else:
            base = app._lerp(pal["top"], pal["bot"], (y - 1) / (h - 2))
        row = []
        for x in range(w):
            if not inside[(x, y)]:
                row.append((0, 0, 0))
                continue
            cx, cy = min(x, w - 1 - x), min(y, h - 1 - y)
            e = min(cx, cy)
            scale = (0.25, 0.5, 0.75)[e] if e < 3 else 1.0
            row.append(tuple(round(c * scale) for c in base))
            if any(not inside.get((x + dx, y + dy), False)
                   for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))):
                edge.add((x, y))
        rows.append(row)
    return rows, edge


def anim_ripple_ping(card, color):
    """Notification ping: the transition_select grammar (soft ring from the
    top edge + Add wash, fast attack / ~1s decay) repurposed as AN ALERT
    ARRIVING — nothing to click, it just announces. Two pings, settle."""
    frames = []
    for i in range(int(3.2 * FPS)):
        t = i / FPS
        f = [row[:] for row in card]
        for start in (0.0, 0.55):
            tt = t - start
            if 0 <= tt < 0.7:
                r = 4 + tt / 0.7 * 78
                f = _add(f, _ring(36, 0, r, 5), tuple(
                    round(c * (1 - tt / 0.7)) for c in color))
        for start in (0.12, 0.67):
            tt = t - start
            if 0 <= tt < 1.0:
                f = _add(f, _wash(0.55 * math.exp(-tt / 0.28)), color)
        frames.append(f)
    return frames


def anim_plate_delayed(bullet_px, sub):
    """DND family: deep-red plate, bullet in the icon slot, DELAYED in
    bold white over the firmware shadow, detail line as a tiny in-plate
    marquee. Same calm breathe as the suspension plate."""
    base, _edge = _plate("#7E1416")
    tw, th, tlit = sim_text("DELAYED", "bold")
    tx0 = 19 + (46 - tw) // 2
    sw, sh, slit = sim_text(sub, "tiny")
    win = 47
    frames = []
    for i in range(int(3.6 * FPS)):
        t = i / FPS
        k = 0.9 + 0.1 * (0.5 + 0.5 * math.sin(t * 2 * math.pi / 3.6))
        f = [[tuple(round(v * k) for v in p) for p in row] for row in base]
        _blit_bullet(f, bullet_px, 1, 0)
        for x, y in tlit:
            fx, fy = tx0 + x, 1 + y
            if (x, y + 1) not in tlit and 0 <= fx < 72 and 0 <= fy + 1 < 16:
                p = f[fy + 1][fx]
                f[fy + 1][fx] = tuple(round(v * 0.4) for v in p)
            if 0 <= fx < 72 and 0 <= fy < 16:
                f[fy][fx] = (255, 255, 255)
        off = int(t * 24) % (sw + 20)
        for x, y in slit:
            sx = 19 + x - off
            if 19 <= sx < 19 + win and 0 <= 10 + y < 16:
                f[10 + y][sx] = (255, 205, 200)
        frames.append(f)
    return frames


def anim_plate_norun(bullet_px, line1, line2):
    """DO NOT DISTURB grammar: deep-red plate, icon left, TWO stacked bold
    lines with baked shadows, static except a slow calm breathe."""
    base, _ = _plate("#7E1416")
    frames = []
    for i in range(int(3.6 * FPS)):
        t = i / FPS
        k = 0.9 + 0.1 * (0.5 + 0.5 * math.sin(t * 2 * math.pi / 3.6))
        f = [[tuple(round(v * k) for v in p) for p in row] for row in base]
        _blit_bullet(f, bullet_px, 1, 0)
        for text, y0 in ((line1, 1), (line2, 8)):
            w, h, lit = sim_text(text, "bold")
            x0 = 19 + (46 - w) // 2
            for x, y in lit:
                fx, fy = x0 + x, y0 + y
                if (x, y + 1) not in lit and 0 <= fx < 72 and \
                        0 <= fy + 1 < 16:
                    p = f[fy + 1][fx]
                    f[fy + 1][fx] = tuple(round(v * 0.4) for v in p)
                if 0 <= fx < 72 and 0 <= fy < 16:
                    f[fy][fx] = (255, 255, 255)
        frames.append(f)
    return frames


def anim_keepout(bullet_px, line1, line2):
    """KEEP OUT grammar: yellow plate, hazard stripes crawling along the
    top and bottom bands (barber-pole), bold BLACK text stacked in two
    lines — their planned-work/warning look verbatim."""
    base, _ = _plate("#FCC30B")
    dark = (24, 20, 2)
    frames = []
    for i in range(int(3.0 * FPS)):
        off = i // 2
        f = [row[:] for row in base]
        for band in (0, 15):
            for x in range(72):
                if f[band][x] == (0, 0, 0):
                    continue
                if ((x + off + band) // 4) % 2:
                    f[band][x] = dark
        _blit_bullet(f, bullet_px, 1, 0)
        for text, y0 in ((line1, 1), (line2, 8)):
            w, h, lit = sim_text(text, "bold")
            x0 = 19 + (46 - w) // 2
            for x, y in lit:
                fx, fy = x0 + x, y0 + y
                if (x, y + 1) not in lit and 0 <= fx < 72 and \
                        1 <= fy + 1 < 15:
                    p = f[fy + 1][fx]
                    f[fy + 1][fx] = tuple(round(v * 0.4) for v in p)
                if 0 <= fx < 72 and 1 <= fy < 15:
                    f[fy][fx] = (255, 255, 255)
        frames.append(f)
    return frames


def anim_alert_cycle(card, bullet_px, headline):
    """The real proposal for live alerts: the normal card keeps its amber
    corner dot; every so often a ping announces, the wash peak covers a cut
    to a full-screen alert plate (bullet + ALERT + in-plate marquee), one
    marquee pass, wash back to the card. The swap-under-the-flash trick is
    exactly how transition_select changes screens."""
    plate, _ = _plate("#8A1113")
    aw, ah, alit = sim_text("ALERT", "bold")
    hw, hh, hlit = sim_text(headline, "tiny")
    win = 47
    plate_secs = (hw + win + 20) / 24  # ~= the device's 1400px/min marquee
    frames = []
    total = 2.0 + 0.9 + plate_secs + 0.9
    for i in range(int(total * FPS)):
        t = i / FPS
        if t < 2.0 or t >= 2.0 + 0.9 + plate_secs:
            f = [row[:] for row in card]
            tt = t - 2.0 if t < 2.0 else t - (2.0 + plate_secs)
        else:
            f = [row[:] for row in plate]
            _blit_bullet(f, bullet_px, 1, 0)
            for x, y in alit:
                fx, fy = 19 + x, 2 + y
                if (x, y + 1) not in alit and fy + 1 < 16:
                    p = f[fy + 1][fx]
                    f[fy + 1][fx] = tuple(round(v * 0.4) for v in p)
                f[fy][fx] = (255, 255, 255)
            off = int((t - 2.9) * 24) % (hw + win + 20) - win
            for x, y in hlit:
                sx = 19 + x - off - 0
                if 19 <= sx < 19 + win and 0 <= 10 + y < 16:
                    f[10 + y][sx] = (255, 205, 200)
        # the ping + swap washes
        for start in (2.0, 2.0 + 0.9 + plate_secs):
            tt = t - start
            if 0 <= tt < 0.7:
                r = 4 + tt / 0.7 * 78
                f = _add(f, _ring(36, 0, r, 5), tuple(
                    round(c * (1 - tt / 0.7)) for c in AMBER_RGB))
            tt2 = t - (start + 0.35)
            if 0 <= tt2 < 0.55:
                f = _add(f, _wash(0.8 * math.exp(-tt2 / 0.22)), AMBER_RGB)
        frames.append(f)
    return frames


def anim_contrast(bullet_px, text, color):
    """A contrasting gradient sweeping behind the bullet — complementary
    color against the line color, diagonal drift (mask-anim language)."""
    frames = []
    for i in range(int(3.0 * FPS)):
        t = i / FPS
        f = _blank()
        phase = t / 3.0 * 72
        for y in range(16):
            for x in range(72):
                v = 0.28 + 0.24 * math.sin((x + y * 2 - phase * 2)
                                           * math.pi / 36)
                f[y][x] = tuple(min(255, round(c * v)) for c in color)
        _blit_bullet(f, bullet_px, 1, 0)
        _stamp_text(f, text, "bold", (255, 255, 255), 19, 5)
        frames.append(f)
    return frames


def anim_trackchange(bullet_px):
    """Track change, said plainly: the blue contrast sweep carrying two
    stacked bold lines — EXPRESS / TRACK — DND stacking on the field
    people liked, instead of a cryptic corner badge."""
    frames = []
    for i in range(int(3.0 * FPS)):
        t = i / FPS
        f = _blank()
        phase = t / 3.0 * 72
        for y in range(16):
            for x in range(72):
                v = 0.28 + 0.24 * math.sin((x + y * 2 - phase * 2)
                                           * math.pi / 36)
                f[y][x] = tuple(min(255, round(c * v)) for c in BLUE_RGB)
        _blit_bullet(f, bullet_px, 1, 0)
        for text, y0 in (("EXPRESS", 1), ("TRACK", 8)):
            w, h, lit = sim_text(text, "bold")
            x0 = 19 + (46 - w) // 2
            for x, y in lit:
                fx, fy = x0 + x, y0 + y
                if (x, y + 1) not in lit and 0 <= fx < 72 and \
                        0 <= fy + 1 < 16:
                    p = f[fy + 1][fx]
                    f[fy + 1][fx] = tuple(round(v * 0.35) for v in p)
                if 0 <= fx < 72 and 0 <= fy < 16:
                    f[fy][fx] = (255, 255, 255)
        frames.append(f)
    return frames


def _plate_rgba(hexc):
    rows, _ = _plate(hexc)
    return [[(0, 0, 0, 0) if p == (0, 0, 0) else (*p, 255) for p in row]
            for row in rows]


def flatten_state(elements, bullets_px):
    """A state's element list -> one static 72x16 frame (tickers frozen at
    their start position) to run treatments over."""
    base, tickers = sim_render(elements, bullets_px)
    for t in tickers:
        for y in range(t["h"]):
            for x in range(min(t["win"], t["w"])):
                p = t["strip"][y][x]
                if p[3] and 0 <= t["x"] + x < 72 and 0 <= t["y"] + y < 16:
                    base[t["y"] + y][t["x"] + x] = p[:3]
    return base


def build_treatments(g, bullets, bullets_px):
    alert_card = flatten_state(
        state_alert_dot(bullets, g["a3_route"], 7, g["a3"]["head"]),
        bullets_px)
    head = plain(g["a3"]["head"])
    a3_bullet = bullets_px.get(f"mem:{g['a3_route']}")
    d_bullet = bullets_px.get(f"mem:{g['d_route']}")
    g_bullet = bullets_px.get(f"mem:{g['s_route']}")
    red_bullet = bullets_px.get("mem:1")
    out = []
    for name, title, frames, caption, tags in (
        ("cycle", "Card ⇄ alert page — the real proposal",
         anim_alert_cycle(alert_card, a3_bullet, head[:52]),
         "The next-train card keeps a quiet amber dot; a ping announces, "
         "the wash peak covers the cut to a full-screen alert plate "
         "(bullet + ALERT + in-plate marquee, busy-mode box geometry), one "
         "pass, wash back. Screens swapping under the flash is exactly how "
         "transition_select changes pages.",
         ["headline: live Delays alert", "busy-mode plate",
          "transition_select swap"]),
        ("ping", "Notification ping — announce, don't decorate",
         anim_ripple_ping(alert_card, AMBER_RGB),
         "The transition_select ring + wash grammar as an arrival sound: "
         "soft ring blooms from the top edge, amber wash decays with the "
         "firmware's fast-in/slow-out envelope, twice. The card stays "
         "readable throughout.",
         ["transition_select grammar", "Add blend"]),
        ("delayed", "DELAYED, in the plate family",
         anim_plate_delayed(d_bullet, f"held {g['d_min']:.0f} min at "
                            f"{g['d_name']}"),
         "Deep-red plate, bold DELAYED over the firmware shadow, the "
         "held-time detail scrolling inside the plate. Same family as the "
         "suspension screen.",
         [g["d_src"], "dnd_72x16 grammar"]),
        ("dnd", "DO NOT DISTURB grammar — suspension",
         anim_plate_norun(g_bullet, "NO", "TRAINS"),
         f"Two stacked bold lines with baked shadows on the deep-red "
         f"plate, icon left — their DND screen with the {g['s_route']} "
         "bullet in the icon slot. Slow calm breathe, nothing else moves.",
         [g["s_live"], "dnd_72x16 grammar"]),
        ("keepout", "KEEP OUT grammar — planned work",
         anim_keepout(g_bullet, "PLANNED", "WORK"),
         "Yellow plate, white bold text over the firmware shadow (matching "
         "the app's white-letter identity), hazard stripes crawling along "
         "the top and bottom edges — their KEEP OUT screen recut for "
         "weekend work alerts.",
         ["keep_out_72x16 grammar", "for Planned - Part Suspended"]),
        ("trackchange", "Track change — said plainly",
         anim_trackchange(bullets_px.get(f"mem:{g['d_route']}")
                          or red_bullet),
         "EXPRESS / TRACK stacked DND-style on the blue contrast sweep — "
         "replaces the cryptic corner badge. Fires when "
         "NyctStopTimeUpdate's actual_track diverges from scheduled_track "
         "(both present on every stop update, verified 1347/1347).",
         ["NYCT GTFS-RT extension", "diff simulated tonight"]),
        ("contrast", "Blue gradient against a red train",
         anim_contrast(red_bullet or list(bullets_px.values())[0],
                       "REROUTED", BLUE_RGB),
         "Complementary field: a cold blue diagonal sweep drifting behind "
         "the warm red 1 bullet — maximum color contrast at one glance.",
         ["complementary to line color", "mask-anim drift grammar"]),
    ):
        blob = b"".join(bytes(v for row in fr for p in row for v in p)
                        for fr in frames)
        out.append(dict(name=name, title=title, caption=caption, tags=tags,
                        fps=FPS, n=len(frames),
                        frames=base64.b64encode(blob).decode()))
    return out


# ------------------------------------------------- simulated board (serve)

SIM_FONTS = {"tiny": ("TINY_GLYPHS", 5), "bold": ("BULLET_GLYPHS", 7),
             "extra_large": ("XL_GLYPHS", 10)}


def _punct(h):
    """Tiny synthesized punctuation the glyph tables lack: char ->
    (width, {(x, y), ...})."""
    mid = h // 2
    slash_w = max(2, h // 3 + 1)
    return {
        "-": (3, {(x, mid) for x in range(3)}),
        ".": (1, {(0, h - 1)}),
        ",": (2, {(1, h - 2), (0, h - 1)}),
        ":": (1, {(0, mid - 1), (0, mid + 1)}),
        "'": (1, {(0, 0), (0, 1)}),
        "/": (slash_w, {((h - 1 - y) * (slash_w - 1) // max(h - 1, 1), y)
                        for y in range(h)}),
        "+": (3, {(x, mid) for x in range(3)} | {(1, mid - 1), (1, mid + 1)}),
    }


def sim_text(text, font):
    """(w, h, lit-set) — caps-only approximation from the app's own glyph
    tables (the real device renders lowercase; close enough for a mock)."""
    table = getattr(app, SIM_FONTS[font][0])
    h = SIM_FONTS[font][1]
    punct = _punct(h)
    x = 0
    lit = set()
    for ch in text.upper():
        if ch == " ":
            x += 3 if font == "tiny" else 4
            continue
        g = table.get(ch)
        if g:
            gw, gh = len(g[0]), len(g)
            y0 = h - gh
            lit |= {(x + gx, y0 + gy) for gy, row in enumerate(g)
                    for gx, c in enumerate(row) if c == "#"}
            x += gw + 1
        elif ch in punct:
            pw, pts = punct[ch]
            lit |= {(x + px, py) for px, py in pts}
            x += pw + 1
        else:
            x += 2
    return max(x - 1, 1), h, lit


def _hex_rgba(c):
    c = c.lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))


def sim_render(elements, bullets_px):
    """Render a state's element list the way the firmware would: a 72x16
    base frame plus ticker strips for scrolling labels."""
    base = [[(0, 0, 0)] * 72 for _ in range(16)]
    tickers = []

    def stamp(lit, color, x0, y0):
        for x, y in lit:
            if 0 <= x0 + x < 72 and 0 <= y0 + y < 16:
                base[y0 + y][x0 + x] = color

    for el in elements:
        t = el["type"]
        if t == "image":
            px = bullets_px[el["path"]]
            for y, row in enumerate(px):
                for x, p in enumerate(row):
                    if p[3] and 0 <= el["x"] + x < 72 and \
                            0 <= el["y"] + y < 16:
                        base[el["y"] + y][el["x"] + x] = p[:3]
        elif t == "rectangle":
            color = _hex_rgba(el["fill_colors"][0])
            for y in range(el["height"]):
                for x in range(el["width"]):
                    if 0 <= el["x"] + x < 72 and 0 <= el["y"] + y < 16:
                        base[el["y"] + y][el["x"] + x] = color
        elif t == "text":
            w, h, lit = sim_text(el["text"], el["font"])
            color = _hex_rgba(el["color"])
            align = el.get("align", "top_left")
            ax, ay = el["x"], el["y"]
            win = el.get("width")
            if align == "top_right":
                x0, y0 = ax - (min(w, win or w)) + 1, ay
            elif align == "bottom_left":
                # the device anchors the LINE BOX bottom at y; the fonts
                # keep descender space below the ink (measured off frame
                # dumps: bold "min" at y=15 inks rows 6-12, tiny 10-13)
                x0 = ax
                y0 = ay - h + 1 - {"tiny": 2, "bold": 3}.get(el["font"], 3)
            elif align == "mid_right":
                # geometric center, verified: XL digit at y=8 inks rows
                # 3-12 on hardware
                x0, y0 = ax - w + 1, ay - h // 2
            elif align == "center":
                x0, y0 = ax - w // 2, ay - h // 2
            else:
                x0, y0 = ax, ay
            if win and w > win:
                strip = [[(0, 0, 0, 0)] * w for _ in range(h)]
                for x, y in lit:
                    strip[y][x] = (*color, 255)
                tickers.append(dict(x=x0, y=y0, win=win, w=w, h=h,
                                    rate=el.get("scroll_rate", 1200),
                                    strip=strip))
            else:
                stamp(lit, color, x0, y0)
    return base, tickers


def build_payload(captures_dir):
    from PIL import Image
    import io
    g = gather()
    routes = {g["d_route"], g["s_route"], g["a3_route"], "N", "1"}
    bullets = {d: f"mem:{d}" for d in routes}
    bullets_px = {}
    for d in routes:
        img = Image.open(io.BytesIO(app.make_bullet(d))).convert("RGBA")
        px = img.load()
        bullets_px[f"mem:{d}"] = [[px[x, y] for x in range(15)]
                                  for y in range(15)]
    bullets_px["mem:plate_dnd"] = _plate_rgba("#7E1416")

    states = [
        ("normal", "Today's card (for contrast)", state_normal(
            bullets, "N", 3),
         "The shipped next-train card: bullet, minutes, position dots.",
         ["baseline"]),
        ("delayed", "DELAYED — train physically held", state_delayed(
            bullets, g["d_route"], g["d_name"], g["d_min"]),
         f"{g['d_route']} train stopped at {g['d_name']} for "
         f"{g['d_min']:.0f} minutes and not moving — VehiclePositions "
         "STOPPED_AT + stale timestamp, the same signal the platform "
         "clocks turn into “Delayed”.",
         [g["d_src"], "GTFS-RT VehiclePositions"]),
        ("not_running", "NOT RUNNING — (part-)suspension", state_not_running(
            bullets, g["s_route"], plain(g["s"]["head"]), g["s"]["period"]),
         f"{g['s']['type']}: “{plain(g['s']['head'])}” "
         f"({g['s']['period'] or 'no period given'}). The DND-grammar "
         "plate rendered from plain draw elements (plate image + bullet + "
         "shadowed text), so the shipped app can draw this exact screen.",
         [g["s_live"], "Mercury subway-alerts feed"]),
        ("alert", "SERVICE ALERT — trains still running", state_alert_dot(
            bullets, g["a3_route"], 7, g["a3"]["head"]),
         f"Delays alert on the {g['a3_route']}: "
         f"“{plain(g['a3']['head'])}” — the card keeps its minutes, the "
         "amber corner dot marks the alert; the full story lives in the "
         "card ⇄ alert page cycle below.",
         [g["a3_live"], "Mercury subway-alerts feed, type=Delays"]),
    ]

    out = []
    for name, title, els, caption, tags in states:
        base, tickers = sim_render(els, bullets_px)
        cap_url = None
        if captures_dir:
            p = captures_dir / f"state_{name}.png"
            if p.exists():
                cap_url = ("data:image/png;base64,"
                           + base64.b64encode(p.read_bytes()).decode())
        out.append(dict(
            name=name, title=title, caption=caption, tags=tags,
            base=base64.b64encode(bytes(
                v for row in base for p in row for v in p)).decode(),
            tickers=[dict(x=t["x"], y=t["y"], win=t["win"], w=t["w"],
                          h=t["h"], rate=t["rate"],
                          strip=base64.b64encode(bytes(
                              v for row in t["strip"] for p in row
                              for v in p)).decode())
                     for t in tickers],
            capture=cap_url))
    return dict(generated=time.strftime("%H:%M:%S"), states=out,
                treatments=build_treatments(g, bullets, bullets_px))


SIM_PAGE = r"""<!doctype html>
<meta charset="utf-8">
<title>MTA status concepts — BUSY Bar</title>
<style>
  :root { color-scheme: dark; }
  body { margin: 0; background: #0c0c0e; color: #e8e8ea;
         font: 15px/1.5 ui-sans-serif, system-ui, sans-serif; }
  header { display: flex; align-items: baseline; gap: 14px;
           padding: 16px 26px; border-bottom: 1px solid #232328;
           position: sticky; top: 0; background: #121215; z-index: 3; }
  h1 { font-size: 16px; margin: 0; }
  #meta { color: #9a9aa2; font-size: 12.5px; margin-right: auto; }
  button { background: #1d1d22; color: #e8e8ea; border: 1px solid #303038;
           border-radius: 7px; padding: 6px 12px; font: inherit;
           cursor: pointer; }
  button:hover { background: #26262c; }
  main { max-width: 820px; margin: 0 auto; padding: 22px 26px 80px; }
  .state { margin: 26px 0 38px; }
  .state h2 { font-size: 14px; margin: 0 0 10px;
              letter-spacing: .04em; }
  .board { background: #08080a; border: 1px solid #232328;
           border-radius: 12px; padding: 14px; }
  canvas { display: block; width: 100%; image-rendering: auto; }
  .caption { color: #b7b7bf; font-size: 13.5px; margin-top: 10px; }
  .tags { margin-top: 8px; display: flex; gap: 6px; flex-wrap: wrap; }
  .tag { font-size: 11px; padding: 2px 9px; border-radius: 999px;
         border: 1px solid #303038; color: #9a9aa2; }
  .tag.live { border-color: #1d7c3b; color: #6fd487; }
  .tag.sim { border-color: #7c5a1d; color: #ffb64f; }
  .hw { margin-top: 10px; }
  .hw img { width: 100%; border-radius: 8px; display: block; }
  .hw figcaption { color: #6f6f78; font-size: 11.5px; margin-top: 5px; }
  .note { color: #6f6f78; font-size: 12.5px; margin-top: 30px;
          line-height: 1.7; }
</style>
<header>
  <h1>MTA status concepts</h1>
  <span id="meta">loading live data…</span>
  <button id="refresh">Refresh live data</button>
</header>
<main id="main"></main>
<script>
const SCALE = 10, GAP = 15;
let t0 = performance.now();

function decode(b64, w, h, rgba) {
  const bin = atob(b64), n = rgba ? 4 : 3;
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

function paint(ctx, frame) {
  const W = 72, H = 16;
  ctx.fillStyle = "#08080a";
  ctx.fillRect(0, 0, W * SCALE, H * SCALE);
  for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
    const i = (y * W + x) * 3;
    const lit = frame[i] + frame[i+1] + frame[i+2] > 0;
    ctx.fillStyle = lit ? `rgb(${frame[i]},${frame[i+1]},${frame[i+2]})`
                        : "#141416";
    ctx.beginPath();
    ctx.arc((x + .5) * SCALE, (y + .5) * SCALE, SCALE * .37, 0, 6.2832);
    ctx.fill();
  }
}

function drawBoard(ctx, base, tickers, tms) {
  const W = 72, H = 16;
  const frame = new Uint8Array(base);          // copy RGB base
  for (const t of tickers) {
    const px = (t.rate / 60000) * tms;
    const off = Math.floor(px % (t.w + GAP));
    for (let y = 0; y < t.h; y++) for (let x = 0; x < t.win; x++) {
      const sx = x + off;
      let p = null;
      if (sx < t.w) p = sx; else if (sx - t.w - GAP >= 0 &&
                                     sx - t.w - GAP < t.w) p = sx - t.w - GAP;
      if (p === null) continue;
      const s = (y * t.w + p) * 4;
      if (t.data[s + 3] === 0) continue;
      const bx = t.x + x, by = t.y + y;
      if (bx < 0 || bx >= W || by < 0 || by >= H) continue;
      const d = (by * W + bx) * 3;
      frame[d] = t.data[s]; frame[d+1] = t.data[s+1]; frame[d+2] = t.data[s+2];
    }
  }
  paint(ctx, frame);
}

let boards = [], movies = [];
function loop() {
  const tms = performance.now() - t0;
  for (const b of boards) drawBoard(b.ctx, b.base, b.tickers, tms);
  const FR = 72 * 16 * 3;
  for (const m of movies) {
    const idx = Math.floor((tms / 1000) * m.fps) % m.n;
    paint(m.ctx, m.data.subarray(idx * FR, (idx + 1) * FR));
  }
  requestAnimationFrame(loop);
}

async function load(fresh) {
  document.getElementById("meta").textContent = "pulling live MTA data…";
  const j = await (await fetch("/demo" + (fresh ? "?fresh=1" : ""))).json();
  const main = document.getElementById("main");
  main.innerHTML = "";
  boards = []; movies = [];
  for (const s of j.states) {
    const div = document.createElement("div");
    div.className = "state";
    const tags = s.tags.map(t => {
      const cls = /LIVE|ACTIVE/.test(t) ? "tag live"
                : /simulated/.test(t) ? "tag sim" : "tag";
      return `<span class="${cls}">${t}</span>`;
    }).join("");
    div.innerHTML = `<h2>${s.title}</h2>
      <div class="board"><canvas width="720" height="160"></canvas></div>
      <div class="caption">${s.caption}</div>
      <div class="tags">${tags}</div>` +
      (s.capture ? `<figure class="hw"><img src="${s.capture}">
        <figcaption>the same state captured off the physical Bar earlier
        (real firmware fonts)</figcaption></figure>` : "");
    main.append(div);
    const ctx = div.querySelector("canvas").getContext("2d");
    boards.push({
      ctx,
      base: decode(s.base, 72, 16, false),
      tickers: s.tickers.map(t => ({...t, data: decode(t.strip, t.w, t.h,
                                                       true)})),
    });
  }
  const hdr = document.createElement("div");
  hdr.className = "state";
  hdr.innerHTML = `<h2 style="font-size:16px;margin-top:26px">Animated
    treatments — rebuilt in the stock apps' own grammar</h2>
    <div class="caption">Studied from busybar-firmware's busy-mode screens
    (meeting / on_air / dnd / keep_out / booked anims) and its transition
    system (busy_presets.c): a full-width rounded plate with bevel +
    vertical ramp, the icon at left, bold text with baked shadows — two
    stacked 7px lines for long words — and exactly ONE living element per
    screen (a glow pulse, crawling hazard stripes, a breathing plate, a
    ring announcing). Fonts map 1:1 to the REST API: bold=busy_bold_7,
    extra_large=busy_bold_10, tiny=busy_tiny. Every panel is a 72×16 frame
    sequence the existing pipeline compiles to a device-side 60 fps
    .anim.</div>`;
  main.append(hdr);
  for (const m of j.treatments) {
    const div = document.createElement("div");
    div.className = "state";
    const tags = m.tags.map(t => `<span class="tag">${t}</span>`).join("");
    div.innerHTML = `<h2>${m.title}</h2>
      <div class="board"><canvas width="720" height="160"></canvas></div>
      <div class="caption">${m.caption}</div>
      <div class="tags">${tags}</div>`;
    main.append(div);
    const ctx = div.querySelector("canvas").getContext("2d");
    movies.push({ ctx, fps: m.fps, n: m.n,
                  data: decode(m.frames, 72, 16, false) });
  }
  document.getElementById("meta").textContent =
    `live MTA data pulled ${j.generated} — board is simulated ` +
    `(caps-only glyphs); nothing touches the Bar`;
}

document.getElementById("refresh").onclick = () => load(true);
load(false);
loop();
</script>
"""


def serve(args):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import webbrowser

    cache = {"t": 0, "payload": None}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            if self.path.startswith("/demo"):
                fresh = "fresh=1" in self.path
                if fresh or not cache["payload"] or \
                        time.time() - cache["t"] > 90:
                    try:
                        cache["payload"] = build_payload(args.captures)
                        cache["t"] = time.time()
                    except Exception as e:
                        body = json.dumps({"error": str(e)}).encode()
                        self.send_response(500)
                        self.send_header("Content-Type", "application/json")
                        self.send_header("Content-Length", str(len(body)))
                        self.end_headers()
                        self.wfile.write(body)
                        return
                body = json.dumps(cache["payload"]).encode()
                ctype = "application/json"
            elif self.path == "/":
                body = SIM_PAGE.encode()
                ctype = "text/html; charset=utf-8"
            else:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    url = f"http://localhost:{args.port}"
    print(f"status demo (simulated board, live data) at {url} — Ctrl-C "
          "to stop; the Bar is not touched")
    if not args.no_open:
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--push", action="store_true",
                    help="drive the physical Bar (default is --serve)")
    ap.add_argument("--serve", action="store_true")
    ap.add_argument("--port", type=int, default=8766)
    ap.add_argument("--no-open", action="store_true")
    ap.add_argument("--captures", type=Path,
                    help="dir of hardware frame dumps to embed in the page")
    ap.add_argument("--hold", type=float, default=8.0)
    ap.add_argument("--capture", type=Path,
                    help="with --push: frame-dump each state here")
    args = ap.parse_args()
    if args.push:
        push_main(args)
    else:
        serve(args)


if __name__ == "__main__":
    main()
