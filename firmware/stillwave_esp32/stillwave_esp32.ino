// stillwave_esp32.ino
// Contactless sleep monitor: reads the DFRobot C1001 60 GHz radar and prints
// lines over USB for server.py.
//
//   R,ms,presence,movement,moving_range,breath_state,breath_rate,heart_rate,in_bed,sleep_state
//       one per loop (every few seconds; each radar question takes ~0.5 s)
//   C,ms,presence,sleep_state,avg_resp,avg_heart,turnovers,large_moves,minor_moves,apneas
//       the radar's own sleep summary, every 60 s
//
// presence: 0 nobody, 1 someone     movement: 0 none, 1 still, 2 active
// sleep_state: 0 deep, 1 light, 2 awake, 3 none
//
// Wiring: Radar VIN->5V, GND->GND, TX->GPIO4, RX->GPIO5

#include "DFRobot_HumanDetection.h"

DFRobot_HumanDetection hu(&Serial1);

const int RADAR_RX_PIN = 4;
const int RADAR_TX_PIN = 5;
unsigned long lastComposite = 0;

void setup() {
  Serial.begin(115200);
  Serial1.begin(115200, SERIAL_8N1, RADAR_RX_PIN, RADAR_TX_PIN);
  delay(1000);
  Serial.println("# stillwave starting");
  while (hu.begin() != 0) {
    Serial.println("# radar not responding, check wiring (swap TX/RX?)");
    delay(1000);
  }
  while (hu.configWorkMode(hu.eSleepMode) != 0) {
    Serial.println("# could not set sleep mode, retrying");
    delay(1000);
  }
  Serial.println("# radar ready (sleep mode)");
}

void loop() {
  int presence = hu.smHumanData(hu.eHumanPresence);
  int movement = hu.smHumanData(hu.eHumanMovement);
  int mRange   = hu.smHumanData(hu.eHumanMovingRange);
  int bState   = hu.getBreatheState();
  int bRate    = hu.getBreatheValue();
  int hRate    = hu.getHeartRate();
  int inBed    = hu.smSleepData(hu.eInOrNotInBed);
  int sState   = hu.smSleepData(hu.eSleepState);

  char buf[112];
  snprintf(buf, sizeof(buf), "R,%lu,%d,%d,%d,%d,%d,%d,%d,%d",
           millis(), presence, movement, mRange, bState, bRate, hRate, inBed, sState);
  Serial.println(buf);

  if (millis() - lastComposite >= 60000) {
    lastComposite = millis();
    sSleepComposite c = hu.getSleepComposite();
    char cbuf[128];
    snprintf(cbuf, sizeof(cbuf), "C,%lu,%d,%d,%d,%d,%d,%d,%d,%d",
             millis(), c.presence, c.sleepState, c.averageRespiration, c.averageHeartbeat,
             c.turnoverNumber, c.largeBodyMove, c.minorBodyMove, c.apneaEvents);
    Serial.println(cbuf);
  }
}
