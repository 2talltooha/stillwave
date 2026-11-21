// sleep_test.ino
// Overnight test of the radar's own sleep features, including its apnea counter.
// Prints two kinds of lines:
//   Q,ms,in_bed,sleep_state,breath_state,breath_rate,heart_rate,moving_range,struggle   every 5 s
//   C,ms,presence,sleep_state,avg_resp,avg_heart,turnovers,large_moves,minor_moves,apneas   every 30 s
// sleep_state: 0 deep, 1 light, 2 awake, 3 none
//
// Wiring: Radar VIN->5V, GND->GND, TX->GPIO4, RX->GPIO5

#include "DFRobot_HumanDetection.h"

DFRobot_HumanDetection hu(&Serial1);

unsigned long lastQuick = 0;
unsigned long lastComposite = 0;

void setup() {
  Serial.begin(115200);
  Serial1.begin(115200, SERIAL_8N1, 4, 5);
  delay(1000);
  Serial.println("# sleep test starting");
  while (hu.begin() != 0) {
    Serial.println("# radar not responding");
    delay(1000);
  }
  while (hu.configWorkMode(hu.eSleepMode) != 0) {
    Serial.println("# could not set sleep mode, retrying");
    delay(1000);
  }
  Serial.println("# ready (sleep mode)");
}

void loop() {
  unsigned long now = millis();

  if (now - lastQuick >= 5000) {
    lastQuick = now;
    int inBed  = hu.smSleepData(hu.eInOrNotInBed);
    int state  = hu.smSleepData(hu.eSleepState);
    int bState = hu.getBreatheState();
    int bRate  = hu.getBreatheValue();
    int hRate  = hu.getHeartRate();
    int mRange = hu.smHumanData(hu.eHumanMovingRange);
    int strug  = hu.smSleepData(hu.eAbnormalStruggle);
    char buf[96];
    snprintf(buf, sizeof(buf), "Q,%lu,%d,%d,%d,%d,%d,%d,%d",
             now, inBed, state, bState, bRate, hRate, mRange, strug);
    Serial.println(buf);
  }

  if (now - lastComposite >= 30000) {
    lastComposite = now;
    sSleepComposite c = hu.getSleepComposite();
    char buf[128];
    snprintf(buf, sizeof(buf), "C,%lu,%d,%d,%d,%d,%d,%d,%d,%d",
             millis(), c.presence, c.sleepState, c.averageRespiration, c.averageHeartbeat,
             c.turnoverNumber, c.largeBodyMove, c.minorBodyMove, c.apneaEvents);
    Serial.println(buf);
  }
}
