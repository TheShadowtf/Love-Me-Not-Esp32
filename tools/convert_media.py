"""
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
"""

"""
Convert images, GIFs, or videos into FastOled130 C headers for ESP32 (128x64 OLED).

Dependencies:
  pip install pillow            # Required for images and GIFs
  pip install opencv-python     # Optional, required only for MP4/AVI/MOV videos

Usage:
  python convert_media.py image image.png -o ../src/MyImage.h --name my_image
  python convert_media.py video anim.gif -o ../src/MyVideo.h --name my_video --fps 20
  python convert_media.py video clip.mp4 -o ../src/MyVideo.h --name my_video --fps 15 --dither
"""

import argparse
import os
import sys
from typing import List, Tuple

try:
    from PIL import Image, ImageSequence
except ImportError:
    print("Error: Pillow is required. Install with: pip install pillow", file=sys.stderr)
    sys.exit(1)

OLED_W = 128
OLED_H = 64
OLED_PAGES = 8
FRAME_BYTES = OLED_W * OLED_PAGES  # 1024

FRAME_RAW = 0x00
FRAME_RLE = 0x01
FRAME_XOR_RLE = 0x02


def image_to_page_lsb(img: Image.Image, threshold: int = 128, dither: bool = False, invert: bool = False) -> bytes:
    # Resize/pad to 128x64 and pack into SH1106 native page-major LSB-top format.
    gray = img.convert("L")
    if gray.size != (OLED_W, OLED_H):
        gray = gray.resize((OLED_W, OLED_H), Image.Resampling.LANCZOS)

    if dither:
        bw = gray.convert("1", dither=Image.Dither.FLOYDSTEINBERG)
        pixels = bw.load()
        is_on = lambda x, y: (pixels[x, y] != 0) ^ invert
    else:
        pixels = gray.load()
        is_on = lambda x, y: (pixels[x, y] >= threshold) ^ invert

    out = bytearray(FRAME_BYTES)
    for page in range(OLED_PAGES):
        y_base = page * 8
        row_offset = page * OLED_W
        for x in range(OLED_W):
            b = 0
            for bit in range(8):
                if is_on(x, y_base + bit):
                    b |= (1 << bit)
            out[row_offset + x] = b
    return bytes(out)


def packbits_encode(data: bytes) -> bytes:
    # Encode a 1024-byte buffer using PackBits RLE (matches FastOled130::decodeRleFrame).
    n = len(data)
    out = bytearray()
    i = 0
    while i < n:
        # Check for run of identical bytes (at least 2)
        run_len = 1
        while i + run_len < n and run_len < 129 and data[i + run_len] == data[i]:
            run_len += 1

        if run_len >= 2:
            out.append(257 - run_len)
            out.append(data[i])
            i += run_len
        else:
            # Literal run until a repeat of >=2 starts or 128 bytes reached
            lit_start = i
            lit_len = 0
            while i < n and lit_len < 128:
                if i + 1 < n and data[i] == data[i + 1]:
                    # Peek: worth stopping literal if repeat >= 2 (or >=3)
                    if lit_len > 0:
                        break
                i += 1
                lit_len += 1
            out.append(lit_len - 1)
            out.extend(data[lit_start:lit_start + lit_len])
    return bytes(out)


def encode_video_frames(frames: List[bytes], keyframe_interval: int = 0) -> Tuple[bytes, int]:
    # Encode frames using smallest of RAW / RLE / XOR-RLE per frame.
    stream = bytearray()
    raw_total = len(frames) * FRAME_BYTES
    prev = None

    for idx, frame in enumerate(frames):
        rle = packbits_encode(frame)
        candidates = [
            (FRAME_RAW, frame),
            (FRAME_RLE, rle),
        ]
        force_key = (idx == 0) or (keyframe_interval > 0 and (idx % keyframe_interval == 0))
        if not force_key and prev is not None:
            xor_buf = bytes(a ^ b for a, b in zip(prev, frame))
            xor_rle = packbits_encode(xor_buf)
            candidates.append((FRAME_XOR_RLE, xor_rle))

        best_type, best_payload = min(candidates, key=lambda item: len(item[1]))
        stream.append(best_type)
        stream.extend(best_payload)
        prev = frame

    return bytes(stream), raw_total


def format_c_array(name: str, data: bytes, per_line: int = 16) -> str:
    lines = [f"static const uint8_t {name}[] PROGMEM = {{"]
    for i in range(0, len(data), per_line):
        chunk = ", ".join(f"0x{b:02X}" for b in data[i:i + per_line])
        comma = "," if i + per_line < len(data) else ""
        lines.append(f"  {chunk}{comma}")
    lines.append("};")
    return "\n".join(lines)


