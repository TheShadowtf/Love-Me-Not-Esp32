# "Love Me Not" — ESP32 128×64 OLED Video Player & FastOled130 Library

An **ESP32-WROOM-32** project playing the **"Love Me Not"** video on a **1.30" 128×64 monochrome OLED** (`1.30'' V1.0 I315`, SSD1306/SH1106-compatible), powered by a custom, lightweight graphics and video library (**FastOled130**) and an accompanying desktop converter/editor (**FastOled130 Studio**).

---

## About the `FastOled130` Library

`FastOled130` is built **specifically for 1.30" 128×64 monochrome OLED displays** (no generic multi-panel overhead).

### How It Works
- **Native Page-Major Framebuffer (1024 bytes)**: The `128 × 64` 1-bit framebuffer (`128 × 8` pages) matches the OLED's internal GDDRAM layout directly, so `display()` streams raw pages with zero bit transposition.
- **Fast Transport**: Runs at **800 kHz I2C** (or **10+ MHz Hardware SPI**) with zero dynamic memory allocation (`malloc`/`new`-free), giving smooth video playback.
- **Cold-Start Compatible**: Sends both SSD1306 (`0x8D 0x14`, `0x20 0x02`) and SH1106 (`0xAD 0x8B`) charge-pump initialization commands with a 50 ms power-rail settle delay so the display wakes up reliably from a cold power-on. (I specify because I didn't knew about this and I was debugging for like half an hour :)
- **Compressed Video Streaming**: Decodes **PackBits RLE** keyframes (`OLED_FRAME_RLE`) and **XOR-delta RLE** inter-frames (`OLED_FRAME_XOR_RLE`) directly from flash (`PROGMEM`) into the 1 KB framebuffer, fitting long videos inside the ESP32's internal flash without an SD card.

### How to Use It
```cpp
#include "src/FastOled130.h"
#include "src/my_video.h"

FastOled130 oled;

void setup() {
  // I2C: addr=0x3C, SDA=21, SCL=22, 800kHz, colOffset=0 (use 2 for 2px-shifted SH1106)
  oled.beginI2C(0x3C, 21, 22, 800000, 0);

  // Text & primitives
  oled.clear();
  oled.drawRect(0, 0, 128, 64, OLED_WHITE);
  oled.drawText(10, 10, "Hello ESP32!", OLED_WHITE, 1);
  oled.display();

  // Start looping compressed video
  oled.beginVideo(my_video);
}

void loop() {
  oled.stepVideo(true); // non-blocking frame-paced playback
}
```

---

## Hardware Wiring (ESP32-WROOM-32)
- `VCC` → `3.3V`
- `GND` → `GND`
- `SDA` → `GPIO 21`
- `SCL` → `GPIO 22`

> If your 1.30" OLED panel is shifted 2 pixels horizontally, change `OLED_COL_OFFSET` in `ESP32.ino` from `0` to `2`.

---

## Build, Upload & Media Studio
- **Compile**: Run `compile.bat`
- **Upload to `COM`**: Run `upload.bat` (or `upload.bat COMx` or just use ArduinoIDE)
- **Convert & Edit Videos**: Run `studio.bat` to open **FastOled130 Studio** (`tools/oled_studio.py`), which downscales videos/GIFs/images to `128×64`, lets you choose FPS and binarization algorithm (choose between them to see which gets the best result), edit individual pixels/frames, preview ESP32 flash usage, and export `.h` headers.

---

## License

This project and the `FastOled130` library are released under the **MIT License** — you are free to use, modify, and distribute this project however you wish, as long as you include the copyright notice and **credit the original author (`Ems`)**.

```text
MIT License
Copyright (c) 2026 Ems

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.
```
