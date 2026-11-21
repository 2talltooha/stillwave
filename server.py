"""
server.py - reads the radar from the ESP32, saves every reading, and serves the
live dashboard and night report.

Run:   python server.py
Open:  http://localhost:8000          live dashboard
       http://localhost:8000/report   night report

Close the Arduino IDE first - only one program can use the USB port.
If the ESP32 isn't plugged in, the dashboard still opens and shows "waiting for sensor".
"""

import asyncio
import csv
import os
import threading
import time
from collections import deque
from datetime import datetime

import serial
import serial.tools.list_ports
import uvicorn
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse

import night_summary
import notify
import settings
import sleepdata

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
WEB = os.path.join(HERE, "web")
BAUD = 115200
FIELDS = ["kind", "wall_time", "esp_ms", "presence", "movement", "moving_range", "breath_state",
          "breath_rate", "heart_rate", "in_bed", "sleep_state", "summary"]


class Sensor:
    """Background thread: serial -> CSV + latest state + 15 minutes of history."""

    def __init__(self):
        os.makedirs(DATA, exist_ok=True)
        self.path = os.path.join(DATA, datetime.now().strftime("session_%Y%m%d_%H%M%S.csv"))
        self.f = open(self.path, "w", newline="")
        self.w = csv.writer(self.f)
        self.w.writerow(FIELDS)
        self.f.flush()
        self.lock = threading.Lock()
        self.connected = False
        self.last_reading_t = None
        self.last_big_move_t = None
        self.latest = {}
        self.summary = {}
        self.history = deque()          # (unix_t, breath_rate, heart_rate, moving_range, state, dip)
        self.events = deque(maxlen=30)  # (clock, text)
        self.prev_state = None
        self.session_start = time.time()
        self.in_bed_since = None
        self.dips = sleepdata.DipDetector()
        self.dip_count = 0
        self.last_dip_t = None
        self.empty_since = None         # for the automatic morning notification
        self.morning_checked = False
        self.morning_sent = False
        self.present_since = None       # for the out-of-bed alert
        self.left_at = None
        self.oob_armed = False
        self.oob_sent = False

    def _port(self):
        forced = settings.get("SERIAL_PORT")          # e.g. SERIAL_PORT=COM3 in .env
        if forced:
            return forced
        ports = list(serial.tools.list_ports.comports())
        for keys in (("CP210", "CH340"), ("USB",)):   # ESP32 USB chips first, then anything USB
            for p in ports:
                if any(k in p.description for k in keys):
                    return p.device
        return None

    def run(self):
        while True:
            try:
                port = self._port()
                if not port:
                    self.connected = False
                    time.sleep(2)
                    continue
                with serial.Serial(port, BAUD, timeout=1) as ser:
                    time.sleep(0.5)
                    ser.reset_input_buffer()
                    self.connected = True
                    print(f"# connected on {port}")
                    while True:
                        line = ser.readline().decode(errors="ignore").strip()
                        if line:
                            self._handle(line)
            except (serial.SerialException, OSError):
                self.connected = False
                print("# USB lost, reconnecting...")
                time.sleep(2)
            except Exception as e:                    # never let the reader thread die overnight
                self.connected = False
                print(f"# reader error ({e!r}), restarting...")
                time.sleep(2)

    def _handle(self, line):
        if line.startswith("#"):
            print(line)
            return
        parts = line.split(",")
        now = time.time()
        wall = datetime.now().isoformat(timespec="seconds")
        try:
            nums = list(map(int, parts[1:]))
        except ValueError:
            print("# dropped:", repr(line))
            return

        if parts[0] == "R" and len(nums) == 9:
            esp_ms, presence, movement, mrange, bstate, brate, hr, in_bed, sstate = nums
            brate, hr = sleepdata.clean(brate), sleepdata.clean(hr)     # 255 = radar query timed out
            if mrange > sleepdata.RESTLESS_THRESHOLD:
                self.last_big_move_t = now
            age = now - self.last_big_move_t if self.last_big_move_t else None
            state = sleepdata.live_state(presence, mrange, brate, sstate, age)
            dip = self.dips.update(int(now), brate, bstate, mrange)   # whole seconds, like the report
            self.w.writerow(["R", wall, esp_ms, presence, movement, mrange, bstate, brate, hr, in_bed, sstate, ""])
            self.f.flush()
            with self.lock:
                self.last_reading_t = now
                self.latest = {"presence": presence, "moving_range": mrange, "breath_rate": brate,
                               "heart_rate": hr, "in_bed": in_bed,
                               "sleep": sleepdata.SLEEP_NAMES.get(sstate, "none"), "state": state}
                self.history.append((now, brate, hr, mrange, state, dip["drop"] if dip else 0))
                while self.history and now - self.history[0][0] > 15 * 60:
                    self.history.popleft()
                self._track_events(now, state, presence)
                self._check_morning(now, presence)
                self._check_out_of_bed(now, presence)
                if dip:
                    self.dip_count += 1
                    self.last_dip_t = now
                    self.events.appendleft((datetime.now().strftime("%H:%M:%S"),
                                            f"Breathing dip: {dip['from']} to {dip['to']} breaths/min "
                                            f"after {dip['still_s']} s still"))

        elif parts[0] == "C" and len(nums) == 9:
            _, pres, sstate, avg_r, avg_h, turns, large, minor, apneas = nums
            self.w.writerow(["C", wall, nums[0], pres, "", "", "", "", "", "", sstate,
                             f"avg_resp={avg_r};avg_heart={avg_h};turnovers={turns};"
                             f"large={large};minor={minor};apneas={apneas}"])
            self.f.flush()
            with self.lock:
                self.summary = {"avg_resp": avg_r, "avg_heart": avg_h, "turnovers": turns,
                                "large_moves": large, "minor_moves": minor, "apneas": apneas}

    def _check_out_of_bed(self, now, presence):
        """Caregiver alert: someone who was in bed for a while got up and hasn't come back.
        Off unless OUT_OF_BED_ALERT_MIN is set in .env. The same number is how long they must
        have been in bed first, so a demo can use 1 and a real night 10."""
        limit = notify.out_of_bed_minutes() * 60
        if not limit:
            return
        if presence == 1:
            if self.left_at is not None:                    # came back
                if self.oob_sent:
                    away = round((now - self.left_at) / 60)
                    self._alert("Stillwave - back in bed", f"Back after {away} min out of bed.", "white_check_mark")
                self.left_at, self.oob_sent = None, False
            if self.present_since is None:
                self.present_since = now
            return
        if self.left_at is None:                            # just left
            self.left_at = now
            self.oob_armed = self.present_since is not None and now - self.present_since >= limit
            self.present_since = None
        if self.oob_armed and not self.oob_sent and now - self.left_at >= limit:
            self.oob_sent = True
            left = datetime.fromtimestamp(self.left_at).strftime("%-I:%M %p" if os.name != "nt" else "%#I:%M %p")
            self._alert("Stillwave - out of bed",
                        f"Out of bed for {round((now - self.left_at) / 60)} min (got up at {left}). "
                        "Radar presence only, not fall detection.", "warning")

    def _alert(self, title, text, tags):
        def go():
            clock = datetime.now().strftime("%H:%M:%S")
            try:
                notify.send(title, text, tags=tags, to=notify.caregiver_topic())
                msg = f"{title.split(' - ')[1].capitalize()} alert sent"
            except notify.NotifyError as e:
                msg = f"Couldn't send alert: {e}"
            with self.lock:
                self.events.appendleft((clock, msg))
        threading.Thread(target=go, daemon=True).start()

    def _check_morning(self, now, presence):
        """Out of bed for 10 min after a real night -> send the night summary to the phone, once."""
        if presence == 1:
            self.empty_since = None
            self.morning_checked = False
            return
        if self.empty_since is None:
            self.empty_since = now
        if (not self.morning_checked and not self.morning_sent and 5 <= datetime.now().hour < 13
                and now - self.empty_since >= notify.MORNING_EMPTY_S and notify.topic()):
            self.morning_checked = True
            threading.Thread(target=self._send_morning, daemon=True).start()

    def _send_morning(self):
        rep = sleepdata.build_report(sleepdata.night_only(sleepdata.load_readings(self.path)))
        if rep.get("in_bed_min", 0) < notify.MORNING_MIN_IN_BED:
            return                      # a short test, not a night
        clock = datetime.now().strftime("%H:%M:%S")
        try:
            send_night(self.path, "Stillwave - last night", night=True)
            text = "Morning summary sent to your phone"
            self.morning_sent = True
            if datetime.now().weekday() == 6:          # Sunday: weekly roll-up too, like Screen Time
                send_week()
                text += " (plus your week)"
        except notify.NotifyError as e:
            text = f"Couldn't send the morning summary: {e}"
        with self.lock:
            self.events.appendleft((clock, text))

    def _track_events(self, now, state, presence):
        clock = datetime.now().strftime("%H:%M:%S")
        if presence == 1 and self.in_bed_since is None:
            self.in_bed_since = now
            self.events.appendleft((clock, "Person detected"))
        if presence == 0 and self.in_bed_since is not None:
            self.in_bed_since = None
            self.events.appendleft((clock, "Bed empty"))
        if state == "RESTLESS" and self.prev_state != "RESTLESS":
            self.events.appendleft((clock, "Restless"))
        if state == "ASLEEP" and self.prev_state != "ASLEEP":
            self.events.appendleft((clock, "Asleep (radar)"))
        self.prev_state = state

    def snapshot(self):
        with self.lock:
            now = time.time()
            stale = self.last_reading_t is None or now - self.last_reading_t > 45
            hist = [{"ago": round(now - t), "rate": r or None, "heart": h or None, "move": m, "state": s, "dip": d}
                    for t, r, h, m, s, d in self.history]
            return {
                "connected": self.connected and not stale,
                "latest": self.latest,
                "summary": self.summary,
                "history": hist,
                "events": list(self.events),
                "in_bed_s": round(now - self.in_bed_since) if self.in_bed_since else 0,
                "dip_count": self.dip_count,
                "still_s": round(self.dips.still_for(now)),
                "last_dip_ago": round(now - self.last_dip_t) if self.last_dip_t else None,
                "file": os.path.basename(self.path),
            }


