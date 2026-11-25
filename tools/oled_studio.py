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

# I might update it to support more resolutions then just 128x64

import os
import re
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import List, Optional

import cv2
import numpy as np
from PIL import Image, ImageSequence, ImageTk

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

from convert_media import (
    FRAME_BYTES,
    FRAME_RAW,
    FRAME_RLE,
    FRAME_XOR_RLE,
    OLED_H,
    OLED_PAGES,
    OLED_W,
    encode_video_frames,
    format_c_array,
)

# Default ESP32 Arduino app partition (1,310,720 B) minus ~300 KB base sketch/libs
ESP32_APP_MAX_BYTES = 1310720
ESP32_BASE_SKETCH_BYTES = 300000
ESP32_AVAILABLE_FLASH = ESP32_APP_MAX_BYTES - ESP32_BASE_SKETCH_BYTES

PIXEL_SCALE = 6  # 128*6 = 768 x 64*6 = 384 canvas


def page_lsb_to_grid(buf: bytes) -> np.ndarray:
    # Convert 1024-byte SH1106 page-major LSB-top buffer to (64, 128) uint8 {0, 1} array.
    arr = np.frombuffer(buf, dtype=np.uint8).reshape(OLED_PAGES, OLED_W)
    bits = np.unpackbits(arr[:, np.newaxis, :], axis=1, bitorder="little")
    return bits.reshape(OLED_H, OLED_W)


def grid_to_page_lsb(grid: np.ndarray) -> bytes:
    # Convert (64, 128) uint8 {0, 1} array back to 1024-byte SH1106 page-major LSB-top buffer.
    reshaped = grid.reshape(OLED_PAGES, 8, OLED_W)
    packed = np.packbits(reshaped, axis=1, bitorder="little").reshape(OLED_PAGES, OLED_W)
    return packed.tobytes()


def decode_rle_stream_frame(src: bytes, offset: int, prev_buf: Optional[bytes], xor_mode: bool):
    # Decode one PackBits RLE (or XOR-RLE) frame from stream starting at offset.
    out = bytearray(prev_buf) if (xor_mode and prev_buf is not None) else bytearray(FRAME_BYTES)
    in_idx = offset
    out_idx = 0
    while out_idx < FRAME_BYTES and in_idx < len(src):
        ctrl = src[in_idx]
        in_idx += 1
        if ctrl < 128:
            count = min(ctrl + 1, FRAME_BYTES - out_idx)
            if xor_mode:
                for i in range(count):
                    out[out_idx + i] ^= src[in_idx + i]
            else:
                out[out_idx:out_idx + count] = src[in_idx:in_idx + count]
            out_idx += count
            in_idx += count
        else:
            count = min(257 - ctrl, FRAME_BYTES - out_idx)
            val = src[in_idx]
            in_idx += 1
            if xor_mode:
                if val != 0:
                    for i in range(count):
                        out[out_idx + i] ^= val
            else:
                out[out_idx:out_idx + count] = bytes([val]) * count
            out_idx += count
    return bytes(out), in_idx


def parse_oled_video_header(path: str):
    # Load an exported OledVideo .h file back into (frames_grids, fps, symbol_name).
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()

    # Find stream byte array
    arr_match = re.search(r"uint8_t\s+(\w+)_stream\[\]\s*(?:PROGMEM\s*)?=\s*\{([^}]+)\};", text, re.S)
    if not arr_match:
        raise ValueError("Could not find *_stream[] array in header file.")

    symbol_name = arr_match.group(1)
    hex_bytes = re.findall(r"0x[0-9A-Fa-f]{2}|\d+", arr_match.group(2))
    stream = bytes(int(x, 0) for x in hex_bytes)

    # Find fps & frameCount in OledVideo struct if present
    fps = 24
    frame_count = 0
    struct_match = re.search(r"OledVideo\s+\w+\s*=\s*\{\s*(\d+)\s*,\s*(\d+)", text)
    if struct_match:
        frame_count = int(struct_match.group(1))
        fps = int(struct_match.group(2))

    frames: List[np.ndarray] = []
    offset = 0
    prev_buf = None
    while offset < len(stream) and (frame_count == 0 or len(frames) < frame_count):
        ftype = stream[offset]
        offset += 1
        if ftype == FRAME_RAW:
            buf = stream[offset:offset + FRAME_BYTES]
            offset += FRAME_BYTES
        elif ftype == FRAME_RLE:
            buf, offset = decode_rle_stream_frame(stream, offset, prev_buf, False)
        elif ftype == FRAME_XOR_RLE:
            buf, offset = decode_rle_stream_frame(stream, offset, prev_buf, True)
        else:
            raise ValueError(f"Unknown frame type 0x{ftype:02X} at offset {offset - 1}")
        prev_buf = buf
        frames.append(page_lsb_to_grid(buf))

    return frames, fps, symbol_name


