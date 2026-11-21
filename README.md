# Stillwave

A contactless bedside breathing monitor built at Hack Western 13.
A 60 GHz mmWave radar watches breathing from about a metre away, with nothing worn and no camera.

## Hardware

- ESP32 dev board
- DFRobot C1001 60 GHz mmWave radar (SEN0623)

| Radar pin | ESP32 pin |
| --- | --- |
| VIN | 5V (VIN) |
| GND | GND |
| TX | GPIO 4 |
| RX | GPIO 5 |

## Run it

1. Upload `firmware/stillwave_esp32/stillwave_esp32.ino` to the ESP32, then close the Arduino IDE.
2. `pip install -r requirements.txt`
3. Optional, for the Gemini night summary: get a free key at aistudio.google.com and put
   `GEMINI_API_KEY=your-key` in a file called `.env` next to `server.py` (it's gitignored).
   For phone notifications, install the free ntfy app, subscribe to a long random topic name,
   and add `NTFY_TOPIC=that-name` to `.env`. The night summary arrives 10 minutes after you get up, plus a weekly roll-up on Sundays.
4. `python server.py`
5. Open http://localhost:8000 (live) and http://localhost:8000/report (night report).

## Software

- `firmware/stillwave_esp32/` - main ESP32 program
- `server.py` - reads the radar, saves `data/session_*.csv`, serves the dashboard and report
- `sleepdata.py` - live state, breathing-dip detection and night-report calculations
- `night_summary.py` - asks Gemini for a short plain-language summary of a night
- `notify.py` - sends the night summary to your phone through ntfy
- `settings.py` - reads private settings (API key, ntfy topic) from `.env`
- `hardware/` - 3D-printable enclosure (`enclosure.py` generates the STL files)
- `web/` - dashboard and report pages
- `firmware/sleep_test/` - overnight test of the radar's built-in sleep data
- `firmware/radar_sniffer/` - test that prints the radar's raw messages
- `logger.py` - reads the ESP32, saves a CSV, prints the live state
- `detector.py` - decides NOBODY / MOVING / BREATHING / PAUSED
- `capture.py` - saves raw lines to a text file (overnight tests)

Arduino library: `DFRobot_HumanDetection`. Python: `pip install -r requirements.txt`.

## What we found

- The radar's built-in breathing rate is a slow average (one reading about every 6 s). It never
  drops to zero during a breath hold, its breathing-waveform output was always flat, and its
  built-in apnea counter stayed at 0.
- What it does show is a sharp drop in the rate around a hold. Stillwave flags these as
  **breathing dips** (rate falls by 6+ breaths/min within 30 s while the person is still).
  It caught 8 of 8 deliberate breath holds (mostly 25-40 s), and logged about 5 dips an hour during a
  normal night. Normal slow-downs look the same, so dips are not an apnea count.
