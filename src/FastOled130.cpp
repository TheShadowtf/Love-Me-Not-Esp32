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

// Is not the best, but it get's the job done

#include "FastOled130.h"
#include "OledFont5x7.h"
#include <stdarg.h>
#include <stdio.h>
#include <string.h>

static const uint8_t SH1106_INIT_CMDS[] = {
  0xAE,       // Display OFF
  0xD5, 0x80, // Display clock divide ratio / oscillator freq
  0xA8, 0x3F, // Multiplex ratio (64)
  0xD3, 0x00, // Display offset = 0
  0x40,       // Display start line = 0
  0x20, 0x02, // Page addressing mode (required for SSD1306, ignored by SH1106)
  0x8D, 0x14, // Enable internal charge pump (SSD1306)
  0xAD, 0x8B, // Enable DC-DC charge pump (SH1106)
  0xA1,       // Segment re-map (col 127/131 -> 0)
  0xC8,       // COM output scan direction (remapped)
  0xDA, 0x12, // COM pins hardware config
  0x81, 0xCF, // Contrast
  0xD9, 0xF1, // Pre-charge period
  0xDB, 0x40, // VCOMH deselect level
  0xA4,       // Entire display ON (resume to RAM content)
  0xA6,       // Normal display (non-inverted)
  0xAF        // Display ON
};

bool FastOled130::beginI2C(uint8_t addr, int sda, int scl, uint32_t i2cHz,
                           uint8_t colOffset, TwoWire& wire) {
  _useSpi = false;
  _wire = &wire;
  _i2cAddr = addr;
  _colOffset = colOffset;

  _wire->begin(sda, scl, i2cHz);
  delay(50); // Allow OLED power/charge-pump rail to stabilize on cold boot
  _wire->beginTransmission(_i2cAddr);
  if (_wire->endTransmission() != 0) {
    return false;
  }

  initDisplay();
  clear();
  display();
  return true;
}

bool FastOled130::beginSPI(SPIClass& spi, int8_t csPin, int8_t dcPin, int8_t rstPin,
                           uint32_t spiHz, uint8_t colOffset) {
  _useSpi = true;
  _spi = &spi;
  _csPin = csPin;
  _dcPin = dcPin;
  _rstPin = rstPin;
  _spiHz = spiHz;
  _colOffset = colOffset;

  pinMode(_csPin, OUTPUT);
  pinMode(_dcPin, OUTPUT);
  digitalWrite(_csPin, HIGH);
  digitalWrite(_dcPin, LOW);

  if (_rstPin >= 0) {
    pinMode(_rstPin, OUTPUT);
    digitalWrite(_rstPin, HIGH);
    delay(1);
    digitalWrite(_rstPin, LOW);
    delay(10);
    digitalWrite(_rstPin, HIGH);
    delay(10);
  }

  _spi->begin();
  initDisplay();
  clear();
  display();
  return true;
}

void FastOled130::initDisplay() {
  sendCmdList(SH1106_INIT_CMDS, sizeof(SH1106_INIT_CMDS));
}

void FastOled130::sendCmd(uint8_t cmd) {
  if (_useSpi) {
    _spi->beginTransaction(SPISettings(_spiHz, MSBFIRST, SPI_MODE0));
    digitalWrite(_dcPin, LOW);
    digitalWrite(_csPin, LOW);
    _spi->transfer(cmd);
    digitalWrite(_csPin, HIGH);
    _spi->endTransaction();
  } else {
    _wire->beginTransmission(_i2cAddr);
    _wire->write(0x00); // Co=0, D/C#=0 (command)
    _wire->write(cmd);
    _wire->endTransmission();
  }
}

void FastOled130::sendCmdList(const uint8_t* cmds, size_t len) {
  if (_useSpi) {
    _spi->beginTransaction(SPISettings(_spiHz, MSBFIRST, SPI_MODE0));
    digitalWrite(_dcPin, LOW);
    digitalWrite(_csPin, LOW);
    _spi->writeBytes(cmds, len);
    digitalWrite(_csPin, HIGH);
    _spi->endTransaction();
  } else {
    _wire->beginTransmission(_i2cAddr);
    _wire->write(0x00);
    _wire->write(cmds, len);
    _wire->endTransmission();
  }
}

