"""
sleepdata.py - shared logic for the live state and the night report.

Reads two file formats:
  * session CSVs written by server.py (data/session_*.csv)
  * night files written by capture.py (data/night_*.txt, lines like "02:11:19,Q,...")
"""

import csv
import os
from collections import deque
from datetime import datetime, timedelta

RESTLESS_THRESHOLD = 40     # radar moving_range above this = restless
RESTLESS_HOLD_S = 10        # stay "restless" this long after the last big movement
SLEEP_NAMES = {0: "deep", 1: "light", 2: "awake", 3: "none"}

# Breathing dips: the radar's breathing rate falls by DIP_DROP or more within DIP_WINDOW_S,
# after the person has been watched being still for DIP_STILL_S (movement throws off the radar's estimate).
# A breath hold or pause looks like this, but so do normal slow-downs, so this is NOT an apnea count.
# Informal self-tests on 21 Nov (one person): 8 of 8 deliberate breath holds flagged, and about 5 dips
# an hour during one hour of normal sleep.
DIP_DROP = 6            # breaths per minute
DIP_WINDOW_S = 30
DIP_GAP_S = 60          # dips closer together than this count once
DIP_STILL_S = 40        # need this long of observed stillness before a dip can count
DIP_STILL_MAX = 15      # radar moving_range above this counts as movement
DIP_DATA_GAP_S = 30     # no readings for this long (USB drop, restart) -> start observing again
BAD_VALUE = 200         # the radar answers 255 when a query times out; treat as "no reading"


def clean(value):
    return 0 if value >= BAD_VALUE else value


class DipDetector:
    """Feed it readings in time order. update() returns a dict when a new dip starts, else None."""

    def __init__(self):
        self.recent = deque()       # (seconds, rate) of locked readings in the last DIP_WINDOW_S
        self.last_dip = None
        self.still_since = None     # start of the current observed still stretch
        self.last_seen = None

    def still_for(self, t):
        return 0 if self.still_since is None else max(0, t - self.still_since)

    def update(self, t, rate, breath_state, moving_range=0):
        """t: seconds (any origin). breath_state 1 = radar has locked onto breathing."""
        if self.last_seen is not None and t - self.last_seen > DIP_DATA_GAP_S:
            self.still_since = None
            self.recent.clear()
        self.last_seen = t
        if moving_range > DIP_STILL_MAX or self.still_since is None:
            self.still_since = t
        if breath_state != 1 or rate <= 0:
            return None
        while self.recent and t - self.recent[0][0] > DIP_WINDOW_S:
            self.recent.popleft()
        hit = None
        if self.recent and self.still_for(t) >= DIP_STILL_S:
            peak = max(r for _, r in self.recent)
            if peak - rate >= DIP_DROP and (self.last_dip is None or t - self.last_dip > DIP_GAP_S):
                self.last_dip = t
                hit = {"drop": peak - rate, "from": peak, "to": rate, "still_s": round(self.still_for(t))}
        self.recent.append((t, rate))
        return hit


def live_state(presence, moving_range, breath_rate, sleep_state, last_big_move_age):
    """One word for the dashboard."""
    if presence == 0:
        return "EMPTY"
    if moving_range > RESTLESS_THRESHOLD or (last_big_move_age is not None and last_big_move_age < RESTLESS_HOLD_S):
        return "RESTLESS"
    if breath_rate == 0:
        return "LOCKING ON"
    if sleep_state in (0, 1):
        return "ASLEEP"
    return "STILL"


# ---------- loading ----------

def load_readings(path):
    """Returns a list of dicts with: t (datetime), presence, moving_range, breath_state,
    breath_rate, heart_rate, in_bed, sleep_state. Works for both file formats."""
    if path.endswith(".csv"):
        return _load_session_csv(path)
    return _load_night_txt(path)


