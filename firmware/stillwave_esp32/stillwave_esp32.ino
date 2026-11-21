// stillwave_esp32.ino
// Reads two sensors and prints lines over USB for logger.py:
//   M,ms,ax,ay,az                       chest motion sensor, 20 times a second
//   R,ms,presence,movement,moving_range,breath_state,breath_rate,heart_rate   radar, every few seconds
//
// The radar is slow to answer, so it runs in its own task on the second core.
// That keeps the chest readings steady at 20 per second.
//
// Wiring:
//   Radar (C1001):   VIN->5V  GND->GND  TX->GPIO4  RX->GPIO5
//   GY-6500 (chest): VCC->3V3 GND->GND  SDA->GPIO21  SCL->GPIO22

#include <Wire.h>
#include "DFRobot_HumanDetection.h"

DFRobot_HumanDetection hu(&Serial1);

const int RADAR_RX_PIN = 4;
const int RADAR_TX_PIN = 5;
const int I2C_SDA = 21;
const int I2C_SCL = 22;
const uint8_t MPU_ADDR = 0x68;
const unsigned long IMU_INTERVAL_MS = 50;   // 20 readings per second

// Latest radar values, written by the radar task, printed by loop().
volatile int rPresence = 0, rMovement = 0, rRange = 0, rBState = 0, rBRate = 0, rHR = 0;
volatile bool rNew = false;
volatile int radarStatus = 0;   // 0 starting, 1 not responding, 2 ready
bool imuOk = false;
unsigned long lastImu = 0;
int lastRadarStatus = -1;

void say(const char *msg) { Serial.println(msg); }

// ---------- chest motion sensor ----------
void imuWrite(uint8_t reg, uint8_t val) {
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(reg);
  Wire.write(val);
  Wire.endTransmission();
}

bool imuInit() {
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(0x75);                       // WHO_AM_I
  if (Wire.endTransmission(false) != 0) return false;
  Wire.requestFrom(MPU_ADDR, (uint8_t)1);
  if (!Wire.available()) return false;
  uint8_t id = Wire.read();
  char buf[48];
  snprintf(buf, sizeof(buf), "# chest sensor id 0x%02X", id);
  say(buf);
  imuWrite(0x6B, 0x00);                   // wake up
  delay(50);
  imuWrite(0x1C, 0x00);                   // accel range +-2g (most sensitive)
  imuWrite(0x1A, 0x05);                   // low-pass filter ~10 Hz to cut jitter
  return true;
}

bool imuRead(int16_t &ax, int16_t &ay, int16_t &az) {
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(0x3B);                       // first accel register
  if (Wire.endTransmission(false) != 0) return false;
  if (Wire.requestFrom(MPU_ADDR, (uint8_t)6) != 6) return false;
  ax = (Wire.read() << 8) | Wire.read();
  ay = (Wire.read() << 8) | Wire.read();
  az = (Wire.read() << 8) | Wire.read();
  return true;
}

// ---------- radar task (second core) ----------
void radarTask(void *param) {
  Serial1.begin(115200, SERIAL_8N1, RADAR_RX_PIN, RADAR_TX_PIN);
  vTaskDelay(pdMS_TO_TICKS(1000));
  while (hu.begin() != 0) {
    radarStatus = 1;
    vTaskDelay(pdMS_TO_TICKS(1000));
  }
  while (hu.configWorkMode(hu.eSleepMode) != 0) {
    vTaskDelay(pdMS_TO_TICKS(1000));
  }
  radarStatus = 2;

  for (;;) {
    int p  = hu.smHumanData(hu.eHumanPresence);
    int m  = hu.smHumanData(hu.eHumanMovement);
    int mr = hu.smHumanData(hu.eHumanMovingRange);
    int bs = hu.getBreatheState();
    int br = hu.getBreatheValue();
    int hr = hu.getHeartRate();
    rPresence = p; rMovement = m; rRange = mr; rBState = bs; rBRate = br; rHR = hr;
    rNew = true;
    vTaskDelay(pdMS_TO_TICKS(200));
  }
}

void setup() {
  Serial.begin(115200);
  delay(500);
  say("# stillwave starting");

  Wire.begin(I2C_SDA, I2C_SCL);
  Wire.setClock(100000);                  // slower clock is safer on long wires
  imuOk = imuInit();
  say(imuOk ? "# chest sensor ok" : "# chest sensor NOT found (check SDA/SCL/3V3)");

  xTaskCreatePinnedToCore(radarTask, "radar", 8192, NULL, 1, NULL, 0);
}

void loop() {
  if (radarStatus != lastRadarStatus) {
    lastRadarStatus = radarStatus;
    if (radarStatus == 1) say("# radar not responding, check wiring (swap TX/RX?)");
    if (radarStatus == 2) say("# radar ready (sleep mode)");
  }

  unsigned long now = millis();
  if (imuOk && now - lastImu >= IMU_INTERVAL_MS) {
    lastImu = now;
    int16_t ax, ay, az;
    if (imuRead(ax, ay, az)) {
      char buf[64];
      snprintf(buf, sizeof(buf), "M,%lu,%d,%d,%d", now, ax, ay, az);
      Serial.println(buf);
    }
  }

  if (rNew) {
    rNew = false;
    char buf[96];
    snprintf(buf, sizeof(buf), "R,%lu,%d,%d,%d,%d,%d,%d",
             millis(), rPresence, rMovement, rRange, rBState, rBRate, rHR);
    Serial.println(buf);
  }
}
