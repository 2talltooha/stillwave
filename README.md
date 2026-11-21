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
3. `python server.py`
4. Open http://localhost:8000 (live) and http://localhost:8000/report (night report).

## Software

- `firmware/stillwave_esp32/` - main ESP32 program
- `server.py` - reads the radar, saves `data/session_*.csv`, serves the dashboard and report
- `sleepdata.py` - live state and night-report calculations
- `web/` - dashboard and report pages
- `firmware/sleep_test/` - overnight test of the radar's built-in sleep data
- `firmware/radar_sniffer/` - test that prints the radar's raw messages
- `logger.py` - reads the ESP32, saves a CSV, prints the live state
- `detector.py` - decides NOBODY / MOVING / BREATHING / PAUSED
- `capture.py` - saves raw lines to a text file (overnight tests)

Arduino library: `DFRobot_HumanDetection`. Python: `pip install -r requirements.txt`.

## What we found so far

The radar's built-in breathing rate is a slow average: it stayed "normal" through
40-second breath-holds, and its breathing waveform output was always flat.
We're testing its built-in apnea counter next.