def convert_image(args):
    img = Image.open(args.input)
    page_bytes = image_to_page_lsb(img, args.threshold, args.dither, args.invert)
    rle_bytes = packbits_encode(page_bytes)

    if len(rle_bytes) < len(page_bytes):
        data = rle_bytes
        fmt = "OLED_IMG_RLE_PAGE"
    else:
        data = page_bytes
        fmt = "OLED_IMG_PAGE_LSB"

    header = f"""#pragma once
#include "FastOled130.h"

// Generated by convert_media.py from {os.path.basename(args.input)} ({len(data)} bytes, {fmt})
{format_c_array(args.name + "_data", data)}

static const OledImage {args.name} = {{
  {OLED_W}, {OLED_H}, {args.name}_data, {fmt}
}};
"""
    with open(args.output, "w", encoding="utf-8") as f:
        f.write(header)
    print(f"Wrote {args.output}: {len(data)} bytes ({fmt})")


def load_video_frames(path: str, target_fps: int, max_frames: int) -> Tuple[List[Image.Image], int]:
    ext = os.path.splitext(path)[1].lower()
    frames = []

    if ext in (".gif", ".webp"):
        im = Image.open(path)
        durations = []
        for frame in ImageSequence.Iterator(im):
            frames.append(frame.copy())
            durations.append(frame.info.get("duration", 50))
            if max_frames > 0 and len(frames) >= max_frames:
                break
        if target_fps <= 0:
            avg_ms = sum(durations) / max(len(durations), 1)
            target_fps = max(1, min(60, round(1000.0 / max(avg_ms, 16))))
        return frames, target_fps

    try:
        import cv2
    except ImportError:
        print("Error: OpenCV is required for video files (.mp4/.avi). Install with: pip install opencv-python",
              file=sys.stderr)
        sys.exit(1)

    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        print(f"Error: Could not open video {path}", file=sys.stderr)
        sys.exit(1)

    src_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    if target_fps <= 0:
        target_fps = max(1, min(30, round(src_fps)))

    step = max(1.0, src_fps / target_fps)
    next_idx = 0.0
    frame_idx = 0

    while True:
        ret, bgr = cap.read()
        if not ret:
            break
        if frame_idx >= int(round(next_idx)):
            rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
            frames.append(Image.fromarray(rgb))
            next_idx += step
            if max_frames > 0 and len(frames) >= max_frames:
                break
        frame_idx += 1

    cap.release()
    return frames, target_fps


def convert_video(args):
    pil_frames, fps = load_video_frames(args.input, args.fps, args.max_frames)
    if not pil_frames:
        print("Error: No frames extracted.", file=sys.stderr)
        sys.exit(1)

    packed = [image_to_page_lsb(f, args.threshold, args.dither, args.invert) for f in pil_frames]
    stream, raw_total = encode_video_frames(packed, args.keyframe_interval)
    ratio = raw_total / max(len(stream), 1)

    header = f"""#pragma once
#include "FastOled130.h"

// Generated by convert_media.py from {os.path.basename(args.input)}
// Frames: {len(packed)}, FPS: {fps}, Size: {len(stream)} bytes (raw {raw_total} bytes, {ratio:.2f}x compression)
{format_c_array(args.name + "_stream", stream)}

static const OledVideo {args.name} = {{
  {len(packed)}, {fps}, {args.name}_stream
}};
"""
    with open(args.output, "w", encoding="utf-8") as f:
        f.write(header)
    print(f"Wrote {args.output}: {len(packed)} frames @ {fps} FPS, {len(stream)} bytes ({ratio:.2f}x compression)")


def main():
    parser = argparse.ArgumentParser(description="Convert images/videos to FastOled130 C headers")
    sub = parser.add_subparsers(dest="mode", required=True)

    for mode in ("image", "video"):
        p = sub.add_parser(mode)
        p.add_argument("input", help="Input image, GIF, or video file")
        p.add_argument("-o", "--output", required=True, help="Output .h file path")
        p.add_argument("--name", default="media_asset", help="C symbol name")
        p.add_argument("--threshold", type=int, default=128, help="Monochrome threshold 0..255")
        p.add_argument("--dither", action="store_true", help="Use Floyd-Steinberg dithering")
        p.add_argument("--invert", action="store_true", help="Invert black/white pixels")
        if mode == "video":
            p.add_argument("--fps", type=int, default=0, help="Target FPS (0 = auto)")
            p.add_argument("--max-frames", type=int, default=0, help="Max frames to export (0 = all)")
            p.add_argument("--keyframe-interval", type=int, default=0, help="Insert RLE keyframe every N frames")

    args = parser.parse_args()
    if args.mode == "image":
        convert_image(args)
    else:
        convert_video(args)


if __name__ == "__main__":
    main()
