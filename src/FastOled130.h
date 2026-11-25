/*
 * FastOled130 - Lightweight 128x64 1.30" OLED Graphics & Video Library for ESP32
 *
 * MIT License
 * Copyright (c) 2026 Ems
 *
 * Permission is hereby granted, free of charge, to any person obtaining a copy
 * of this software and associated documentation files (the "Software"), to deal
 * in the Software without restriction, including without limitation the rights
 * to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
 * copies of the Software, and to permit persons to whom the Software is
 * furnished to do so, subject to the following conditions:
 *
 * The above copyright notice and this permission notice shall be included in all
 * copies or substantial portions of the Software.
 *
 * THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
 * IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
 * FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.
 */

#pragma once

#include <Arduino.h>
#include <Wire.h>
#include <SPI.h>

// Display geometry for 1.30" 128x64 monochrome OLED (SH1106 / SSD1306)
#define OLED_WIDTH    128
#define OLED_HEIGHT   64
#define OLED_PAGES    (OLED_HEIGHT / 8)
#define OLED_BUF_SIZE (OLED_WIDTH * OLED_PAGES)

// Pixel colors
enum OledColor : uint8_t {
  OLED_BLACK  = 0,
  OLED_WHITE  = 1,
  OLED_INVERT = 2
};

// Image storage formats
enum OledImageFormat : uint8_t {
  OLED_IMG_ROW_MSB  = 0,
  OLED_IMG_PAGE_LSB = 1,
  OLED_IMG_RLE_PAGE = 2 
};

// Video frame compression types (each frame in stream starts with 1-byte type)
enum OledFrameType : uint8_t {
  OLED_FRAME_RAW      = 0x00,
  OLED_FRAME_RLE      = 0x01,
  OLED_FRAME_XOR_RLE  = 0x02
};

struct OledImage {
  uint16_t width;
  uint16_t height;
  const uint8_t* data;
  uint8_t format;
};

struct OledVideo {
  uint16_t frameCount;
  uint8_t fps;
  const uint8_t* stream;
};

class FastOled130 {
public:
  FastOled130() = default;

  bool beginI2C(uint8_t addr = 0x3C, int sda = 21, int scl = 22,
                uint32_t i2cHz = 800000, uint8_t colOffset = 2,
                TwoWire& wire = Wire);

  bool beginSPI(SPIClass& spi, int8_t csPin, int8_t dcPin, int8_t rstPin = -1,
                uint32_t spiHz = 10000000, uint8_t colOffset = 2);

  void setColumnOffset(uint8_t offset) { _colOffset = offset; }
  void setContrast(uint8_t contrast);
  void setInvert(bool invert);
  void setFlip(bool flipX, bool flipY);
  void setDisplayOn(bool on);

  // Framebuffer operations (fast, zero dynamic allocation)
  void clear();
  void fill(uint8_t bytePattern = 0xFF);
  void display();

  // Direct buffer access for custom effects
  uint8_t* buffer() { return _buf; }
  const uint8_t* buffer() const { return _buf; }

  // Drawing primitives
  void setPixel(int16_t x, int16_t y, uint8_t color = OLED_WHITE);
  void drawHLine(int16_t x, int16_t y, int16_t w, uint8_t color = OLED_WHITE);
  void drawVLine(int16_t x, int16_t y, int16_t h, uint8_t color = OLED_WHITE);
  void drawLine(int16_t x0, int16_t y0, int16_t x1, int16_t y1, uint8_t color = OLED_WHITE);
  void drawRect(int16_t x, int16_t y, int16_t w, int16_t h, uint8_t color = OLED_WHITE);
  void fillRect(int16_t x, int16_t y, int16_t w, int16_t h, uint8_t color = OLED_WHITE);

  // Text rendering (built-in 5x7 font)
  void drawChar(int16_t x, int16_t y, char c, uint8_t color = OLED_WHITE, uint8_t scale = 1);
  void drawText(int16_t x, int16_t y, const char* text, uint8_t color = OLED_WHITE, uint8_t scale = 1);
  void drawTextf(int16_t x, int16_t y, uint8_t color, uint8_t scale, const char* fmt, ...);

  // Image rendering
  void drawBitmap(int16_t x, int16_t y, uint16_t w, uint16_t h,
                  const uint8_t* data, uint8_t color = OLED_WHITE);
  void drawImage(int16_t x, int16_t y, const OledImage& img, uint8_t color = OLED_WHITE);

  // Compressed video playback
  void beginVideo(const OledVideo& video);
  // Non-blocking frame stepper; returns true when a frame was decoded & displayed.
  bool stepVideo(bool loop = true);
  bool isVideoFinished() const { return _videoFinished; }
  uint16_t currentVideoFrame() const { return _videoFrame; }

private:
  uint8_t _buf[OLED_BUF_SIZE] = {0};

  // Transport state
  bool _useSpi = false;
  uint8_t _colOffset = 2;

  // I2C
  TwoWire* _wire = &Wire;
  uint8_t _i2cAddr = 0x3C;

  // SPI
  SPIClass* _spi = nullptr;
  int8_t _csPin = -1;
  int8_t _dcPin = -1;
  int8_t _rstPin = -1;
  uint32_t _spiHz = 10000000;

  // Video player state
  const OledVideo* _video = nullptr;
  uint32_t _videoOffset = 0;
  uint16_t _videoFrame = 0;
  uint32_t _lastFrameUs = 0;
  bool _videoFinished = true;

  void initDisplay();
  void sendCmd(uint8_t cmd);
  void sendCmdList(const uint8_t* cmds, size_t len);
  void sendPageData(uint8_t page, const uint8_t* data, size_t len);

  uint32_t decodeRleFrame(const uint8_t* src, bool xorMode);
};
