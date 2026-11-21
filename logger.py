"""
logger.py - reads both sensors from the ESP32, saves everything to a CSV,
runs the pause detector, and prints the state once a second.

Run:   python logger.py
Keys:  S = breath-hold START mark   E = breath-hold END mark   Q = quit

Close the Arduino IDE first - only one program can use the port.
If the USB connection drops, it reconnects by itself and keeps the same file.
"""

import csv
import os
import sys
import time
from datetime import datetime

import serial
import serial.tools.list_ports

from detector import PauseDetector

try:
    import msvcrt  # Windows keyboard
except ImportError:
    msvcrt = None

BAUD = 115200
FIELDS = [
    "kind", "wall_time", "t", "esp_ms",
    "ax", "ay", "az",
    "presence", "movement", "moving_range", "breath_state", "breath_rate", "heart_rate",
    "state", "gap", "bpm", "wave", "mark",
]


def find_port():
    ports = list(serial.tools.list_ports.comports())
    for p in ports:
        if "CP210" in p.description or "CH340" in p.description or "USB" in p.description:
            return p.device
    return ports[0].device if ports else None


def open_port():
    """Keep trying until the ESP32 shows up."""
    warned = False
    while True:
        port = find_port()
        if port:
            try:
                ser = serial.Serial(port, BAUD, timeout=0.2)
                time.sleep(0.5)
                ser.reset_input_buffer()   # drop stale data
                print(f"# connected on {port}")
                return ser
            except serial.SerialException as e:
                if not warned:
                    print(f"# can't open {port} ({e}). Is the Arduino IDE still open? Retrying...")
                    warned = True
        elif not warned:
            print("# ESP32 not found. Plug it in. Retrying...")
            warned = True
        time.sleep(2)


class Session:
    """Reads lines, feeds the detector, writes the CSV. Used by logger.py and the dashboard."""

    def __init__(self, folder="data"):
        os.makedirs(folder, exist_ok=True)
        self.path = os.path.join(folder, datetime.now().strftime("session_%Y%m%d_%H%M%S.csv"))
        self.f = open(self.path, "w", newline="")
        self.w = csv.writer(self.f)
        self.w.writerow(FIELDS)
        self.f.flush()
        self.det = PauseDetector()
        self.ser = None
        self.last_esp = {"M": None, "R": None}
        self.latest = {}          # latest values, for printing and the dashboard
        self.messages = []        # '#' lines from the ESP32

    def _row(self, **kw):
        row = [kw.get(k, "") for k in FIELDS]
        self.w.writerow(row)
        self.f.flush()

    def mark(self, label):
        now = time.time()
        self._row(kind="MARK", wall_time=datetime.now().isoformat(timespec="seconds"),
                  t=f"{now:.2f}", state=self.det.state, mark=label)

    def step(self):
        """Read one line. Returns True if something new was processed."""
        if self.ser is None:
            self.ser = open_port()
        try:
            raw = self.ser.readline().decode(errors="ignore").strip()
        except (serial.SerialException, OSError):
            print("# USB connection lost - reconnecting...")
            try:
                self.ser.close()
            except Exception:
                pass
            self.ser = None
            self.last_esp = {"M": None, "R": None}
            return False
        if not raw:
            return False
        if raw.startswith("#"):
            self.messages.append(raw)
            print(raw)
            return False

        parts = raw.split(",")
        kind = parts[0]
        now = time.time()
        wall = datetime.now().isoformat(timespec="seconds")
        try:
            nums = list(map(int, parts[1:]))
        except ValueError:
            return False

        if kind == "M" and len(nums) == 4:
            esp_ms, ax, ay, az = nums
            if esp_ms == self.last_esp["M"]:
                return False
            self.last_esp["M"] = esp_ms
            state, gap, bpm, wave = self.det.update_chest(now, ax, ay, az)
            self._row(kind="M", wall_time=wall, t=f"{now:.2f}", esp_ms=esp_ms, ax=ax, ay=ay, az=az,
                      state=state, gap=f"{gap:.1f}", bpm=bpm, wave=f"{wave:.1f}")
            self.latest.update(t=now, state=state, gap=gap, bpm=bpm, wave=wave)
            return True

        if kind == "R" and len(nums) == 7:
            esp_ms, presence, movement, mrange, bstate, brate, hr = nums
            if esp_ms == self.last_esp["R"]:
                return False
            self.last_esp["R"] = esp_ms
            self.det.update_radar(now, presence, movement, mrange, bstate, brate)
            self._row(kind="R", wall_time=wall, t=f"{now:.2f}", esp_ms=esp_ms,
                      presence=presence, movement=movement, moving_range=mrange,
                      breath_state=bstate, breath_rate=brate, heart_rate=hr, state=self.det.state)
            self.latest.update(presence=presence, moving_range=mrange, radar_rate=brate,
                               breath_state=bstate, heart_rate=hr)
            if self.det.start_t is None:   # radar-only: state comes from the radar
                self.latest.update(t=now, state=self.det.state, gap=self.det.gap, bpm=self.det.bpm)
            return True
        return False

    def close(self):
        self.f.close()
        if self.ser:
            self.ser.close()


def read_key():
    if msvcrt is None or not msvcrt.kbhit():
        return ""
    key = msvcrt.getwch().lower()
    while msvcrt.kbhit():      # throw away auto-repeats from holding a key down
        msvcrt.getwch()
    return key


def main():
    s = Session()
    print(f"Saving to {s.path}")
    print("S = hold START, E = hold END, Q = quit\n")
    holding = False
    last_print = 0

    try:
        while True:
            key = read_key()
            if key == "q":
                break
            if key == "s" and not holding:
                holding = True
                s.mark("HOLD_START")
                print("\n>>> HOLD START\n")
            elif key == "e" and holding:
                holding = False
                s.mark("HOLD_END")
                print("\n>>> HOLD END\n")

            s.step()

            now = time.time()
            if now - last_print >= 1.0 and "state" in s.latest:
                last_print = now
                L = s.latest
                flag = "   <<< NO BREATHING" if L["state"] == "PAUSED" else ""
                hold = " [holding]" if holding else ""
                print(f"{datetime.now():%H:%M:%S}  {L['state']:9}  bpm={L.get('bpm', 0):3}"
                      f"  radar_bpm={L.get('radar_rate', '-'):>3}  bstate={L.get('breath_state', '-')}"
                      f"  here={L.get('presence', '-')}"
                      f"  move={L.get('moving_range', '-'):>3}  gap={L.get('gap', 0):4.1f}s{hold}{flag}")
    except KeyboardInterrupt:
        pass
    finally:
        s.close()
        print(f"\nSaved {s.path}")
        print(f"Pauses this session: {len(s.det.pauses)}")


if __name__ == "__main__":
    main()