def _load_session_csv(path):
    out = []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            if row.get("kind") != "R":
                continue
            try:
                out.append({
                    "t": datetime.fromisoformat(row["wall_time"]),
                    "presence": int(row["presence"]),
                    "moving_range": int(row["moving_range"]),
                    "breath_state": int(row["breath_state"]),
                    "breath_rate": clean(int(row["breath_rate"])),
                    "heart_rate": clean(int(row["heart_rate"])),
                    "in_bed": int(row["in_bed"]),
                    "sleep_state": int(row["sleep_state"]),
                })
            except (ValueError, KeyError, TypeError):    # half-written row after a power cut
                continue
    return out


def _load_night_txt(path):
    """capture.py format: HH:MM:SS,Q,ms,in_bed,sleep_state,b_state,b_rate,hr,moving_range,struggle
    The date comes from the file name (night_YYYYMMDD_HHMMSS.txt)."""
    base = os.path.basename(path)
    try:
        day = datetime.strptime(base.split("_")[1], "%Y%m%d")
    except (IndexError, ValueError):
        day = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    out = []
    prev = None
    with open(path, errors="ignore") as f:
        for line in f:
            parts = line.strip().split(",")
            if len(parts) < 10 or parts[1] != "Q":
                continue
            try:
                hh, mm, ss = map(int, parts[0].split(":"))
                _, in_bed, sstate, bstate, brate, hr, mrange = map(int, parts[2:9])
            except ValueError:
                continue
            t = day + timedelta(hours=hh, minutes=mm, seconds=ss)
            if prev and t < prev:          # crossed midnight
                day += timedelta(days=1)
                t += timedelta(days=1)
            prev = t
            hr = clean(hr)
            out.append({"t": t, "presence": in_bed, "moving_range": mrange, "breath_state": bstate,
                        "breath_rate": clean(brate),
                        "heart_rate": hr, "in_bed": in_bed, "sleep_state": sstate})
    return out


# ---------- report ----------