void FastOled130::sendPageData(uint8_t page, const uint8_t* data, size_t len) {
  uint8_t colLow = _colOffset & 0x0F;
  uint8_t colHigh = 0x10 | ((_colOffset >> 4) & 0x0F);
  uint8_t pageCmds[3] = { static_cast<uint8_t>(0xB0 | (page & 0x07)), colLow, colHigh };
  sendCmdList(pageCmds, 3);

  if (_useSpi) {
    _spi->beginTransaction(SPISettings(_spiHz, MSBFIRST, SPI_MODE0));
    digitalWrite(_dcPin, HIGH);
    digitalWrite(_csPin, LOW);
    _spi->writeBytes(data, len);
    digitalWrite(_csPin, HIGH);
    _spi->endTransaction();
  } else {
    size_t offset = 0;
    while (offset < len) {
      size_t chunk = len - offset;
      if (chunk > 64) chunk = 64;
      _wire->beginTransmission(_i2cAddr);
      _wire->write(0x40); // Co=0, D/C#=1 (data)
      _wire->write(data + offset, chunk);
      _wire->endTransmission();
      offset += chunk;
    }
  }
}

void FastOled130::setContrast(uint8_t contrast) {
  uint8_t cmds[2] = { 0x81, contrast };
  sendCmdList(cmds, 2);
}

void FastOled130::setInvert(bool invert) {
  sendCmd(invert ? 0xA7 : 0xA6);
}

void FastOled130::setFlip(bool flipX, bool flipY) {
  uint8_t cmds[2] = {
    static_cast<uint8_t>(flipX ? 0xA0 : 0xA1),
    static_cast<uint8_t>(flipY ? 0xC0 : 0xC8)
  };
  sendCmdList(cmds, 2);
}

void FastOled130::setDisplayOn(bool on) {
  sendCmd(on ? 0xAF : 0xAE);
}

void FastOled130::clear() {
  memset(_buf, 0x00, OLED_BUF_SIZE);
}

void FastOled130::fill(uint8_t bytePattern) {
  memset(_buf, bytePattern, OLED_BUF_SIZE);
}

void FastOled130::display() {
  for (uint8_t page = 0; page < OLED_PAGES; ++page) {
    sendPageData(page, _buf + (page * OLED_WIDTH), OLED_WIDTH);
  }
}

void FastOled130::setPixel(int16_t x, int16_t y, uint8_t color) {
  if (static_cast<uint16_t>(x) >= OLED_WIDTH || static_cast<uint16_t>(y) >= OLED_HEIGHT) {
    return;
  }
  uint16_t idx = ((y >> 3) * OLED_WIDTH) + x;
  uint8_t mask = 1 << (y & 7);
  if (color == OLED_WHITE) {
    _buf[idx] |= mask;
  } else if (color == OLED_BLACK) {
    _buf[idx] &= ~mask;
  } else {
    _buf[idx] ^= mask;
  }
}

void FastOled130::drawHLine(int16_t x, int16_t y, int16_t w, uint8_t color) {
  if (y < 0 || y >= OLED_HEIGHT || w <= 0) return;
  if (x < 0) { w += x; x = 0; }
  if (x + w > OLED_WIDTH) { w = OLED_WIDTH - x; }
  if (w <= 0) return;

  uint8_t* p = _buf + ((y >> 3) * OLED_WIDTH) + x;
  uint8_t mask = 1 << (y & 7);
  if (color == OLED_WHITE) {
    while (w--) *p++ |= mask;
  } else if (color == OLED_BLACK) {
    mask = ~mask;
    while (w--) *p++ &= mask;
  } else {
    while (w--) *p++ ^= mask;
  }
}

void FastOled130::drawVLine(int16_t x, int16_t y, int16_t h, uint8_t color) {
  if (x < 0 || x >= OLED_WIDTH || h <= 0) return;
  if (y < 0) { h += y; y = 0; }
  if (y + h > OLED_HEIGHT) { h = OLED_HEIGHT - y; }
  if (h <= 0) return;

  int16_t yEnd = y + h - 1;
  uint8_t pStart = y >> 3;
  uint8_t pEnd = yEnd >> 3;

  for (uint8_t page = pStart; page <= pEnd; ++page) {
    uint8_t mask = 0xFF;
    if (page == pStart) mask &= (0xFF << (y & 7));
    if (page == pEnd)   mask &= (0xFF >> (7 - (yEnd & 7)));

    uint8_t* dst = &_buf[page * OLED_WIDTH + x];
    if (color == OLED_WHITE)      *dst |= mask;
    else if (color == OLED_BLACK) *dst &= ~mask;
    else                          *dst ^= mask;
  }
}