BAYER_4X4 = np.array([
    [ 0,  8,  2, 10],
    [12,  4, 14,  6],
    [ 3, 11,  1,  9],
    [15,  7, 13,  5],
], dtype=np.float32) / 16.0


def detect_media_frame_count(path: str, target_fps: int) -> int:
    # Detect total output frames for the entire video/GIF/image at target_fps.
    ext = os.path.splitext(path)[1].lower()
    if ext in (".png", ".jpg", ".jpeg", ".bmp"):
        return 1
    if ext in (".gif", ".webp"):
        im = Image.open(path)
        return sum(1 for _ in ImageSequence.Iterator(im))
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        return 0
    src_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    src_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    cap.release()
    if src_frames <= 0:
        return 0
    return max(1, int(round(src_frames * max(1, target_fps) / max(1.0, src_fps))))


def pil_to_grid(
    img: Image.Image,
    fit_mode: str,
    threshold: int,
    algo: str,
    invert: bool,
    zoom: float = 1.0,
) -> np.ndarray:
    # Downscale PIL image to 128x64 first (INTER_AREA), then binarize.
    rgb_full = np.asarray(img.convert("RGB"), dtype=np.uint8)
    sh, sw = rgb_full.shape[:2]

    # Optional center zoom before fitting
    if zoom > 1.01:
        zw = max(16, int(round(sw / zoom)))
        zh = max(16, int(round(sh / zoom)))
        left = (sw - zw) // 2
        top = (sh - zh) // 2
        rgb_full = rgb_full[top:top + zh, left:left + zw]
        sh, sw = rgb_full.shape[:2]

    if fit_mode == "Stretch":
        rgb_u8 = cv2.resize(rgb_full, (OLED_W, OLED_H), interpolation=cv2.INTER_AREA)
    elif fit_mode == "Fit (Letterbox)":
        scale = min(OLED_W / sw, OLED_H / sh)
        nw, nh = max(1, int(round(sw * scale))), max(1, int(round(sh * scale)))
        scaled = cv2.resize(rgb_full, (nw, nh), interpolation=cv2.INTER_AREA)
        bg_color = np.median(rgb_full.reshape(-1, 3), axis=0).astype(np.uint8)
        rgb_u8 = np.full((OLED_H, OLED_W, 3), bg_color, dtype=np.uint8)
        y0, x0 = (OLED_H - nh) // 2, (OLED_W - nw) // 2
        rgb_u8[y0:y0 + nh, x0:x0 + nw] = scaled
    else:  # Crop (Fill)
        scale = max(OLED_W / sw, OLED_H / sh)
        nw, nh = max(OLED_W, int(round(sw * scale))), max(OLED_H, int(round(sh * scale)))
        scaled = cv2.resize(rgb_full, (nw, nh), interpolation=cv2.INTER_AREA)
        left = (nw - OLED_W) // 2
        top = (nh - OLED_H) // 2
        rgb_u8 = scaled[top:top + OLED_H, left:left + OLED_W]

    arr_u8 = cv2.cvtColor(rgb_u8, cv2.COLOR_RGB2GRAY)

    if algo == "Smart Auto (Text + Scene)":
        lap = np.abs(cv2.Laplacian(arr_u8, cv2.CV_16S))
        flat_ratio = float(np.mean(lap < 15))
        if flat_ratio >= 0.82:
            # Text / title-card frame: Bernsen 7x7 local contrast keeps thin strokes crisp
            kernel = np.ones((7, 7), np.uint8)
            lmin = cv2.erode(arr_u8, kernel).astype(np.int16)
            lmax = cv2.dilate(arr_u8, kernel).astype(np.int16)
            contrast = lmax - lmin
            mid = (lmax + lmin) // 2
            contrast_thresh = max(10, 35 - (threshold - 128) // 4)
            arr = np.where(contrast >= contrast_thresh, arr_u8 > mid, arr_u8 >= threshold).astype(np.uint8)
        else:
            hsv = cv2.cvtColor(rgb_u8, cv2.COLOR_RGB2HSV).astype(np.float32)
            score = np.clip(arr_u8.astype(np.float32) - 0.25 * hsv[:, :, 1] + 50.0, 0, 255).astype(np.uint8)
            c_offset = 10.0 + (threshold - 128) / 8.0
            bin_img = cv2.adaptiveThreshold(
                score, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 35, c_offset
            )
            arr = (bin_img > 0).astype(np.uint8)
    elif algo == "Text-Aware (Bernsen)":
        # Detect text/high-contrast regions on the downscaled 128x64 image
        kernel = np.ones((7, 7), np.uint8)
        lmin = cv2.erode(arr_u8, kernel).astype(np.int16)
        lmax = cv2.dilate(arr_u8, kernel).astype(np.int16)
        contrast = lmax - lmin
        mid = (lmax + lmin) // 2
        contrast_thresh = max(10, 35 - (threshold - 128) // 4)
        arr = np.where(contrast >= contrast_thresh, arr_u8 > mid, arr_u8 >= threshold).astype(np.uint8)
    elif algo == "Floyd-Steinberg Dither":
        bw = Image.fromarray(arr_u8, mode="L").convert("1", dither=Image.Dither.FLOYDSTEINBERG)
        arr = (np.asarray(bw, dtype=np.uint8) != 0).astype(np.uint8)
    elif algo == "Bayer 4x4 (Clean Video)":
        bayer = np.tile(BAYER_4X4, (OLED_H // 4, OLED_W // 4))
        bias = (threshold - 128) / 255.0
        norm = (arr_u8.astype(np.float32) / 255.0) - bias
        arr = (norm > bayer).astype(np.uint8)
    elif algo == "Adaptive (Best for Text)":
        blur = cv2.GaussianBlur(arr_u8, (0, 0), sigmaX=1.0)
        sharp = cv2.addWeighted(arr_u8, 1.6, blur, -0.6, 0)
        c_offset = (threshold - 128) / 8.0
        bin_img = cv2.adaptiveThreshold(
            sharp, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 11, c_offset
        )
        arr = (bin_img > 0).astype(np.uint8)
    elif algo == "Edge + Sharpen":
        blur = cv2.GaussianBlur(arr_u8, (0, 0), sigmaX=1.0)
        sharp = cv2.addWeighted(arr_u8, 1.8, blur, -0.8, 0)
        edges = cv2.Canny(sharp, 50, 150)
        base = (sharp >= threshold).astype(np.uint8)
        if np.median(arr_u8) >= 128:
            arr = np.where(edges > 0, 0, base).astype(np.uint8)
        else:
            arr = np.where(edges > 0, 1, base).astype(np.uint8)
    else:  # Global Threshold
        arr = (arr_u8 >= threshold).astype(np.uint8)

    if invert:
        arr ^= 1
    return arr


class OledStudioApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("FastOled130 Studio — 1.30\" OLED Video Converter & Editor")
        self.root.geometry("1120x700")
        self.root.minsize(980, 620)

        self.source_path: Optional[str] = None
        self.source_pil_frames: List[Image.Image] = []
        self.frames: List[np.ndarray] = [np.zeros((OLED_H, OLED_W), dtype=np.uint8)]
        self.current_idx = 0
        self.playing = False
        self.play_job = None
        self.compressed_bytes = 0
        self.raw_bytes = FRAME_BYTES
        self._photo_ref = None

        # Tk Variables
        self.fps_var = tk.IntVar(value=20)
        self.max_frames_var = tk.IntVar(value=0)
        self.threshold_var = tk.IntVar(value=128)
        self.algo_var = tk.StringVar(value="Smart Auto (Text + Scene)")
        self.zoom_var = tk.DoubleVar(value=1.0)
        self.invert_var = tk.BooleanVar(value=False)
        self.fit_mode_var = tk.StringVar(value="Fit (Letterbox)")
        self.brush_size_var = tk.IntVar(value=1)
        self.symbol_var = tk.StringVar(value="my_video")
        self.status_var = tk.StringVar(value="Open a Video, GIF, Image, or .h header to begin.")

        self._build_ui()
        self._recalc_size()
        self._render_canvas()

    def _build_ui(self):
        # Top toolbar
        top = ttk.Frame(self.root, padding=8)
        top.pack(fill=tk.X)

        ttk.Button(top, text="Open Video / GIF / Image", command=self.open_media).pack(side=tk.LEFT, padx=3)
        ttk.Button(top, text="Open .h Header", command=self.open_header).pack(side=tk.LEFT, padx=3)
        ttk.Button(top, text="Export .h Header", command=self.export_header).pack(side=tk.LEFT, padx=3)
        ttk.Separator(top, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=8)
        ttk.Label(top, text="C Symbol:").pack(side=tk.LEFT)
        ttk.Entry(top, textvariable=self.symbol_var, width=16).pack(side=tk.LEFT, padx=4)

        # Main split
        main = ttk.Frame(self.root, padding=(8, 0, 8, 8))
        main.pack(fill=tk.BOTH, expand=True)

        # Left: Controls + Flash meter
        left = ttk.LabelFrame(main, text="Conversion & ESP32 Flash", padding=10, width=280)
        left.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 8))
        left.pack_propagate(False)

        self._add_labeled_spin(left, "Framerate (FPS):", self.fps_var, 1, 60)
        self._add_labeled_spin(left, "Max Frames (0=all):", self.max_frames_var, 0, 5000)

        ttk.Label(left, text="Downscale Mode:").pack(anchor=tk.W, pady=(6, 0))
        ttk.Combobox(
            left, textvariable=self.fit_mode_var, state="readonly",
            values=["Fit (Letterbox)", "Crop (Fill)", "Stretch"]
        ).pack(fill=tk.X, pady=(2, 6))

        ttk.Label(left, text="Algorithm:").pack(anchor=tk.W)
        ttk.Combobox(
            left, textvariable=self.algo_var, state="readonly",
            values=[
                "Smart Auto (Text + Scene)",
                "Text-Aware (Bernsen)",
                "Adaptive (Best for Text)",
                "Edge + Sharpen",
                "Bayer 4x4 (Clean Video)",
                "Floyd-Steinberg Dither",
                "Global Threshold",
            ]
        ).pack(fill=tk.X, pady=(2, 6))

        ttk.Label(left, text="Center Zoom (1.0x - 3.0x):").pack(anchor=tk.W)
        ttk.Scale(left, from_=1.0, to=3.0, variable=self.zoom_var, orient=tk.HORIZONTAL).pack(fill=tk.X)

        ttk.Label(left, text="Threshold / Sensitivity (0-255):").pack(anchor=tk.W, pady=(4, 0))
        ttk.Scale(left, from_=0, to=255, variable=self.threshold_var, orient=tk.HORIZONTAL).pack(fill=tk.X)

        ttk.Checkbutton(left, text="Invert Colors", variable=self.invert_var).pack(anchor=tk.W, pady=2)

        ttk.Button(left, text="Preview Current Frame", command=self.preview_current_frame).pack(fill=tk.X, pady=(6, 2))
        ttk.Button(left, text="Apply to All Frames", command=self.reconvert_source).pack(fill=tk.X, pady=(2, 10))

        # Flash meter
        ttk.Separator(left, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=6)
        ttk.Label(left, text="ESP32-WROOM-32 Flash Estimate", font=("Segoe UI", 9, "bold")).pack(anchor=tk.W)
        self.flash_bar = ttk.Progressbar(left, maximum=100, mode="determinate")
        self.flash_bar.pack(fill=tk.X, pady=6)
        self.flash_label = ttk.Label(left, text="", justify=tk.LEFT, wraplength=250)
        self.flash_label.pack(anchor=tk.W)

        # Right: Canvas + Playback + Pixel tools
        right = ttk.Frame(main)
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        canvas_frame = ttk.LabelFrame(right, text="128x64 OLED Editor (Left-click: Draw | Right-click: Erase)", padding=6)
        canvas_frame.pack(fill=tk.BOTH, expand=True)

        self.canvas = tk.Canvas(
            canvas_frame,
            width=OLED_W * PIXEL_SCALE,
            height=OLED_H * PIXEL_SCALE,
            bg="#05080d",
            highlightthickness=1,
            highlightbackground="#334155",
            cursor="crosshair",
        )
        self.canvas.pack(expand=True)
        self.canvas.bind("<Button-1>", lambda e: self._paint_at(e, 1))
        self.canvas.bind("<B1-Motion>", lambda e: self._paint_at(e, 1))
        self.canvas.bind("<Button-3>", lambda e: self._paint_at(e, 0))
        self.canvas.bind("<B3-Motion>", lambda e: self._paint_at(e, 0))

        # Timeline & playback controls
        tl = ttk.Frame(right, padding=(0, 6))
        tl.pack(fill=tk.X)

        self.play_btn = ttk.Button(tl, text="Play", width=8, command=self.toggle_play)
        self.play_btn.pack(side=tk.LEFT, padx=2)
        ttk.Button(tl, text="<", width=3, command=lambda: self.step_frame(-1)).pack(side=tk.LEFT, padx=2)
        ttk.Button(tl, text=">", width=3, command=lambda: self.step_frame(1)).pack(side=tk.LEFT, padx=2)

        self.frame_scale = ttk.Scale(tl, from_=0, to=0, orient=tk.HORIZONTAL, command=self._on_scrub)
        self.frame_scale.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=8)
        self.frame_lbl = ttk.Label(tl, text="Frame 1 / 1", width=16)
        self.frame_lbl.pack(side=tk.LEFT)

        # Pixel/frame editing tools
        tools = ttk.Frame(right, padding=(0, 4))
        tools.pack(fill=tk.X)
        ttk.Label(tools, text="Brush:").pack(side=tk.LEFT)
        for sz in (1, 2, 3):
            ttk.Radiobutton(tools, text=f"{sz}px", variable=self.brush_size_var, value=sz).pack(side=tk.LEFT, padx=2)
        ttk.Separator(tools, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=8)
        ttk.Button(tools, text="Invert Frame", command=self.invert_current_frame).pack(side=tk.LEFT, padx=2)
        ttk.Button(tools, text="Clear Frame", command=self.clear_current_frame).pack(side=tk.LEFT, padx=2)
        ttk.Button(tools, text="Duplicate", command=self.duplicate_current_frame).pack(side=tk.LEFT, padx=2)
        ttk.Button(tools, text="Delete", command=self.delete_current_frame).pack(side=tk.LEFT, padx=2)
        ttk.Button(tools, text="Recalc Size", command=self._recalc_size).pack(side=tk.RIGHT, padx=2)

        # Status bar
        ttk.Label(self.root, textvariable=self.status_var, relief=tk.SUNKEN, anchor=tk.W, padding=(8, 3)).pack(
            fill=tk.X, side=tk.BOTTOM
        )

    def _add_labeled_spin(self, parent, label, var, frm, to):
        row = ttk.Frame(parent)
        row.pack(fill=tk.X, pady=3)
        ttk.Label(row, text=label).pack(side=tk.LEFT)
        ttk.Spinbox(row, from_=frm, to=to, textvariable=var, width=7).pack(side=tk.RIGHT)

    def open_media(self):
        path = filedialog.askopenfilename(
            title="Select Video, GIF, or Image",
            filetypes=[
                ("Media files", "*.mp4 *.avi *.mov *.mkv *.webm *.gif *.png *.jpg *.jpeg *.bmp"),
                ("All files", "*.*"),
            ],
        )
        if not path:
            return
        self.source_path = path
        total_frames = detect_media_frame_count(path, max(1, self.fps_var.get()))
        if total_frames > 0:
            self.max_frames_var.set(total_frames)
        self.reconvert_source()

    def reconvert_source(self):
        if not self.source_path:
            messagebox.showinfo("No Source", "Open a video, GIF, or image first.")
            return
        self.stop_play()
        fps = max(1, self.fps_var.get())
        max_f = max(0, self.max_frames_var.get())
        ext = os.path.splitext(self.source_path)[1].lower()

        pil_frames: List[Image.Image] = []
        if ext in (".png", ".jpg", ".jpeg", ".bmp"):
            pil_frames.append(Image.open(self.source_path).copy())
        elif ext in (".gif", ".webp"):
            im = Image.open(self.source_path)
            for frame in ImageSequence.Iterator(im):
                pil_frames.append(frame.copy())
                if max_f > 0 and len(pil_frames) >= max_f:
                    break
        else:
            cap = cv2.VideoCapture(self.source_path)
            if not cap.isOpened():
                messagebox.showerror("Error", f"Could not open video: {self.source_path}")
                return
            src_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
            step = max(1.0, src_fps / fps)
            next_idx = 0.0
            idx = 0
            while True:
                ret, bgr = cap.read()
                if not ret:
                    break
                if idx >= int(round(next_idx)):
                    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
                    pil_frames.append(Image.fromarray(rgb))
                    next_idx += step
                    if max_f > 0 and len(pil_frames) >= max_f:
                        break
                idx += 1
            cap.release()

        if not pil_frames:
            messagebox.showerror("Error", "No frames could be read from the selected file.")
            return

        self.source_pil_frames = pil_frames
        fit_mode = self.fit_mode_var.get()
        thresh = self.threshold_var.get()
        algo = self.algo_var.get()
        invert = self.invert_var.get()
        zoom = self.zoom_var.get()

        self.frames = [pil_to_grid(f, fit_mode, thresh, algo, invert, zoom) for f in pil_frames]
        self.current_idx = 0
        self._update_timeline_bounds()
        self._recalc_size()
        self._render_canvas()
        self.status_var.set(f"Loaded {len(self.frames)} frames from {os.path.basename(self.source_path)} @ {fps} FPS ({algo})")

    def preview_current_frame(self):
        if not self.source_pil_frames or self.current_idx >= len(self.source_pil_frames):
            messagebox.showinfo("No Source", "Open a video, GIF, or image first to preview.")
            return
        self.stop_play()
        src = self.source_pil_frames[self.current_idx]
        self.frames[self.current_idx] = pil_to_grid(
            src,
            self.fit_mode_var.get(),
            self.threshold_var.get(),
            self.algo_var.get(),
            self.invert_var.get(),
            self.zoom_var.get(),
        )
        self._render_canvas()
        self.status_var.set(f"Previewed frame {self.current_idx + 1} with {self.algo_var.get()} (zoom {self.zoom_var.get():.1f}x)")

    def open_header(self):
        path = filedialog.askopenfilename(
            title="Open Exported .h Header",
            filetypes=[("C Header", "*.h"), ("All files", "*.*")],
        )
        if not path:
            return
        self.stop_play()
        try:
            frames, fps, sym = parse_oled_video_header(path)
        except Exception as exc:
            messagebox.showerror("Parse Error", str(exc))
            return

        if not frames:
            messagebox.showerror("Empty", "No frames found in header.")
            return

        self.source_path = None
        self.frames = frames
        self.fps_var.set(fps)
        self.symbol_var.set(sym)
        self.current_idx = 0
        self._update_timeline_bounds()
        self._recalc_size()
        self._render_canvas()
        self.status_var.set(f"Opened {os.path.basename(path)}: {len(frames)} frames @ {fps} FPS")

    def export_header(self):
        if not self.frames:
            return
        sym = re.sub(r"\W+", "_", self.symbol_var.get().strip() or "my_video")
        path = filedialog.asksaveasfilename(
            title="Save C Header",
            defaultextension=".h",
            initialfile=f"{sym}.h",
            initialdir=os.path.abspath(os.path.join(TOOLS_DIR, "..", "src")),
            filetypes=[("C Header", "*.h")],
        )
        if not path:
            return

        packed = [grid_to_page_lsb(g) for g in self.frames]
        stream, raw_total = encode_video_frames(packed)
        fps = max(1, self.fps_var.get())
        ratio = raw_total / max(len(stream), 1)

        header = f"""#pragma once
#include "FastOled130.h"

// Generated by oled_studio.py
// Frames: {len(packed)}, FPS: {fps}, Size: {len(stream)} bytes (raw {raw_total} bytes, {ratio:.2f}x compression)
{format_c_array(sym + "_stream", stream)}

static const OledVideo {sym} = {{
  {len(packed)}, {fps}, {sym}_stream
}};
"""
        with open(path, "w", encoding="utf-8") as f:
            f.write(header)

        self._recalc_size()
        self.status_var.set(f"Exported {os.path.basename(path)} ({len(stream):,} bytes, {ratio:.2f}x compression)")

    def _recalc_size(self):
        packed = [grid_to_page_lsb(g) for g in self.frames]
        stream, raw_total = encode_video_frames(packed)
        self.compressed_bytes = len(stream)
        self.raw_bytes = raw_total

        used_pct = min(100.0, (self.compressed_bytes / ESP32_AVAILABLE_FLASH) * 100.0)
        remaining = ESP32_AVAILABLE_FLASH - self.compressed_bytes
        ratio = raw_total / max(self.compressed_bytes, 1)
        dur_s = len(self.frames) / max(1, self.fps_var.get())

        self.flash_bar["value"] = used_pct
        status_note = "Fits in default 1.3MB app!" if remaining >= 0 else "Exceeds default 1.3MB app (use huge_app!)"
        self.flash_label.config(
            text=(
                f"Frames: {len(self.frames)} ({dur_s:.1f}s @ {self.fps_var.get()} FPS)\n"
                f"Compressed: {self.compressed_bytes:,} B ({self.compressed_bytes/1024:.1f} KB)\n"
                f"Raw size: {raw_total:,} B ({ratio:.2f}x saved)\n"
                f"ESP32 Free Flash: {remaining:,} B ({remaining/1024:.1f} KB)\n"
                f"{status_note}"
            )
        )

    def _update_timeline_bounds(self):
        max_idx = max(0, len(self.frames) - 1)
        self.frame_scale.configure(to=max_idx)
        self.frame_scale.set(self.current_idx)
        self.frame_lbl.config(text=f"Frame {self.current_idx + 1} / {len(self.frames)}")

    def _render_canvas(self):
        grid = self.frames[self.current_idx]
        rgb = np.zeros((OLED_H, OLED_W, 3), dtype=np.uint8)
        rgb[grid == 0] = (8, 12, 18)
        rgb[grid == 1] = (225, 245, 255)
        img = Image.fromarray(rgb, mode="RGB").resize(
            (OLED_W * PIXEL_SCALE, OLED_H * PIXEL_SCALE), Image.Resampling.NEAREST
        )
        self._photo_ref = ImageTk.PhotoImage(img)
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, anchor=tk.NW, image=self._photo_ref)
        self.frame_lbl.config(text=f"Frame {self.current_idx + 1} / {len(self.frames)}")

    def _paint_at(self, event, val: int):
        gx = event.x // PIXEL_SCALE
        gy = event.y // PIXEL_SCALE
        b = self.brush_size_var.get()
        r = b // 2
        grid = self.frames[self.current_idx]
        for dy in range(-r, b - r):
            for dx in range(-r, b - r):
                px, py = gx + dx, gy + dy
                if 0 <= px < OLED_W and 0 <= py < OLED_H:
                    grid[py, px] = val
        self._render_canvas()

    def _on_scrub(self, val):
        idx = int(round(float(val)))
        idx = max(0, min(len(self.frames) - 1, idx))
        if idx != self.current_idx:
            self.current_idx = idx
            self._render_canvas()

    def step_frame(self, delta: int):
        self.stop_play()
        self.current_idx = (self.current_idx + delta) % len(self.frames)
        self.frame_scale.set(self.current_idx)
        self._render_canvas()

    def toggle_play(self):
        if self.playing:
            self.stop_play()
        else:
            self.playing = True
            self.play_btn.config(text="Pause")
            self._play_tick()

    def stop_play(self):
        self.playing = False
        self.play_btn.config(text="Play")
        if self.play_job is not None:
            self.root.after_cancel(self.play_job)
            self.play_job = None

    def _play_tick(self):
        if not self.playing:
            return
        self.current_idx = (self.current_idx + 1) % len(self.frames)
        self.frame_scale.set(self.current_idx)
        self._render_canvas()
        delay_ms = max(15, int(round(1000.0 / max(1, self.fps_var.get()))))
        self.play_job = self.root.after(delay_ms, self._play_tick)

    def invert_current_frame(self):
        self.frames[self.current_idx] ^= 1
        self._render_canvas()

    def clear_current_frame(self):
        self.frames[self.current_idx].fill(0)
        self._render_canvas()

    def duplicate_current_frame(self):
        self.frames.insert(self.current_idx + 1, self.frames[self.current_idx].copy())
        self.current_idx += 1
        self._update_timeline_bounds()
        self._recalc_size()
        self._render_canvas()

    def delete_current_frame(self):
        if len(self.frames) <= 1:
            self.clear_current_frame()
            return
        del self.frames[self.current_idx]
        self.current_idx = min(self.current_idx, len(self.frames) - 1)
        self._update_timeline_bounds()
        self._recalc_size()
        self._render_canvas()


def main():
    root = tk.Tk()
    OledStudioApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