sensor = Sensor()
app = FastAPI()


@app.on_event("startup")
def start_sensor():
    threading.Thread(target=sensor.run, daemon=True).start()


@app.get("/")
def dashboard():
    return FileResponse(os.path.join(WEB, "dashboard.html"), headers={"Cache-Control": "no-store"})


@app.get("/report")
def report_page():
    return FileResponse(os.path.join(WEB, "report.html"), headers={"Cache-Control": "no-store"})


@app.get("/api/recordings")
def recordings():
    return sleepdata.list_recordings(DATA)


@app.get("/api/report")
def report(file: str):
    name = os.path.basename(file)          # no folder tricks
    path = os.path.join(DATA, name)
    if not os.path.isfile(path):
        return JSONResponse({"error": "File not found."}, status_code=404)
    return sleepdata.build_report(sleepdata.load_readings(path))


ALLOWED_ORIGINS = {"http://localhost:8000", "http://127.0.0.1:8000"}


def same_origin(origin):
    """Browsers send Origin; other websites can't pass this check. No Origin = not a browser page."""
    return origin is None or origin in ALLOWED_ORIGINS


_summaries = {}     # (file, size) -> summary, so Gemini isn't asked twice about the same data


def gemini_summary(path, rep, variant="full"):
    """Cached Gemini summary of one recording. Raises night_summary.SummaryError."""
    key = (os.path.basename(path), os.path.getsize(path), variant)
    if key not in _summaries:
        _summaries[key] = night_summary.summarize(rep)
    return _summaries[key]