void FastOled130::drawLine(int16_t x0, int16_t y0, int16_t x1, int16_t y1, uint8_t color) {
  if (y0 == y1) {
    if (x0 > x1) { int16_t t = x0; x0 = x1; x1 = t; }
    drawHLine(x0, y0, x1 - x0 + 1, color);
    return;
  }
  if (x0 == x1) {
    if (y0 > y1) { int16_t t = y0; y0 = y1; y1 = t; }
    drawVLine(x0, y0, y1 - y0 + 1, color);
    return;
  }

  int16_t dx = abs(x1 - x0), sx = x0 < x1 ? 1 : -1;
  int16_t dy = -abs(y1 - y0), sy = y0 < y1 ? 1 : -1;
  int16_t err = dx + dy;

  for (;;) {
    setPixel(x0, y0, color);
    if (x0 == x1 && y0 == y1) break;
    int16_t e2 = 2 * err;
    if (e2 >= dy) { err += dy; x0 += sx; }
    if (e2 <= dx) { err += dx; y0 += sy; }
  }
}

void FastOled130::drawRect(int16_t x, int16_t y, int16_t w, int16_t h, uint8_t color) {
  if (w <= 0 || h <= 0) return;
  drawHLine(x, y, w, color);
  drawHLine(x, y + h - 1, w, color);
  drawVLine(x, y, h, color);
  drawVLine(x + w - 1, y, h, color);
}

void FastOled130::fillRect(int16_t x, int16_t y, int16_t w, int16_t h, uint8_t color) {
  if (w <= 0 || h <= 0) return;
  if (x < 0) { w += x; x = 0; }
  if (x + w > OLED_WIDTH) { w = OLED_WIDTH - x; }
  if (w <= 0) return;
  for (int16_t i = 0; i < w; ++i) {
    drawVLine(x + i, y, h, color);
  }
}

void FastOled130::drawChar(int16_t x, int16_t y, char c, uint8_t color, uint8_t scale) {
  if (c < 0x20 || c > 0x7E) c = '?';
  if (scale == 0) scale = 1;

  const uint8_t* glyph = &OLED_FONT_5X7[(c - 0x20) * 5];
  if (scale == 1) {
    for (uint8_t col = 0; col < 5; ++col) {
      uint8_t bits = glyph[col];
      for (uint8_t row = 0; row < 7; ++row) {
        if (bits & (1 << row)) {
          setPixel(x + col, y + row, color);
        }
      }
    }
  } else {
    for (uint8_t col = 0; col < 5; ++col) {
      uint8_t bits = glyph[col];
      for (uint8_t row = 0; row < 7; ++row) {
        if (bits & (1 << row)) {
          fillRect(x + col * scale, y + row * scale, scale, scale, color);
        }
      }
    }
  }
}

void FastOled130::drawText(int16_t x, int16_t y, const char* text, uint8_t color, uint8_t scale) {
  if (!text) return;
  if (scale == 0) scale = 1;
  int16_t cursorX = x;
  int16_t cursorY = y;
  int16_t advX = 6 * scale;
  int16_t advY = 8 * scale;

  while (*text) {
    char c = *text++;
    if (c == '\n') {
      cursorX = x;
      cursorY += advY;
      continue;
    }
    if (c == '\r') continue;
    drawChar(cursorX, cursorY, c, color, scale);
    cursorX += advX;
  }
}

void FastOled130::drawTextf(int16_t x, int16_t y, uint8_t color, uint8_t scale, const char* fmt, ...) {
  char buf[96];
  va_list args;
  va_start(args, fmt);
  vsnprintf(buf, sizeof(buf), fmt, args);
  va_end(args);
  drawText(x, y, buf, color, scale);
}

void FastOled130::drawBitmap(int16_t x, int16_t y, uint16_t w, uint16_t h,
                             const uint8_t* data, uint8_t color) {
  if (!data || w == 0 || h == 0) return;
  uint16_t bytesPerRow = (w + 7) >> 3;
  for (uint16_t j = 0; j < h; ++j) {
    const uint8_t* rowPtr = data + j * bytesPerRow;
    for (uint16_t i = 0; i < w; ++i) {
      if (rowPtr[i >> 3] & (0x80 >> (i & 7))) {
        setPixel(x + i, y + j, color);
      }
    }
  }
}