def build_report(readings):
    if len(readings) < 2:
        return {"error": "Not enough data in this file."}

    start, end = readings[0]["t"], readings[-1]["t"]
    secs = {"deep": 0.0, "light": 0.0, "awake": 0.0, "none": 0.0}
    in_bed_s = 0.0
    restless = []            # list of [start, end]
    last_big = None
    timeline = []            # one point per reading for the charts
    dips = []
    detector = DipDetector()

    observed_s = 0.0
    tracked_s = 0.0          # in bed AND the radar had a breathing rate
    gap_after = set()        # indexes of readings that come right after a gap
    for i, (a, b) in enumerate(zip(readings, readings[1:])):
        dt = (b["t"] - a["t"]).total_seconds()
        if dt <= 0 or dt > 120:          # skip gaps (USB drops, restarts)
            gap_after.add(i + 1)
            continue
        observed_s += dt
        if a["in_bed"] == 1 or a["presence"] == 1:
            in_bed_s += dt
            if a["breath_rate"] > 0:
                tracked_s += dt
            secs[SLEEP_NAMES.get(a["sleep_state"], "none")] += dt

    for i, r in enumerate(readings):
        big = r["moving_range"] > RESTLESS_THRESHOLD
        if big:
            if last_big and (r["t"] - last_big).total_seconds() <= RESTLESS_HOLD_S * 3 and restless:
                restless[-1][1] = r["t"]
            else:
                restless.append([r["t"], r["t"]])
            last_big = r["t"]
        age = (r["t"] - last_big).total_seconds() if last_big else None
        dip = detector.update(r["t"].timestamp(), r["breath_rate"], r.get("breath_state", 1), r["moving_range"])
        if dip:
            dips.append({"t": r["t"].strftime("%H:%M:%S"), **dip})
        timeline.append({
            "t": r["t"].strftime("%H:%M:%S"),
            "rate": r["breath_rate"] or None,
            "heart": r["heart_rate"] or None,
            "state": live_state(r["presence"], r["moving_range"], r["breath_rate"], r["sleep_state"], age),
            "sleep": SLEEP_NAMES.get(r["sleep_state"], "none"),
            "move": r["moving_range"],
            "dip": dip["drop"] if dip else 0,
            "gap": i in gap_after,
        })

    rates = [r["breath_rate"] for r in readings if r["breath_rate"] > 0 and r["presence"] == 1]
    rates_sorted = sorted(rates)
    asleep_s = secs["deep"] + secs["light"]

    def mins(s):
        return round(s / 60)

    return {
        "start": start.strftime("%Y-%m-%d %H:%M"),
        "end": end.strftime("%H:%M"),
        "recorded_min": mins(observed_s),        # time with data, gaps left out
        "in_bed_min": mins(in_bed_s),
        "asleep_min": mins(asleep_s),
        "deep_min": mins(secs["deep"]),
        "light_min": mins(secs["light"]),
        "awake_min": mins(secs["awake"]),
        "sleep_efficiency_pct": round(100 * asleep_s / in_bed_s) if in_bed_s else 0,
        "breath_avg": round(sum(rates) / len(rates), 1) if rates else None,
        "breath_low": rates_sorted[len(rates_sorted) // 20] if rates else None,     # 5th percentile
        "breath_high": rates_sorted[-1 - len(rates_sorted) // 20] if rates else None,  # 95th percentile
        "restless_count": len(restless),
        "dip_count": len(dips),
        "dips_per_hour": round(len(dips) / (in_bed_s / 3600), 1) if in_bed_s >= 3600 else None,  # only for 1 h+
        "breathing_tracked_pct": round(100 * tracked_s / in_bed_s) if in_bed_s else 0,
        "dips": dips,
        "restless_periods": [{"start": s.strftime("%H:%M:%S"),
                              "seconds": max(5, round((e - s).total_seconds()))} for s, e in restless],
        "timeline": timeline,
    }


def night_only(readings):
    """Drop afternoon readings (12:00-18:00) so testing during the day doesn't count as sleep."""
    return [r for r in readings if r["t"].hour >= 18 or r["t"].hour < 12]


def week_summary(folder="data", days=7, now=None):
    """Screen-Time-style weekly roll-up of the nights recorded in the last `days` days.
    Uses the night-time part of each recording (6 pm to noon); it counts if that has an hour or more in bed."""
    now = now or datetime.now()
    nights = []
    for name in list_recordings(folder):
        readings = night_only(load_readings(os.path.join(folder, name)))
        if len(readings) < 2:
            continue
        start = readings[0]["t"]
        if now - start > timedelta(days=days):
            continue
        rep = build_report(readings)
        if rep.get("in_bed_min", 0) < 60:
            continue
        label = (start - timedelta(hours=12)).strftime("%a %b %d")      # 2 am Sunday = Saturday night
        nights.append({"night": label, "start": start.isoformat(timespec="minutes"),
                       "in_bed_min": rep["in_bed_min"], "asleep_min": rep["asleep_min"],
                       "breath_avg": rep["breath_avg"], "dip_count": rep["dip_count"]})
    if not nights:
        return {"nights": 0}
    n = len(nights)
    in_bed = sum(x["in_bed_min"] for x in nights)
    breaths = [x["breath_avg"] for x in nights if x["breath_avg"]]
    longest = max(nights, key=lambda x: x["in_bed_min"])
    return {
        "nights": n,
        "avg_in_bed_min": round(in_bed / n),
        "avg_asleep_min": round(sum(x["asleep_min"] for x in nights) / n),
        "breath_avg": round(sum(breaths) / len(breaths), 1) if breaths else None,
        "dips_per_hour": round(sum(x["dip_count"] for x in nights) / (in_bed / 60), 1) if in_bed else None,
        "longest": longest,
        "list": sorted(nights, key=lambda x: x["start"]),
    }


def list_recordings(folder="data"):
    if not os.path.isdir(folder):
        return []
    files = [f for f in os.listdir(folder) if f.endswith((".csv", ".txt"))
             and os.path.getsize(os.path.join(folder, f)) > 200]          # skip header-only files
    return sorted(files, reverse=True)