def send_night(path, title, night=False):
    """Night numbers + Gemini summary -> phone notification. Raises notify.NotifyError.
    night=True leaves out afternoon readings (for the automatic morning summary)."""
    readings = sleepdata.load_readings(path)
    if night:
        readings = sleepdata.night_only(readings)
    rep = sleepdata.build_report(readings)
    if "error" in rep:
        raise notify.NotifyError(rep["error"])
    try:
        text = gemini_summary(path, rep, "night" if night else "full")["text"]
    except night_summary.SummaryError:
        text = None                     # still send the numbers
    notify.send(title, notify.night_message(rep, text))


def send_week():
    week = sleepdata.week_summary(DATA)
    if not week["nights"]:
        raise notify.NotifyError("No nights recorded in the last 7 days yet.")
    notify.send("Stillwave - your week", notify.week_message(week), tags="calendar")


@app.get("/api/summary")
def summary(file: str):
    path = os.path.join(DATA, os.path.basename(file))
    if not os.path.isfile(path):
        return JSONResponse({"error": "File not found."}, status_code=404)
    rep = sleepdata.build_report(sleepdata.load_readings(path))
    if "error" in rep:
        return JSONResponse(rep, status_code=400)
    try:
        return gemini_summary(path, rep)
    except night_summary.SummaryError as e:
        return JSONResponse({"error": str(e)}, status_code=502)


@app.post("/api/notify")
def notify_phone(file: str, request: Request):
    if not same_origin(request.headers.get("origin")):
        return JSONResponse({"error": "Not allowed from another site."}, status_code=403)
    path = os.path.join(DATA, os.path.basename(file))
    if not os.path.isfile(path):
        return JSONResponse({"error": "File not found."}, status_code=404)
    try:
        send_night(path, "Stillwave - your night")
    except notify.NotifyError as e:
        return JSONResponse({"error": str(e)}, status_code=502)
    return {"ok": True}


@app.post("/api/notify_week")
def notify_week(request: Request):
    if not same_origin(request.headers.get("origin")):
        return JSONResponse({"error": "Not allowed from another site."}, status_code=403)
    try:
        send_week()
    except notify.NotifyError as e:
        return JSONResponse({"error": str(e)}, status_code=502)
    return {"ok": True}


@app.get("/api/week")
def week():
    return sleepdata.week_summary(DATA)


@app.websocket("/ws")
async def ws(socket: WebSocket):
    if not same_origin(socket.headers.get("origin")):
        await socket.close(code=1008)                 # another website trying to read live data
        return
    await socket.accept()
    try:
        while True:
            await socket.send_json(sensor.snapshot())
            await asyncio.sleep(1)
    except (WebSocketDisconnect, RuntimeError):
        pass


def keep_awake():
    """Ask Windows not to sleep on its own while the server runs (closing the lid still can)."""
    if os.name == "nt":
        import ctypes
        ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
        print("Keeping the laptop awake while recording")


if __name__ == "__main__":
    print(f"Saving to {sensor.path}")
    print("Dashboard: http://localhost:8000   Report: http://localhost:8000/report")
    print("Gemini summary:", "ready" if night_summary.load_key() else "no API key (add GEMINI_API_KEY to .env)")
    print("Phone notifications:", "ready" if notify.topic() else "off (add NTFY_TOPIC to .env)")
    oob = notify.out_of_bed_minutes()
    print("Out-of-bed alert:", f"on, after {oob:g} min" if oob else "off (add OUT_OF_BED_ALERT_MIN to .env)")
    keep_awake()
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")