void FastOled130::drawImage(int16_t x, int16_t y, const OledImage& img, uint8_t color) {
  if (!img.data) return;

  if (img.format == OLED_IMG_ROW_MSB) {
    drawBitmap(x, y, img.width, img.height, img.data, color);
    return;
  }

  if (img.format == OLED_IMG_RLE_PAGE) {
    decodeRleFrame(img.data, false);
    return;
  }

  if (img.format == OLED_IMG_PAGE_LSB) {
    // Fast path: full-screen aligned blit
    if (x == 0 && y == 0 && img.width == OLED_WIDTH && img.height == OLED_HEIGHT && color == OLED_WHITE) {
      memcpy(_buf, img.data, OLED_BUF_SIZE);
      return;
    }

    uint16_t pages = (img.height + 7) >> 3;
    // Fast path: byte-aligned Y
    if ((y & 7) == 0 && color == OLED_WHITE) {
      int16_t startPage = y >> 3;
      for (uint16_t p = 0; p < pages; ++p) {
        int16_t dstPage = startPage + p;
        if (dstPage < 0 || dstPage >= OLED_PAGES) continue;
        for (uint16_t i = 0; i < img.width; ++i) {
          int16_t dx = x + i;
          if (dx >= 0 && dx < OLED_WIDTH) {
            _buf[dstPage * OLED_WIDTH + dx] |= img.data[p * img.width + i];
          }
        }
      }
      return;
    }

    // General fallback for arbitrary Y
    for (uint16_t p = 0; p < pages; ++p) {
      for (uint16_t i = 0; i < img.width; ++i) {
        uint8_t b = img.data[p * img.width + i];
        if (!b) continue;
        for (uint8_t bit = 0; bit < 8; ++bit) {
          if (b & (1 << bit)) {
            uint16_t py = p * 8 + bit;
            if (py < img.height) setPixel(x + i, y + py, color);
          }
        }
      }
    }
  }
}

uint32_t FastOled130::decodeRleFrame(const uint8_t* src, bool xorMode) {
  uint32_t inIdx = 0;
  uint16_t outIdx = 0;

  while (outIdx < OLED_BUF_SIZE) {
    uint8_t ctrl = src[inIdx++];
    if (ctrl < 128) {
      uint16_t count = ctrl + 1;
      if (outIdx + count > OLED_BUF_SIZE) count = OLED_BUF_SIZE - outIdx;
      if (xorMode) {
        for (uint16_t i = 0; i < count; ++i) _buf[outIdx++] ^= src[inIdx++];
      } else {
        memcpy(_buf + outIdx, src + inIdx, count);
        outIdx += count;
        inIdx += count;
      }
    } else {
      uint16_t count = 257 - ctrl;
      uint8_t val = src[inIdx++];
      if (outIdx + count > OLED_BUF_SIZE) count = OLED_BUF_SIZE - outIdx;
      if (xorMode) {
        if (val != 0) {
          for (uint16_t i = 0; i < count; ++i) _buf[outIdx++] ^= val;
        } else {
          outIdx += count;
        }
      } else {
        memset(_buf + outIdx, val, count);
        outIdx += count;
      }
    }
  }
  return inIdx;
}

void FastOled130::beginVideo(const OledVideo& video) {
  _video = &video;
  _videoOffset = 0;
  _videoFrame = 0;
  _lastFrameUs = 0;
  _videoFinished = (video.frameCount == 0 || video.stream == nullptr);
}

bool FastOled130::stepVideo(bool loop) {
  if (!_video || _videoFinished) return false;

  uint32_t frameIntervalUs = (_video->fps > 0) ? (1000000UL / _video->fps) : 33333UL;
  uint32_t now = micros();
  if (_videoFrame > 0 && (now - _lastFrameUs) < frameIntervalUs) {
    return false;
  }
  _lastFrameUs = now;

  if (_videoFrame >= _video->frameCount) {
    if (!loop) {
      _videoFinished = true;
      return false;
    }
    _videoOffset = 0;
    _videoFrame = 0;
  }

  const uint8_t* ptr = _video->stream + _videoOffset;
  uint8_t frameType = ptr[0];
  uint32_t consumed = 1;

  if (frameType == OLED_FRAME_RAW) {
    memcpy(_buf, ptr + 1, OLED_BUF_SIZE);
    consumed += OLED_BUF_SIZE;
  } else if (frameType == OLED_FRAME_RLE) {
    consumed += decodeRleFrame(ptr + 1, false);
  } else if (frameType == OLED_FRAME_XOR_RLE) {
    consumed += decodeRleFrame(ptr + 1, true);
  }

  _videoOffset += consumed;
  _videoFrame++;
  display();
  return true;
}
