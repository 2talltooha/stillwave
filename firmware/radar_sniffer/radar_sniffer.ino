// radar_sniffer.ino
// Test only: prints every message frame the C1001 radar sends, so we can see
// whether it reports a live breathing waveform (control 0x81, command 0x05).
//
// Frame format: 53 59 | control | command | len_hi len_lo | data... | checksum | 54 43
//
// Wiring: Radar VIN->5V, GND->GND, TX->GPIO4, RX->GPIO5
// Open Serial Monitor at 115200. Each frame prints as:
//   F,<ms>,<control>,<command>,<len>,<data bytes in hex>

#include "DFRobot_HumanDetection.h"

DFRobot_HumanDetection hu(&Serial1);

const int RADAR_RX_PIN = 4;
const int RADAR_TX_PIN = 5;

uint8_t buf[300];
int pos = 0;
unsigned long lastQuery = 0;

void sendFrame(uint8_t control, uint8_t command, uint8_t data) {
  uint8_t f[10] = {0x53, 0x59, control, command, 0x00, 0x01, data, 0, 0x54, 0x43};
  uint16_t sum = 0;
  for (int i = 0; i < 7; i++) sum += f[i];
  f[7] = sum & 0xFF;
  Serial1.write(f, 10);
}

void printFrame(int len) {
  // buf holds a full frame starting at 0x53 0x59
  uint8_t control = buf[2], command = buf[3];
  int dlen = (buf[4] << 8) | buf[5];
  Serial.print("F,");
  Serial.print(millis());
  Serial.print(",");
  if (control < 0x10) Serial.print("0");
  Serial.print(control, HEX);
  Serial.print(",");
  if (command < 0x10) Serial.print("0");
  Serial.print(command, HEX);
  Serial.print(",");
  Serial.print(dlen);
  Serial.print(",");
  for (int i = 0; i < dlen && 6 + i < len; i++) {
    uint8_t b = buf[6 + i];
    if (b < 0x10) Serial.print("0");
    Serial.print(b, HEX);
    Serial.print(" ");
  }
  Serial.println();
}

void setup() {
  Serial.begin(115200);
  Serial1.begin(115200, SERIAL_8N1, RADAR_RX_PIN, RADAR_TX_PIN);
  delay(1000);
  Serial.println("# sniffer starting");
  while (hu.begin() != 0) {
    Serial.println("# radar not responding");
    delay(1000);
  }
  while (hu.configWorkMode(hu.eSleepMode) != 0) {
    Serial.println("# could not set sleep mode, retrying");
    delay(1000);
  }
  Serial.println("# listening. Sit still ~1 m away. Hold your breath 20 s after a minute.");
}

void loop() {
  // Every 500 ms, alternate between asking for the breathing waveform (81 85)
  // and the breathing rate (81 82), so we can see when breathing locks on.
  static bool askWave = true;
  if (millis() - lastQuery > 500) {
    lastQuery = millis();
    sendFrame(0x81, askWave ? 0x85 : 0x82, 0x0F);
    askWave = !askWave;
  }

  while (Serial1.available()) {
    uint8_t b = Serial1.read();
    if (pos == 0 && b != 0x53) continue;
    if (pos == 1 && b != 0x59) { pos = (b == 0x53) ? 1 : 0; continue; }
    buf[pos++] = b;
    if (pos >= 6) {
      int dlen = (buf[4] << 8) | buf[5];
      int total = 6 + dlen + 3;            // + checksum + 54 43
      if (dlen > 250) { pos = 0; continue; }
      if (pos >= total) {
        printFrame(total);
        pos = 0;
      }
    }
    if (pos >= (int)sizeof(buf)) pos = 0;
  }
}
