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

    ~/busybar/app/.venv/bin/python tools/status_demo.py [--hold SECS]
    ... --capture DIR    # also frame-dump each state (USB only)
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
    return [
        {"id": "bullet", "type": "image", "path": bullets[route],
         "x": 1, "y": 0, "timeout": 30},
        {"id": "big", "type": "text", "text": f"NO {route} TRAINS",
         "font": "tiny", "color": WHITE, "x": 19, "y": 2,
         "align": "top_left", "timeout": 30},
        {"id": "sub", "type": "text", "text": plain(f"{head} - {period}"),
         "font": "tiny", "color": AMBER, "x": 19, "y": 15,
         "align": "bottom_left", "width": 50, "scroll_rate": 1500,
         "timeout": 30},
    ]


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
        {"id": "sub", "type": "text", "text": plain(head), "font": "tiny",
         "color": AMBER, "x": 38, "y": 2, "align": "top_left",
         "width": 30, "scroll_rate": 1200, "timeout": 30},
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


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--hold", type=float, default=8.0)
    ap.add_argument("--capture", type=Path)
    args = ap.parse_args()
    now = time.time()

    print("pulling live MTA data…")
    alerts = fetch_alerts()
    held = fetch_held_train()
    names = stop_names()

    # 1: a really-held train, else a canned example clearly labeled
    if held:
        d_route, d_stop, d_min = held
        d_src = "LIVE VehiclePositions"
    else:
        d_route, d_stop, d_min = "N", "Q01", 6
        d_src = "simulated (no train held >2.5 min right now)"
    d_name = names.get(d_stop, d_stop)

    # 2: a suspension for G if there is one, else any route
    susp = [a for a in alerts if "Suspended" in a["type"] and a["head"]]
    susp.sort(key=lambda a: (("G" not in a["routes"]),
                             not active_now(a, now)))
    s = susp[0]
    s_route = "G" if "G" in s["routes"] else sorted(s["routes"])[0]
    s_live = "ACTIVE NOW" if active_now(s, now) else f"upcoming ({s['period']})"

    # 3: a live Delays-type alert, else the freshest one
    dly = [a for a in alerts if a["type"] == "Delays"]
    dly.sort(key=lambda a: not active_now(a, now))
    a3 = dly[0] if dly else dict(routes={"Q"}, head="delays", period="")
    a3_route = sorted(a3["routes"] & set(app.DESIGNATOR_META) or {"Q"})[0]

    print(f"1 DELAYED:     {d_route} held {d_min:.0f} min at {d_name} "
          f"[{d_src}]")
    print(f"2 NOT RUNNING: {s_route} — {s['head'][:70]} [{s_live}]")
    print(f"3 ALERT:       {a3_route} — {plain(a3['head'])[:70]}")
    print("4 TRACK:       badge TK D3 (extension field; simulated diff)")

    bar = connect_bar()
    bullets = upload_bullets(bar, {d_route, s_route, a3_route, "N"})
    cap = args.capture
    if cap:
        cap.mkdir(parents=True, exist_ok=True)

    for name, els in (
            ("delayed", state_delayed(bullets, d_route, d_name, d_min)),
            ("not_running", state_not_running(
                bullets, s_route, plain(s["head"]), s["period"])),
            ("alert", state_alert_dot(bullets, a3_route, 7, a3["head"])),
            ("track", state_track(bullets, "N", 4, "D3"))):
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


if __name__ == "__main__":
    main()
