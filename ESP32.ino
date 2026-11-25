#include <Arduino.h>
#include "src/FastOled130.h"
#include "src/my_video.h"

static constexpr int OLED_SDA_PIN = 21;
static constexpr int OLED_SCL_PIN = 22;
static constexpr uint8_t OLED_I2C_ADDR = 0x3C;

static constexpr uint8_t OLED_COL_OFFSET = 0;

FastOled130 oled;

enum DemoMode : uint8_t {
  MODE_TEXT = 0,
  MODE_IMAGE = 1,
  MODE_VIDEO = 2
};

DemoMode currentMode = MODE_TEXT;
uint32_t modeStartMs = 0;

void setup() {
  Serial.begin(115200);
  if (!oled.beginI2C(OLED_I2C_ADDR, OLED_SDA_PIN, OLED_SCL_PIN, 800000, OLED_COL_OFFSET)) {
    Serial.println("OLED not found!");
    while (true) delay(1000);
  }
  oled.beginVideo(my_video);
}

void loop() {
  oled.stepVideo(true); // true = loop forever, false = play once
}