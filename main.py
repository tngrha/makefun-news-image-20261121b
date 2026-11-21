#!/usr/bin/env python3
"""The World on One Page - Saturday 21 November 2026.

A deterministic, pure-stdlib renderer for the make.fun daily news-image
competition (news-image-20261121). It draws a dense editorial front page
(1 hero story, 11 illustrated panels, 38 one-line briefs, a day-in-numbers
strip, a UTC timeline, a date-check line and a pinned world map) onto a
1024x1024 RGB framebuffer with a hand-rolled 5x7 bitmap font, then encodes
the result as a PNG with a hand-written encoder. No network, no clock, no
randomness: two runs produce byte-identical output.

Usage: python3 main.py [output_path]   (default /output/entry.png)
"""

import binascii
import math
import os
import struct
import sys
import zlib

W = 1024
H = 1024

# --------------------------------------------------------------------------
# Framebuffer
# --------------------------------------------------------------------------

class Canvas:
    def __init__(self, width, height, bg=(255, 255, 255)):
        self.w = width
        self.h = height
        self.px = bytearray(width * height * 3)
        r, g, b = bg
        for i in range(0, len(self.px), 3):
            self.px[i] = r
            self.px[i + 1] = g
            self.px[i + 2] = b

    def set(self, x, y, color):
        if 0 <= x < self.w and 0 <= y < self.h:
            i = (y * self.w + x) * 3
            self.px[i] = color[0]
            self.px[i + 1] = color[1]
            self.px[i + 2] = color[2]

    def fill_rect(self, x, y, w, h, color):
        x0 = max(0, x)
        y0 = max(0, y)
        x1 = min(self.w, x + w)
        y1 = min(self.h, y + h)
        if x1 <= x0 or y1 <= y0:
            return
        row = bytes(color) * (x1 - x0)
        for yy in range(y0, y1):
            i = (yy * self.w + x0) * 3
            self.px[i:i + len(row)] = row

    def rect_outline(self, x, y, w, h, color, t=1):
        self.fill_rect(x, y, w, t, color)
        self.fill_rect(x, y + h - t, w, t, color)
        self.fill_rect(x, y, t, h, color)
        self.fill_rect(x + w - t, y, t, h, color)

    def hline(self, x, y, length, color, t=1):
        self.fill_rect(x, y, length, t, color)

    def vline(self, x, y, length, color, t=1):
        self.fill_rect(x, y, t, length, color)

    def line(self, x0, y0, x1, y1, color, t=1):
        dx = abs(x1 - x0)
        dy = -abs(y1 - y0)
        sx = 1 if x0 < x1 else -1
        sy = 1 if y0 < y1 else -1
        err = dx + dy
        x, y = x0, y0
        while True:
            if t == 1:
                self.set(x, y, color)
            else:
                self.fill_rect(x - t // 2, y - t // 2, t, t, color)
            if x == x1 and y == y1:
                break
            e2 = 2 * err
            if e2 >= dy:
                err += dy
                x += sx
            if e2 <= dx:
                err += dx
                y += sy

    def fill_circle(self, cx, cy, r, color):
        r2 = r * r
        for yy in range(cy - r, cy + r + 1):
            for xx in range(cx - r, cx + r + 1):
                if (xx - cx) * (xx - cx) + (yy - cy) * (yy - cy) <= r2:
                    self.set(xx, yy, color)

    def circle_outline(self, cx, cy, r, color, t=1):
        steps = max(24, r * 8)
        for k in range(steps + 1):
            a = 2.0 * math.pi * k / steps
            x = int(round(cx + r * math.cos(a)))
            y = int(round(cy + r * math.sin(a)))
            if t == 1:
                self.set(x, y, color)
            else:
                self.fill_rect(x - t // 2, y - t // 2, t, t, color)

    def fill_ellipse(self, cx, cy, rx, ry, color):
        for yy in range(cy - ry, cy + ry + 1):
            for xx in range(cx - rx, cx + rx + 1):
                v = ((xx - cx) / rx) ** 2 + ((yy - cy) / ry) ** 2
                if v <= 1.0:
                    self.set(xx, yy, color)

    def fill_poly(self, pts, color):
        # Scanline even-odd fill. pts: list of (x, y) ints.
        ys = [p[1] for p in pts]
        y_lo = max(0, min(ys))
        y_hi = min(self.h - 1, max(ys))
        n = len(pts)
        for yy in range(y_lo, y_hi + 1):
            nodes = []
            for i in range(n):
                x0, y0 = pts[i]
                x1, y1 = pts[(i + 1) % n]
                if y0 == y1:
                    continue
                if (y0 <= yy < y1) or (y1 <= yy < y0):
                    xint = x0 + (yy - y0) * (x1 - x0) / (y1 - y0)
                    nodes.append(xint)
            nodes.sort()
            for j in range(0, len(nodes) - 1, 2):
                xa = max(0, int(round(nodes[j])))
                xb = min(self.w - 1, int(round(nodes[j + 1])))
                if xb >= xa:
                    row = bytes(color) * (xb - xa + 1)
                    i = (yy * self.w + xa) * 3
                    self.px[i:i + len(row)] = row

    def poly_outline(self, pts, color, t=1):
        n = len(pts)
        for i in range(n):
            x0, y0 = pts[i]
            x1, y1 = pts[(i + 1) % n]
            self.line(x0, y0, x1, y1, color, t)

    def to_png_bytes(self):
        raw = bytearray()
        stride = self.w * 3
        for yy in range(self.h):
            raw.append(0)  # filter type 0 (None) for every row
            start = yy * stride
            raw.extend(self.px[start:start + stride])
        idat = zlib.compress(bytes(raw), 6)

        def chunk(tag, data):
            body = tag + data
            crc = binascii.crc32(body) & 0xFFFFFFFF
            return struct.pack(">I", len(data)) + body + struct.pack(">I", crc)

        ihdr = struct.pack(">IIBBBBB", self.w, self.h, 8, 2, 0, 0, 0)
        png = b"\x89PNG\r\n\x1a\n"
        png += chunk(b"IHDR", ihdr)
        png += chunk(b"IDAT", idat)
        png += chunk(b"IEND", b"")
        return png


# --------------------------------------------------------------------------
# 5x7 bitmap font (each glyph: 7 strings of 5 chars, '#' = lit pixel,
# first string is the top row)
# --------------------------------------------------------------------------

FONT = {
    " ": (".....", ".....", ".....", ".....", ".....", ".....", "....."),
    "!": ("..#..", "..#..", "..#..", "..#..", "..#..", ".....", "..#.."),
    '"': (".#.#.", ".#.#.", ".#.#.", ".....", ".....", ".....", "....."),
    "#": (".#.#.", ".#.#.", "#####", ".#.#.", "#####", ".#.#.", ".#.#."),
    "$": ("..#..", ".####", "#.#..", ".###.", "..#.#", "####.", "..#.."),
    "%": ("##..#", "##.#.", "...#.", "..#..", ".#...", "#..##", "#..##"),
    "&": (".##..", "#..#.", "#.#..", ".#...", "#.#.#", "#..#.", ".##.#"),
    "'": ("..#..", "..#..", ".#...", ".....", ".....", ".....", "....."),
    "(": ("...#.", "..#..", ".#...", ".#...", ".#...", "..#..", "...#."),
    ")": (".#...", "..#..", "...#.", "...#.", "...#.", "..#..", ".#..."),
    "*": (".....", ".#.#.", "..#..", "#####", "..#..", ".#.#.", "....."),
    "+": (".....", "..#..", "..#..", "#####", "..#..", "..#..", "....."),
    ",": (".....", ".....", ".....", ".....", ".##..", ".##..", ".#..."),
    "-": (".....", ".....", ".....", "#####", ".....", ".....", "....."),
    ".": (".....", ".....", ".....", ".....", ".....", ".##..", ".##.."),
    "/": ("....#", "...#.", "...#.", "..#..", ".#...", ".#...", "#...."),
    "0": (".###.", "#..##", "#.#.#", "##..#", "#...#", "#...#", ".###."),
    "1": ("..#..", ".##..", "..#..", "..#..", "..#..", "..#..", ".###."),
    "2": (".###.", "#...#", "....#", "...#.", "..#..", ".#...", "#####"),
    "3": ("#####", "...#.", "..#..", "...#.", "....#", "#...#", ".###."),
    "4": ("...#.", "..##.", ".#.#.", "#..#.", "#####", "...#.", "...#."),
    "5": ("#####", "#....", "####.", "....#", "....#", "#...#", ".###."),
    "6": ("..#..", ".#...", "#....", "####.", "#...#", "#...#", ".###."),
    "7": ("#####", "....#", "...#.", "..#..", "..#..", "..#..", "..#.."),
    "8": (".###.", "#...#", "#...#", ".###.", "#...#", "#...#", ".###."),
    "9": (".###.", "#...#", "#...#", ".####", "....#", "...#.", "..#.."),
    ":": (".....", ".##..", ".##..", ".....", ".##..", ".##..", "....."),
    ";": (".....", ".##..", ".##..", ".....", ".##..", ".##..", ".#..."),
    "<": ("...#.", "..#..", ".#...", "#....", ".#...", "..#..", "...#."),
    "=": (".....", ".....", "#####", ".....", "#####", ".....", "....."),
    ">": (".#...", "..#..", "...#.", "....#", "...#.", "..#..", ".#..."),
    "?": (".###.", "#...#", "....#", "...#.", "..#..", ".....", "..#.."),
    "@": (".###.", "#...#", "#.###", "#.#.#", "#.###", "#....", ".###."),
    "A": (".###.", "#...#", "#...#", "#####", "#...#", "#...#", "#...#"),
    "B": ("####.", "#...#", "#...#", "####.", "#...#", "#...#", "####."),
    "C": (".####", "#....", "#....", "#....", "#....", "#....", ".####"),
    "D": ("####.", "#...#", "#...#", "#...#", "#...#", "#...#", "####."),
    "E": ("#####", "#....", "#....", "####.", "#....", "#....", "#####"),
    "F": ("#####", "#....", "#....", "####.", "#....", "#....", "#...."),
    "G": (".####", "#....", "#....", "#.###", "#...#", "#...#", ".###."),
    "H": ("#...#", "#...#", "#...#", "#####", "#...#", "#...#", "#...#"),
    "I": (".###.", "..#..", "..#..", "..#..", "..#..", "..#..", ".###."),
    "J": ("..###", "...#.", "...#.", "...#.", "...#.", "#..#.", ".##.."),
    "K": ("#...#", "#..#.", "#.#..", "##...", "#.#..", "#..#.", "#...#"),
    "L": ("#....", "#....", "#....", "#....", "#....", "#....", "#####"),
    "M": ("#...#", "##.##", "#.#.#", "#.#.#", "#...#", "#...#", "#...#"),
    "N": ("#...#", "##..#", "##..#", "#.#.#", "#..##", "#..##", "#...#"),
    "O": (".###.", "#...#", "#...#", "#...#", "#...#", "#...#", ".###."),
    "P": ("####.", "#...#", "#...#", "####.", "#....", "#....", "#...."),
    "Q": (".###.", "#...#", "#...#", "#...#", "#.#.#", "#..#.", ".##.#"),
    "R": ("####.", "#...#", "#...#", "####.", "#.#..", "#..#.", "#...#"),
    "S": (".####", "#....", "#....", ".###.", "....#", "....#", "####."),
    "T": ("#####", "..#..", "..#..", "..#..", "..#..", "..#..", "..#.."),
    "U": ("#...#", "#...#", "#...#", "#...#", "#...#", "#...#", ".###."),
    "V": ("#...#", "#...#", "#...#", "#...#", "#...#", ".#.#.", "..#.."),
    "W": ("#...#", "#...#", "#...#", "#.#.#", "#.#.#", "##.##", "#...#"),
    "X": ("#...#", ".#.#.", "..#..", "..#..", "..#..", ".#.#.", "#...#"),
    "Y": ("#...#", "#...#", ".#.#.", "..#..", "..#..", "..#..", "..#.."),
    "Z": ("#####", "....#", "...#.", "..#..", ".#...", "#....", "#####"),
    "[": ("..###", "..#..", "..#..", "..#..", "..#..", "..#..", "..###"),
    "]": ("###..", "..#..", "..#..", "..#..", "..#..", "..#..", "###.."),
    "^": ("..#..", ".#.#.", ".....", ".....", ".....", ".....", "....."),
    "_": (".....", ".....", ".....", ".....", ".....", ".....", "#####"),
    "`": ("..#..", "..#..", "...#.", ".....", ".....", ".....", "....."),
    "a": (".....", ".....", ".###.", "....#", ".####", "#...#", ".####"),
    "b": ("#....", "#....", "####.", "#...#", "#...#", "#...#", "####."),
    "c": (".....", ".....", ".####", "#....", "#....", "#....", ".####"),
    "d": ("....#", "....#", ".####", "#...#", "#...#", "#...#", ".####"),
    "e": (".....", ".....", ".###.", "#...#", "#####", "#....", ".###."),
    "f": ("..###", ".#...", "#####", ".#...", ".#...", ".#...", ".#..."),
    "g": (".....", ".####", "#...#", "#...#", ".####", "....#", ".###."),
    "h": ("#....", "#....", "####.", "#...#", "#...#", "#...#", "#...#"),
    "i": ("..#..", ".....", ".##..", "..#..", "..#..", "..#..", ".###."),
    "j": ("...#.", ".....", "..##.", "...#.", "...#.", "#..#.", ".##.."),
    "k": ("#....", "#....", "#..#.", "#.#..", "##...", "#.#..", "#..#."),
    "l": (".##..", "..#..", "..#..", "..#..", "..#..", "..#..", ".###."),
    "m": (".....", ".....", "##.#.", "#.#.#", "#.#.#", "#.#.#", "#.#.#"),
    "n": (".....", ".....", "####.", "#...#", "#...#", "#...#", "#...#"),
    "o": (".....", ".....", ".###.", "#...#", "#...#", "#...#", ".###."),
    "p": (".....", "####.", "#...#", "#...#", "####.", "#....", "#...."),
    "q": (".....", ".####", "#...#", "#...#", ".####", "....#", "...##"),
    "r": (".....", ".....", "#.##.", "##..#", "#....", "#....", "#...."),
    "s": (".....", ".....", ".####", "#....", ".###.", "....#", "####."),
    "t": (".#...", ".#...", "#####", ".#...", ".#...", ".#...", "..##."),
    "u": (".....", ".....", "#...#", "#...#", "#...#", "#..##", ".##.#"),
    "v": (".....", ".....", "#...#", "#...#", "#...#", ".#.#.", "..#.."),
    "w": (".....", ".....", "#...#", "#...#", "#.#.#", "#.#.#", ".#.#."),
    "x": (".....", ".....", "#...#", ".#.#.", "..#..", ".#.#.", "#...#"),
    "y": (".....", "#...#", "#...#", "#...#", ".####", "....#", ".###."),
    "z": (".....", ".....", "#####", "...#.", "..#..", ".#...", "#####"),
    "{": ("...##", "..#..", "..#..", ".##..", "..#..", "..#..", "...##"),
    "|": ("..#..", "..#..", "..#..", "..#..", "..#..", "..#..", "..#.."),
    "}": ("##...", "..#..", "..#..", "..##.", "..#..", "..#..", "##..."),
    "~": (".....", ".....", ".##.#", "#..##", ".....", ".....", "....."),
}

FALLBACK = ("#####", "#...#", "#...#", "#...#", "#...#", "#...#", "#####")


def clean(text):
    """Restrict text to the glyphs this font can draw."""
    out = []
    for ch in text:
        if ch in FONT:
            out.append(ch)
        elif ch in "’‘":
            out.append("'")
        elif ch in "“”":
            out.append('"')
        elif ch in "–—":
            out.append("-")
        elif ch == "•":
            out.append("-")
        elif ch == "€":
            out.append("EUR")
        elif ch == "×":
            out.append("x")
        else:
            out.append("?")
    return "".join(out)


def text_width(text, scale=1):
    if not text:
        return 0
    return len(text) * 6 * scale - scale


def draw_text(cv, x, y, text, color, scale=1):
    """Draw text with its top-left corner at (x, y). Returns the end x."""
    cx = x
    for ch in clean(text):
        glyph = FONT.get(ch, FALLBACK)
        for row in range(7):
            line = glyph[row]
            for col in range(5):
                if line[col] == "#":
                    cv.fill_rect(cx + col * scale, y + row * scale,
                                 scale, scale, color)
        cx += 6 * scale
    return cx


def wrap(text, max_chars):
    """Word-wrap text to lines of at most max_chars characters."""
    words = clean(text).split()
    lines = []
    cur = ""
    for word in words:
        if not cur:
            cur = word
        elif len(cur) + 1 + len(word) <= max_chars:
            cur += " " + word
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def draw_wrapped(cv, x, y, text, color, scale, max_chars, line_h, max_lines=None):
    lines = wrap(text, max_chars)
    if max_lines is not None:
        lines = lines[:max_lines]
    for i, ln in enumerate(lines):
        draw_text(cv, x, y + i * line_h, ln, color, scale)
    return y + len(lines) * line_h


# --------------------------------------------------------------------------
# Palette
# --------------------------------------------------------------------------

INK = (18, 18, 18)
PAPER = (255, 255, 255)
NAVY = (14, 36, 82)
RED = (178, 24, 24)
GREY = (110, 110, 110)
LIGHT = (238, 238, 238)
GOLD = (184, 134, 11)

REGIONS = {
    "NORTH AMERICA": (37, 99, 235),
    "SOUTH AMERICA": (22, 163, 74),
    "EUROPE": (220, 38, 38),
    "AFRICA": (147, 51, 234),
    "ASIA": (234, 88, 12),
    "OCEANIA": (219, 39, 119),
}
REGION_SHORT = {
    "NORTH AMERICA": "N. AMERICA",
    "SOUTH AMERICA": "S. AMERICA",
    "EUROPE": "EUROPE",
    "AFRICA": "AFRICA",
    "ASIA": "ASIA",
    "OCEANIA": "OCEANIA",
}

# --------------------------------------------------------------------------
# World map: simplified continent polygons in (lon, lat), equirectangular
# --------------------------------------------------------------------------

LAND = [
    # North America
    [(-168, 66), (-150, 70), (-135, 69), (-125, 70), (-115, 68), (-100, 69),
     (-92, 66), (-85, 66), (-80, 63), (-79, 58), (-72, 58), (-65, 50),
     (-56, 47), (-60, 45), (-66, 44), (-70, 43), (-71, 41), (-74, 39),
     (-76, 36), (-76, 34), (-79, 33), (-81, 31), (-81, 26), (-80, 25),
     (-81, 26), (-82, 28), (-84, 30), (-87, 30), (-89, 29), (-92, 29),
     (-95, 29), (-97, 26), (-97, 24), (-97, 20), (-94, 18), (-91, 19),
     (-90, 21), (-87, 21), (-87, 18), (-88, 16), (-85, 12), (-83, 9),
     (-79, 9), (-78, 8), (-77, 7), (-79, 9), (-82, 9), (-84, 13),
     (-87, 13), (-90, 14), (-92, 15), (-95, 16), (-97, 16), (-100, 18),
     (-103, 19), (-105, 20), (-107, 22), (-110, 23), (-112, 27),
     (-114, 30), (-116, 32), (-118, 34), (-121, 36), (-123, 39),
     (-124, 43), (-124, 47), (-127, 50), (-131, 54), (-135, 57),
     (-140, 60), (-147, 61), (-152, 58), (-158, 56), (-162, 58),
     (-166, 60), (-168, 66)],
    # Greenland
    [(-46, 60), (-42, 62), (-40, 64), (-32, 68), (-24, 70), (-20, 73),
     (-22, 76), (-28, 80), (-38, 82), (-50, 81), (-56, 78), (-55, 73),
     (-54, 69), (-52, 65), (-50, 62), (-46, 60)],
    # South America
    [(-77, 7), (-74, 10), (-71, 12), (-66, 10), (-61, 9), (-56, 6),
     (-51, 4), (-50, 0), (-45, -2), (-41, -3), (-35, -7), (-35, -9),
     (-38, -13), (-39, -16), (-39, -19), (-41, -22), (-46, -24),
     (-48, -26), (-50, -29), (-52, -33), (-55, -34), (-57, -35),
     (-58, -37), (-62, -39), (-62, -41), (-64, -42), (-65, -44),
     (-65, -47), (-66, -50), (-68, -52), (-68, -54), (-70, -54),
     (-72, -52), (-74, -49), (-74, -45), (-73, -41), (-73, -37),
     (-72, -33), (-71, -29), (-70, -24), (-70, -20), (-70, -18),
     (-73, -16), (-76, -14), (-77, -11), (-79, -8), (-81, -5),
     (-81, -2), (-80, 1), (-78, 3), (-77, 5), (-77, 7)],
    # Africa
    [(-17, 15), (-17, 19), (-16, 23), (-15, 26), (-13, 28), (-10, 30),
     (-8, 33), (-6, 35), (-2, 35), (3, 37), (8, 37), (10, 37),
     (11, 34), (15, 32), (20, 32), (25, 32), (30, 31), (32, 31),
     (33, 28), (34, 27), (35, 24), (37, 22), (38, 21), (39, 20),
     (40, 17), (41, 15), (42, 13), (43, 12), (45, 11), (48, 11),
     (51, 12), (51, 10), (49, 7), (46, 4), (42, -1), (41, -4),
     (40, -8), (40, -12), (40, -15), (39, -18), (37, -21), (36, -24),
     (35, -27), (33, -29), (31, -30), (29, -32), (27, -33), (24, -34),
     (20, -35), (18, -34), (17, -31), (16, -28), (15, -25), (14, -22),
     (13, -19), (12, -16), (13, -12), (12, -9), (10, -5), (9, -2),
     (9, 2), (9, 4), (7, 4), (4, 6), (0, 5), (-3, 5), (-6, 4),
     (-8, 5), (-11, 7), (-13, 8), (-15, 10), (-16, 12), (-17, 15)],
    # Madagascar
    [(44, -16), (47, -14), (49, -15), (50, -18), (49, -21), (47, -24),
     (45, -25), (44, -23), (43, -20), (44, -16)],
    # Europe (mainland, rough)
    [(-9, 37), (-9, 39), (-9, 43), (-7, 44), (-4, 44), (-2, 44),
     (-1, 45), (-2, 47), (-5, 48), (-4, 49), (0, 49), (2, 50),
     (4, 52), (6, 53), (8, 54), (8, 55), (10, 56), (12, 55),
     (14, 54), (18, 55), (20, 54), (22, 55), (24, 57), (25, 59),
     (28, 59), (30, 60), (30, 62), (28, 65), (28, 68), (26, 70),
     (24, 71), (20, 70), (16, 68), (13, 66), (10, 63), (6, 62),
     (5, 60), (5, 58), (7, 57), (9, 56), (12, 55), (18, 56),
     (21, 55), (24, 58), (28, 59), (30, 60), (32, 55), (30, 50),
     (28, 46), (26, 45), (24, 44), (23, 41), (23, 40), (22, 38),
     (21, 37), (19, 40), (18, 41), (16, 40), (16, 39), (17, 40),
     (15, 41), (14, 42), (13, 44), (12, 44), (11, 44), (10, 44),
     (8, 44), (7, 44), (6, 43), (3, 43), (3, 42), (0, 40),
     (-1, 38), (-2, 37), (-5, 36), (-7, 37), (-9, 37)],
    # Britain
    [(-5, 50), (-3, 51), (-2, 52), (-1, 53), (-2, 55), (-3, 57),
     (-4, 58), (-5, 57), (-5, 55), (-4, 53), (-5, 52), (-5, 50)],
    # Asia (mainland, rough)
    [(28, 41), (32, 41), (36, 41), (40, 41), (44, 41), (48, 41),
     (50, 40), (52, 38), (54, 37), (56, 36), (58, 35), (60, 33),
     (61, 31), (62, 30), (63, 29), (65, 29), (67, 30), (69, 31),
     (70, 33), (71, 34), (72, 36), (73, 37), (75, 37), (77, 36),
     (79, 35), (80, 34), (82, 33), (84, 32), (86, 31), (88, 30),
     (90, 29), (92, 28), (94, 27), (96, 26), (97, 24), (98, 22),
     (99, 20), (100, 18), (101, 16), (102, 14), (103, 12),
     (104, 10), (105, 8), (106, 9), (107, 11), (108, 13),
     (109, 15), (110, 18), (111, 20), (112, 22), (113, 23),
     (115, 24), (117, 25), (119, 26), (120, 28), (121, 30),
     (122, 32), (121, 34), (120, 36), (119, 38), (120, 40),
     (121, 41), (122, 42), (124, 43), (126, 44), (128, 45),
     (130, 46), (132, 47), (134, 48), (136, 49), (138, 50),
     (140, 52), (142, 54), (144, 56), (146, 58), (148, 60),
     (150, 62), (152, 64), (154, 66), (156, 68), (158, 69),
     (160, 69), (162, 68), (164, 67), (166, 66), (168, 65),
     (170, 64), (172, 63), (174, 62), (176, 61), (178, 62),
     (179, 64), (179, 66), (177, 68), (174, 69), (170, 70),
     (165, 70), (160, 70), (155, 71), (150, 72), (145, 72),
     (140, 72), (135, 72), (130, 72), (125, 72), (120, 73),
     (115, 73), (110, 74), (105, 77), (100, 77), (95, 76),
     (90, 75), (85, 73), (80, 72), (75, 72), (70, 72), (65, 71),
     (60, 70), (55, 68), (50, 68), (45, 67), (40, 66), (35, 65),
     (33, 64), (32, 62), (31, 60), (30, 56), (30, 52), (29, 48),
     (28, 45), (28, 41)],
    # Arabia (drawn over Asia base for shape)
    [(34, 28), (36, 27), (38, 25), (40, 22), (41, 19), (42, 16),
     (43, 13), (44, 12), (46, 12), (48, 13), (50, 14), (52, 15),
     (54, 16), (56, 17), (57, 18), (58, 20), (59, 22), (59, 23),
     (57, 24), (55, 24), (53, 25), (51, 26), (50, 27), (49, 28),
     (48, 29), (47, 29), (46, 30), (44, 31), (42, 32), (40, 32),
     (38, 31), (36, 30), (35, 29), (34, 28)],
    # India triangle (over Asia base)
    [(68, 24), (70, 22), (72, 20), (73, 18), (73, 16), (74, 14),
     (75, 12), (76, 10), (77, 8), (78, 9), (79, 11), (80, 13),
     (80, 15), (81, 17), (82, 18), (84, 19), (86, 20), (87, 21),
     (88, 22), (89, 23), (90, 24), (91, 25), (92, 26), (93, 27),
     (94, 28), (95, 28), (96, 27), (97, 25), (97, 23), (96, 21),
     (95, 19), (94, 17), (93, 15), (92, 13), (91, 11), (90, 9),
     (88, 7), (86, 6), (84, 6), (82, 7), (80, 8), (78, 9),
     (77, 10), (76, 12), (75, 14), (74, 16), (73, 18), (72, 20),
     (70, 22), (68, 24)],
    # Japan
    [(130, 31), (132, 33), (134, 34), (136, 35), (138, 36),
     (140, 37), (141, 39), (141, 41), (142, 43), (143, 44),
     (144, 44), (143, 43), (142, 42), (141, 40), (140, 38),
     (139, 37), (137, 36), (135, 35), (133, 34), (131, 32),
     (130, 31)],
    # Indonesia / SE Asia islands (rough blobs)
    [(95, 5), (100, 2), (104, -2), (106, -5), (105, -6), (101, -3),
     (97, 1), (95, 5)],
    [(109, -6), (114, -6), (114, -8), (109, -8), (109, -6)],
    [(109, 0), (113, 3), (117, 4), (119, 1), (117, -2), (113, -3),
     (109, -1), (109, 0)],
    [(131, -1), (136, -2), (141, -3), (146, -5), (147, -7),
     (142, -7), (137, -6), (132, -4), (131, -1)],
    # Australia
    [(113, -22), (113, -26), (114, -30), (115, -33), (117, -35),
     (120, -34), (124, -33), (128, -32), (131, -32), (133, -32),
     (135, -34), (137, -35), (138, -35), (139, -37), (141, -38),
     (144, -38), (146, -38), (148, -38), (150, -37), (151, -34),
     (152, -31), (153, -28), (153, -25), (151, -23), (149, -20),
     (147, -19), (146, -18), (145, -15), (143, -13), (142, -11),
     (142, -13), (142, -15), (141, -17), (140, -18), (137, -17),
     (136, -15), (135, -12), (134, -12), (132, -11), (130, -12),
     (128, -13), (126, -14), (124, -16), (122, -17), (120, -19),
     (117, -20), (114, -21), (113, -22)],
    # New Zealand
    [(167, -46), (169, -44), (171, -42), (172, -41), (173, -40),
     (174, -39), (175, -38), (176, -37), (177, -37), (178, -38),
     (177, -39), (176, -40), (175, -41), (174, -41), (173, -42),
     (172, -43), (170, -44), (168, -46), (167, -46)],
]

MAP_LON0, MAP_LON1 = -180.0, 180.0
MAP_LAT0, MAP_LAT1 = 78.0, -58.0  # top, bottom


def map_xy(mx, my, mw, mh, lon, lat):
    x = mx + (lon - MAP_LON0) / (MAP_LON1 - MAP_LON0) * mw
    y = my + (MAP_LAT0 - lat) / (MAP_LAT0 - MAP_LAT1) * mh
    return int(round(x)), int(round(y))


def draw_map(cv, mx, my, mw, mh, pins):
    """pins: list of dicts with lon, lat, region, num."""
    ocean = (214, 230, 244)
    land = (196, 216, 170)
    land_edge = (90, 120, 70)
    grat = (170, 195, 220)
    cv.fill_rect(mx, my, mw, mh, ocean)
    # graticule every 30 degrees
    for lon in range(-180, 181, 30):
        x, _ = map_xy(mx, my, mw, mh, lon, 0)
        cv.vline(x, my, mh, grat)
    for lat in range(-60, 79, 30):
        _, y = map_xy(mx, my, mw, mh, 0, lat)
        cv.hline(mx, y, mw, grat)
    # equator + prime meridian slightly stronger
    _, ey = map_xy(mx, my, mw, mh, 0, 0)
    cv.hline(mx, ey, mw, (150, 180, 210))
    for poly in LAND:
        pts = [map_xy(mx, my, mw, mh, lon, lat) for lon, lat in poly]
        cv.fill_poly(pts, land)
        cv.poly_outline(pts, land_edge)
    cv.rect_outline(mx, my, mw, mh, INK, 1)
    # pins (tiny display nudges keep near-identical locations separable)
    nudge = {1: (0.0, 2.2), 5: (1.2, -2.2)}
    for pin in pins:
        dlon, dlat = nudge.get(pin["num"], (0.0, 0.0))
        x, y = map_xy(mx, my, mw, mh, pin["lon"] + dlon, pin["lat"] + dlat)
        col = REGIONS[pin["region"]]
        cv.fill_circle(x, y, 9, col)
        cv.circle_outline(x, y, 9, PAPER, 2)
        label = str(pin["num"])
        tw = text_width(label, 1)
        draw_text(cv, x - tw // 2, y - 3, label, PAPER, 1)

# --------------------------------------------------------------------------
# Pictograms: small flat geometric icons drawn inside an (x, y, s) box
# --------------------------------------------------------------------------

def icon_scales(cv, x, y, s, c):
    cx = x + s // 2
    cv.fill_rect(cx - 2, y + 4, 4, s - 12, c)
    cv.fill_rect(x + 3, y + 4, s - 6, 3, c)
    cv.fill_rect(cx - 8, y + s - 7, 16, 3, c)
    cv.fill_circle(cx, y + 3, 3, c)
    for sx in (x + 6, x + s - 6):
        cv.line(sx, y + 7, sx - 5, y + s - 14, c)
        cv.line(sx, y + 7, sx + 5, y + s - 14, c)
        cv.fill_poly([(sx - 7, y + s - 14), (sx + 7, y + s - 14),
                      (sx + 4, y + s - 10), (sx - 4, y + s - 10)], c)


def icon_missile(cv, x, y, s, c):
    cv.fill_poly([(x + s - 4, y + 4), (x + s - 14, y + 12),
                  (x + 12, y + s - 8), (x + 6, y + s - 14)], c)
    cv.fill_poly([(x + 12, y + s - 8), (x + 6, y + s - 4),
                  (x + 3, y + s - 12)], c)
    cv.fill_poly([(x + 6, y + s - 14), (x + 2, y + s - 6),
                  (x + 10, y + s - 6)], (230, 120, 20))
    cv.line(x + 16, y + 6, x + 24, y + 14, c, 2)


def icon_chart(cv, x, y, s, c):
    cv.vline(x + 5, y + 4, s - 10, c, 2)
    cv.hline(x + 5, y + s - 6, s - 9, c, 2)
    pts = [(x + 7, y + 10), (x + 15, y + 14), (x + 21, y + 12),
           (x + 28, y + 20), (x + s - 5, y + s - 10)]
    for i in range(len(pts) - 1):
        cv.line(pts[i][0], pts[i][1], pts[i + 1][0], pts[i + 1][1], RED, 3)
    cv.fill_poly([(x + s - 5, y + s - 8), (x + s - 11, y + s - 9),
                  (x + s - 7, y + s - 15)], RED)


def icon_flags(cv, x, y, s, c):
    cols = [RED, GOLD, (40, 90, 200)]
    for i in range(3):
        fx = x + 6 + i * (s // 3)
        cv.vline(fx, y + 6, s - 10, c, 2)
        cv.fill_poly([(fx + 2, y + 6), (fx + 12, y + 10), (fx + 2, y + 14)], cols[i])


def icon_leaf(cv, x, y, s, c):
    cv.fill_ellipse(x + s // 2, y + s // 2, s // 2 - 4, s // 3, (60, 160, 70))
    cv.line(x + 8, y + s - 8, x + s - 8, y + 8, (30, 100, 40), 2)
    cv.line(x + 8, y + s - 8, x + 5, y + s - 3, (30, 100, 40), 2)


def icon_ship(cv, x, y, s, c):
    cv.fill_poly([(x + 4, y + s - 14), (x + s - 4, y + s - 14),
                  (x + s - 10, y + s - 6), (x + 10, y + s - 6)], c)
    cv.fill_rect(x + 12, y + s - 22, s - 26, 8, c)
    cv.vline(x + s // 2, y + 6, s - 28, c, 2)
    cv.fill_poly([(x + s // 2 + 2, y + 6), (x + s // 2 + 10, y + 10),
                  (x + s // 2 + 2, y + 14)], RED)
    for wy in (y + s - 4,):
        for wx in range(x + 4, x + s - 4, 8):
            cv.line(wx, wy, wx + 4, wy - 3, (40, 110, 190), 2)
            cv.line(wx + 4, wy - 3, wx + 8, wy, (40, 110, 190), 2)


def icon_burst(cv, x, y, s, c):
    cx, cy = x + s // 2, y + s // 2
    cv.fill_circle(cx, cy, 5, (230, 120, 20))
    for k in range(8):
        a = 2 * math.pi * k / 8
        cv.line(cx + int(7 * math.cos(a)), cy + int(7 * math.sin(a)),
                cx + int((s // 2 - 2) * math.cos(a)),
                cy + int((s // 2 - 2) * math.sin(a)), (230, 120, 20), 2)
    cv.line(x + 6, y + s - 4, cx - 4, cy + 6, c, 2)


def icon_drone(cv, x, y, s, c):
    cx, cy = x + s // 2, y + s // 2
    cv.fill_rect(cx - 5, cy - 4, 10, 8, c)
    for dx, dy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
        ex = cx + dx * (s // 2 - 5)
        ey = cy + dy * (s // 2 - 5)
        cv.line(cx, cy, ex, ey, c, 2)
        cv.fill_circle(ex, ey, 4, c)
        cv.fill_circle(ex, ey, 2, PAPER)
    cv.fill_circle(cx, cy, 2, RED)


def icon_pill(cv, x, y, s, c):
    # capsule at 45 degrees: two circles + quad
    x0, y0 = x + 10, y + s - 10
    x1, y1 = x + s - 10, y + 10
    cv.fill_circle(x0, y0, 7, RED)
    cv.fill_circle(x1, y1, 7, PAPER)
    cv.circle_outline(x1, y1, 7, c, 2)
    cv.fill_poly([(x0 - 5, y0 - 5), (x0 + 5, y0 + 5), (x1 + 5, y1 + 5),
                  (x1 - 5, y1 - 5)], RED)
    cv.fill_poly([(x0, y0), (x0 + 5, y0 + 5), (x1 + 5, y1 + 5),
                  (x1, y1)], PAPER)
    cv.line(x0, y0, x1, y1, c, 1)


def icon_smoke(cv, x, y, s, c):
    cv.fill_circle(x + 14, y + 16, 8, (150, 150, 150))
    cv.fill_circle(x + 24, y + 12, 9, (170, 170, 170))
    cv.fill_circle(x + 32, y + 18, 7, (150, 150, 150))
    cv.fill_poly([(x + 12, y + s - 4), (x + 20, y + s - 18),
                  (x + 28, y + s - 4)], (230, 110, 20))
    cv.fill_poly([(x + 16, y + s - 4), (x + 21, y + s - 12),
                  (x + 26, y + s - 4)], (245, 180, 40))


def icon_ballot(cv, x, y, s, c):
    cv.fill_rect(x + 6, y + s // 2, s - 12, s // 2 - 6, c)
    cv.fill_rect(x + s // 2 - 7, y + 6, 14, s // 2 - 2, PAPER)
    cv.rect_outline(x + s // 2 - 7, y + 6, 14, s // 2 - 2, c, 2)
    cv.line(x + s // 2 - 4, y + 14, x + s // 2 - 1, y + 18, RED, 2)
    cv.line(x + s // 2 - 1, y + 18, x + s // 2 + 5, y + 9, RED, 2)
    cv.hline(x + s // 2 - 8, y + s // 2 - 2, 16, PAPER, 2)


def icon_cyclone(cv, x, y, s, c):
    cx, cy = x + s // 2, y + s // 2
    pts = []
    for k in range(60):
        t = k / 59.0
        ang = t * 4.5 * math.pi
        r = 3 + t * (s // 2 - 4)
        pts.append((cx + int(r * math.cos(ang)), cy + int(r * math.sin(ang))))
    for i in range(len(pts) - 1):
        cv.line(pts[i][0], pts[i][1], pts[i + 1][0], pts[i + 1][1],
                (40, 110, 190), 2)
    cv.fill_circle(cx, cy, 3, (40, 110, 190))


ICONS = {
    "scales": icon_scales,
    "missile": icon_missile,
    "chart": icon_chart,
    "flags": icon_flags,
    "leaf": icon_leaf,
    "ship": icon_ship,
    "burst": icon_burst,
    "drone": icon_drone,
    "pill": icon_pill,
    "smoke": icon_smoke,
    "ballot": icon_ballot,
    "cyclone": icon_cyclone,
}


# --------------------------------------------------------------------------
# The day's stories: Saturday 21 November 2026 (50 in all)
# --------------------------------------------------------------------------

HERO = {
    "num": 1,
    "headline": "WORLD COURT: MYANMAR COMMITTED GENOCIDE AGAINST THE ROHINGYA",
    "summary": ("The International Court of Justice ruled that Myanmar's security "
                "forces carried out state-sponsored genocide against the Rohingya "
                "- the first time the court has held a state responsible for "
                "genocide. It ordered the government to allow refugees to return. "
                "Rohingya in the camps wept with joy at the verdict; Myanmar and "
                "China rejected it as unfair, and Britain expelled a Myanmar "
                "diplomat. The court cannot enforce its ruling, and rights groups "
                "urged sanctions."),
    "place": "THE HAGUE, NETHERLANDS",
    "region": "EUROPE",
    "lon": 4.3, "lat": 52.1, "hour": 8,
    "icon": "scales",
    "fact": "FIRST-EVER STATE GENOCIDE VERDICT",
    "source": "PBS NEWSHOUR / SKY NEWS",
}

PANELS = [
    {
        "num": 2,
        "headline": "RUSSIAN MISSILES HIT POLAND",
        "summary": ("Two Russian missiles and a drone fell in Poland. NATO "
                    "cried violation."),
        "place": "POLAND", "region": "EUROPE", "lon": 19.0, "lat": 52.0,
        "hour": 2, "icon": "missile", "source": "BBC",
    },
    {
        "num": 3,
        "headline": "WALL STREET INTO CORRECTION",
        "summary": ("Stocks fell 3.7%; Nasdaq down over 5%. Anthropic slid "
                    "11% after debut."),
        "place": "NEW YORK, USA", "region": "NORTH AMERICA", "lon": -74.0,
        "lat": 40.7, "hour": 0, "icon": "chart", "source": "BBC / GUARDIAN",
    },
    {
        "num": 4,
        "headline": "TRUMP-XI-PUTIN SUMMIT ENDS THIN",
        "summary": ("Declaration on nuclear war, oil and UN reform - "
                    "symbolism over substance."),
        "place": "SHENZHEN, CHINA", "region": "ASIA", "lon": 114.0,
        "lat": 22.5, "hour": 3, "icon": "flags", "source": "NPR / CNBC",
    },
    {
        "num": 5,
        "headline": "UK AND EU SIGN LANDMARK DEALS",
        "summary": ("Deals on food exports, carbon markets and youth "
                    "travel, called landmark."),
        "place": "BRUSSELS, BELGIUM", "region": "EUROPE", "lon": 4.35,
        "lat": 50.85, "hour": 7, "icon": "leaf", "source": "BBC",
    },
    {
        "num": 6,
        "headline": "IRAN WAR NEARS NINTH MONTH",
        "summary": ("Trump may not decide until 2027; Tehran wants "
                    "diplomacy. Tanker burned."),
        "place": "STRAIT OF HORMUZ", "region": "ASIA", "lon": 56.5,
        "lat": 26.5, "hour": 5, "icon": "ship", "source": "CBS / STRAITS TIMES",
    },
    {
        "num": 7,
        "headline": "SAUDI DOWNS HOUTHI MISSILES",
        "summary": ("Coalition destroyed 3 Houthi missiles at Riyadh. "
                    "Houthis say 41 killed."),
        "place": "RIYADH, SAUDI ARABIA", "region": "ASIA", "lon": 46.7,
        "lat": 24.6, "hour": 4, "icon": "burst", "source": "MIDDLE EAST EYE",
    },
    {
        "num": 8,
        "headline": "RUSSIA RAINS 100 MISSILES ON KYIV",
        "summary": ("Bridges and stations hit; several killed. Ukraine "
                    "says its strikes killed 12."),
        "place": "KYIV, UKRAINE", "region": "EUROPE", "lon": 30.5,
        "lat": 50.4, "hour": 1, "icon": "drone", "source": "BBC",
    },
    {
        "num": 9,
        "headline": "PILL BREAKTHROUGH FOR CANCER",
        "summary": ("Daraxonrasib halted tumours in nearly all patients; "
                    "1 in 12 clear."),
        "place": "UNITED STATES", "region": "NORTH AMERICA", "lon": -98.0,
        "lat": 39.0, "hour": 6, "icon": "pill", "source": "THE GUARDIAN",
    },
    {
        "num": 10,
        "headline": "PRABOWO SAYS SORRY FOR HAZE",
        "summary": ("Apology at ASEAN summit; 17 concessions revoked, 16 "
                    "firefighters dead."),
        "place": "JAKARTA, INDONESIA", "region": "ASIA", "lon": 106.8,
        "lat": -6.2, "hour": 1, "icon": "smoke", "source": "AL JAZEERA",
    },
    {
        "num": 11,
        "headline": "ANC FIGHTS TO HOLD KEY CITIES",
        "summary": ("ANC resists repeating its national deal with the DA "
                    "at local level."),
        "place": "JOHANNESBURG, S. AFRICA", "region": "AFRICA", "lon": 28.0,
        "lat": -26.2, "hour": 0, "icon": "ballot", "source": "BBC",
    },
    {
        "num": 12,
        "headline": "CYCLONE XAVIER WEAKENS",
        "summary": ("Category 5 peak over the Cook Islands, now "
                    "weakening. Australia alert."),
        "place": "COOK ISLANDS", "region": "OCEANIA", "lon": -159.8,
        "lat": -21.2, "hour": 3, "icon": "cyclone", "source": "BOM / USGS",
    },
]

# 38 one-line briefs (stories 13-50): (headline, place)
BRIEFS = [
    ("LA JURY CONVICTS MAN OF STARTING PALISADES FIRE", "LOS ANGELES"),
    ("MAMDANI TO CLOSE RIKERS ISLAND JAIL BY 2032", "NEW YORK"),
    ("US RELEASES 8TH TRANCHE OF UFO FOOTAGE, CLIPS 2020-26", "PENTAGON"),
    ("TRUMP THREATENS BAN ON US FIRMS' AI DATA CENTRES ABROAD", "WASHINGTON"),
    ("WHITE HOUSE REMOVES CYBER CHIEF AFTER TECH OUTAGES", "WASHINGTON"),
    ("FBI CRIME DATA CENTER BREACHED FOR A SECOND TIME", "USA"),
    ("TEXAS EXECUTION TAKES MORE THAN AN HOUR", "TEXAS"),
    ("TEEN PLEADS NOT GUILTY IN PLOT VS INDIAN SHRINE", "ILLINOIS"),
    ("MAN JAILED 19+ YEARS IN SIKH LEADER KILL PLOT", "USA"),
    ("HIPPO FIONA DIES; SENATE PASSES TRIBUTE", "CINCINNATI"),
    ("$811M FINES DID NOT STOP MIGRANT-DETENTION SCAMS", "USA"),
    ("TRUMP CALLS US FLIGHT SYSTEMS ANCIENT BUT SAFE", "USA"),
    ("BUKELE REGISTERS BID FOR A THIRD TERM", "EL SALVADOR"),
    ("HEGSETH MAKES SURPRISE STOP IN CARACAS", "VENEZUELA"),
    ("TESLA OPENS FIRST ARGENTINA STORE; MODEL 3 FROM $40,990", "BUENOS AIRES"),
    ("COLOMBIA NATIONWIDE STRIKE DRAWS HUNDREDS OF THOUSANDS", "BOGOTA"),
    ("COLOMBIAN TEEN, 17, WINS CHILDREN'S PEACE PRIZE", "COLOMBIA"),
    ("BRAZIL REAL DOWN 18% IN A MONTH; ARGENTINA BONDS DISTRESSED", "SAO PAULO"),
    ("LULA BACKS SANCHEZ IN SPAIN'S NOV 29 SNAP ELECTION", "BRASILIA"),
    ("FRENCH POLICE JAILED FOR BEATING PRODUCER ZECLER", "PARIS"),
    ("DEPARDIEU INVESTIGATED OVER SEX ALLEGATIONS", "PARIS"),
    ("DENMARK ACCUSES RUSSIA OF PLOT TO KILL RAPPER", "COPENHAGEN"),
    ("COST-OF-LIVING PROTESTS SWEEP ACROSS EUROPE", "EUROPE"),
    ("CZECHS CLASH OVER STATE MEDIA", "PRAGUE"),
    ("VATICAN CHRISTMAS TREE RAISED BY ST PETER'S", "VATICAN CITY"),
    ("EU PLEDGES EUR1BN FOR UKRAINE DRONES; UK SENDS 1,000+", "BRUSSELS"),
    ("EU RELEASES EUR1.7BN RECOVERY FUNDS TO BULGARIA", "SOFIA"),
    ("KRYVYI RIH STRIKE KILLS 2, HURTS 42 INCL. 4 CHILDREN", "UKRAINE"),
    ("NEARLY 600 WOMEN KILLED IN SOUTH AFRICA IN 3 MONTHS", "SOUTH AFRICA"),
    ("NIGERIA MOVES TO END JAIL TERMS FOR ATTEMPTED SUICIDE", "ABUJA"),
    ("JAPAN-INDIA DEEPEN MILITARY TIES, PLAN LIVE-FIRE DRILLS", "TOKYO"),
    ("NEPAL PM SHAH FACES HIS INDIA TEST", "KATHMANDU"),
    ("SOUTH KOREA CATCHES 111 STUDENTS CHEATING IN EXAMS", "SEOUL"),
    ("CHINA WARNS ON MOUNT FUJI CLIMB; REWARD FOR MISSING CLIMBER", "JAPAN"),
    ("BESIEGED TAIZ: YEMENIS DESCRIBE LIFE UNDER HOUTHI SIEGE", "YEMEN"),
    ("IRAQ ARRESTS 8 OIL OFFICIALS OVER PROCUREMENT FRAUD", "BAGHDAD"),
    ("GAZA STRIKES KILL 1; CHILDREN BEAR 1/3 OF VIOLATIONS", "GAZA"),
    ("BEPICOLOMBO NEARS MERCURY ORBIT AFTER 8-YEAR VOYAGE", "SPACE"),
]

NUMBERS = [
    ("-3.7%", "S&P 500 ENTERS CORRECTION"),
    ("100+400", "MISSILES + DRONES ON KYIV"),
    ("41", "KILLED IN YEMEN IN 24H, HOUTHIS SAY"),
    ("EUR1BN", "EU PLEDGE FOR UKRAINE DRONES"),
    ("600", "WOMEN KILLED IN S. AFRICA IN 3 MONTHS"),
    ("$48BN", "IRAN WAR COST SO FAR"),
    ("71,000", "FIREFIGHTERS DEPLOYED IN INDONESIA"),
    ("1 IN 12", "CANCER UNDETECTABLE ON NEW PILL"),
]

SOURCES_LINE = ("SOURCES: BBC - AP - REUTERS - AL JAZEERA - THE GUARDIAN - "
                "PBS NEWSHOUR - NPR - CBS NEWS - STRAITS TIMES - "
                "MIDDLE EAST EYE - ANTARA - USGS")


# --------------------------------------------------------------------------
# Layout
# --------------------------------------------------------------------------

def badge(cv, x, y, text, color):
    """Small filled chip with white scale-1 text. Returns chip width."""
    w = text_width(text, 1) + 8
    cv.fill_rect(x, y, w, 11, color)
    draw_text(cv, x + 4, y + 2, text, PAPER, 1)
    return w


def num_chip(cv, x, y, num, color):
    label = str(num)
    w = text_width(label, 2) + 10
    cv.fill_rect(x, y, w, 18, color)
    draw_text(cv, x + 5, y + 2, label, PAPER, 2)
    return w


def render():
    cv = Canvas(W, H, PAPER)
    M = 12  # page margin

    # ---- Masthead -------------------------------------------------------
    cv.fill_rect(M, 4, W - 2 * M, 3, INK)
    draw_text(cv, M, 11, "THE WORLD ON ONE PAGE", INK, 5)
    date_line = "SATURDAY 21 NOVEMBER 2026"
    draw_text(cv, W - M - text_width(date_line, 2), 14, date_line, RED, 2)
    sub_r = "50 STORIES - 6 CONTINENTS"
    draw_text(cv, W - M - text_width(sub_r, 1), 31, sub_r, INK, 1)
    draw_text(cv, M, 49, "ONE PAGE, THE WHOLE DAY", GREY, 2)
    cv.fill_rect(M, 66, W - 2 * M, 2, INK)

    # ---- Date-check strip ----------------------------------------------
    cv.fill_rect(M, 72, W - 2 * M, 22, NAVY)
    dc = "DATE CHECK: SATURDAY 21 NOVEMBER 2026 - ALL 50 STORIES REPORTED THIS DAY"
    draw_text(cv, (W - text_width(dc, 2)) // 2, 76, dc, PAPER, 2)

    # ---- Hero -----------------------------------------------------------
    hero_y = 100
    hx = M
    hw = 640  # hero text column width
    col = REGIONS[HERO["region"]]
    bw = badge(cv, hx, hero_y, REGION_SHORT[HERO["region"]], col)
    draw_text(cv, hx + bw + 6, hero_y + 2,
              "1 - " + HERO["place"], INK, 1)
    fx = hx + bw + 6 + text_width("1 - " + HERO["place"], 1) + 10
    draw_text(cv, fx, hero_y + 2, HERO["fact"], RED, 1)
    # pictogram box on the right of the hero block
    pic_x, pic_y, pic_s = hx + hw - 148, hero_y + 14, 148
    cv.rect_outline(pic_x, pic_y, pic_s, pic_s, GREY, 1)
    ICONS[HERO["icon"]](cv, pic_x + 10, pic_y + 10, pic_s - 20, NAVY)
    cap = "THE HAGUE"
    draw_text(cv, pic_x + (pic_s - text_width(cap, 1)) // 2,
              pic_y + pic_s + 4, cap, GREY, 1)
    # headline + summary
    head_w_chars = (hw - 156) // 18
    y_end = draw_wrapped(cv, hx, hero_y + 16, HERO["headline"], INK, 3,
                         head_w_chars, 25, max_lines=3)
    sum_chars = (hw - 156) // 6
    y_end = draw_wrapped(cv, hx, y_end + 4, HERO["summary"], INK, 1,
                         sum_chars, 9, max_lines=8)
    draw_text(cv, hx, y_end + 3, "SOURCE: " + HERO["source"], GREY, 1)

    # ---- Day in numbers -------------------------------------------------
    nx = hx + hw + 16
    nw = W - M - nx
    cv.vline(nx - 8, hero_y, 180, LIGHT, 1)
    draw_text(cv, nx, hero_y, "DAY IN NUMBERS", NAVY, 2)
    cv.hline(nx, hero_y + 17, nw, NAVY, 2)
    iy = hero_y + 24
    for value, label in NUMBERS:
        draw_text(cv, nx, iy, value, RED, 2)
        lx = nx + 118
        draw_wrapped(cv, lx, iy + 1, label, INK, 1, (nw - 118) // 6, 8,
                     max_lines=2)
        iy += 20

    # ---- Panels ---------------------------------------------------------
    py0 = 286
    cell_w, cell_h = 328, 78
    gap = 8
    cells = PANELS + [None]  # 11 panels + legend cell
    for idx, story in enumerate(cells):
        col_i = idx % 3
        row_i = idx // 3
        cx = M + col_i * (cell_w + gap)
        cy = py0 + row_i * cell_h
        if story is None:
            cv.rect_outline(cx, cy, cell_w, cell_h, GREY, 1)
            draw_text(cv, cx + 8, cy + 6, "HOW TO READ THIS PAGE", NAVY, 2)
            ly = cy + 26
            for rname, rcol in REGIONS.items():
                cv.fill_rect(cx + 8, ly, 10, 8, rcol)
                draw_text(cv, cx + 22, ly + 1, REGION_SHORT[rname], INK, 1)
                ly += 11
            draw_text(cv, cx + 150, cy + 26, "PIN NUMBERS MATCH", INK, 1)
            draw_text(cv, cx + 150, cy + 35, "STORY NUMBERS.", INK, 1)
            draw_text(cv, cx + 150, cy + 47, "EVERY STORY CARRIES", INK, 1)
            draw_text(cv, cx + 150, cy + 56, "ONE HARD NUMBER.", INK, 1)
            continue
        rcol = REGIONS[story["region"]]
        cv.rect_outline(cx, cy, cell_w, cell_h, (200, 200, 200), 1)
        cv.fill_rect(cx, cy, 3, cell_h, rcol)
        chw = num_chip(cv, cx + 6, cy + 5, story["num"], rcol)
        draw_text(cv, cx + 6 + chw + 5, cy + 9,
                  REGION_SHORT[story["region"]] + " - " + story["place"],
                  GREY, 1)
        ICONS[story["icon"]](cv, cx + 8, cy + 28, 44, rcol)
        tx = cx + 58
        tw_chars = (cell_w - 64) // 12
        hy = draw_wrapped(cv, tx, cy + 24, story["headline"], INK, 2,
                          tw_chars, 15, max_lines=2)
        draw_wrapped(cv, tx, hy + 2, story["summary"], INK, 1,
                     (cell_w - 64) // 6, 8, max_lines=2)

    # ---- Map ------------------------------------------------------------
    my0 = 604
    draw_text(cv, M, my0, "WHERE IT HAPPENED - THE 12 MAIN STORIES PINNED",
              NAVY, 2)
    pins = [HERO] + PANELS
    draw_map(cv, M, my0 + 20, W - 2 * M, 146, pins)

    # ---- Timeline -------------------------------------------------------
    ty0 = 778
    draw_text(cv, M, ty0, "THE DAY IN UTC - WHEN THE 12 MAIN STORIES WERE REPORTED",
              NAVY, 1)
    ax0, ax1, ay = 44, W - 30, ty0 + 26
    cv.hline(ax0, ay, ax1 - ax0, INK, 2)
    for h in range(0, 25, 3):
        txp = ax0 + int((ax1 - ax0) * h / 24)
        cv.vline(txp, ay - 3, 7, INK, 1)
        lab = "%02d" % h
        draw_text(cv, txp - text_width(lab, 1) // 2, ay + 7, lab, GREY, 1)
    by_hour = {}
    for story in pins:
        by_hour.setdefault(story["hour"], []).append(story)
    for hour, group in by_hour.items():
        txp = ax0 + int((ax1 - ax0) * hour / 24)
        for k, story in enumerate(group):
            dy = -10 if k % 2 == 0 else 10
            if len(group) == 1:
                dy = -10
            rcol = REGIONS[story["region"]]
            cv.fill_circle(txp, ay + dy, 5, rcol)
            lab = str(story["num"])
            ly = ay + dy - 12 if dy < 0 else ay + dy + 7
            draw_text(cv, txp - text_width(lab, 1) // 2, ly, lab, rcol, 1)

    # ---- Briefs ---------------------------------------------------------
    by0 = 828
    draw_text(cv, M, by0, "AND 38 MORE - THE REST OF THE DAY IN ONE LINE EACH",
              NAVY, 2)
    cv.hline(M, by0 + 17, W - 2 * M, NAVY, 2)
    col_w = (W - 2 * M - 12) // 2
    row_h = 8
    start_y = by0 + 22
    for i, (headline, place) in enumerate(BRIEFS):
        num = 13 + i
        col_i = 0 if i < 19 else 1
        row_i = i if i < 19 else i - 19
        bx = M + col_i * (col_w + 12)
        by = start_y + row_i * row_h
        prefix = "%d. " % num
        draw_text(cv, bx, by, prefix, RED, 1)
        rest = headline + " - " + place
        max_chars = col_w // 6 - len(prefix)
        if len(rest) > max_chars:
            rest = rest[:max_chars - 1].rstrip() + "."
        draw_text(cv, bx + text_width(prefix, 1), by, rest, INK, 1)

    # ---- Footer ---------------------------------------------------------
    fy = 1006
    cv.hline(M, fy - 4, W - 2 * M, INK, 1)
    draw_text(cv, (W - text_width(SOURCES_LINE, 1)) // 2, fy,
              SOURCES_LINE, GREY, 1)
    return cv


def main(argv):
    out = argv[1] if len(argv) > 1 else "/output/entry.png"
    parent = os.path.dirname(out)
    if parent:
        os.makedirs(parent, exist_ok=True)
    data = render().to_png_bytes()
    with open(out, "wb") as fh:
        fh.write(data)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
