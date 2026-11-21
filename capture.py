"""
capture.py - saves every line from the ESP32 to a text file with a timestamp.
Used for the overnight sleep test. Reconnects by itself if USB drops.

Run:  python capture.py        Stop: Ctrl+C
Close the Arduino IDE first.
"""

import os
import time
from datetime import datetime

import serial
import serial.tools.list_ports


def find_port():
    for p in serial.tools.list_ports.comports():
        if "CP210" in p.description or "CH340" in p.description or "USB" in p.description:
            return p.device
    return None


def main():
    os.makedirs("data", exist_ok=True)
    path = os.path.join("data", datetime.now().strftime("night_%Y%m%d_%H%M%S.txt"))
    print(f"Saving to {path}  (Ctrl+C to stop)")
    with open(path, "a") as f:
        while True:
            port = find_port()
            if not port:
                time.sleep(2)
                continue
            try:
                with serial.Serial(port, 115200, timeout=1) as ser:
                    print(f"# connected on {port}")
                    while True:
                        line = ser.readline().decode(errors="ignore").strip()
                        if not line:
                            continue
                        stamped = f"{datetime.now():%H:%M:%S},{line}"
                        f.write(stamped + "\n")
                        f.flush()
                        print(stamped)
            except serial.SerialException:
                print("# USB lost, reconnecting...")
                time.sleep(2)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped.")
