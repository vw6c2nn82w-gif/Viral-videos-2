#!/usr/bin/env python3
"""
Renders a 1080x1920 vertical reel explaining how active noise cancellation works.

Everything — visuals and sound — is generated in code:
  * frames are drawn with Pillow (supersampled 2x for anti-aliasing, plus a bloom pass)
  * the soundtrack is synthesised with numpy (engine rumble that actually gets
    "cancelled" when ANC switches on, residual hiss, a soft pad, whooshes)
  * ffmpeg muxes it all into an H.264/AAC MP4

Usage:  python3 make_reel.py [--out reel.mp4] [--preview SECONDS]
"""
import argparse
import math
import os
import subprocess
import sys
import wave
from multiprocessing import Pool

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

# ---------------------------------------------------------------- config
W, H = 1080, 1920
SS = 2                      # supersampling factor
FPS = 30
SR = 48000                  # audio sample rate

BG_TOP = (9, 12, 24)
BG_BOT = (18, 14, 34)
WHITE = (245, 247, 255)
GREY = (140, 150, 175)
DIM = (70, 80, 105)
RED = (255, 77, 90)         # noise
CYAN = (40, 230, 215)       # anti-noise
YELLOW = (255, 220, 80)     # result
GREEN = (90, 235, 130)
ORANGE = (255, 150, 60)
PANEL = (22, 28, 46)

FONT_DIR = "/usr/share/fonts/opentype/inter"
FONT_BOLD = os.path.join(FONT_DIR, "Inter-ExtraBold.otf")
FONT_SEMI = os.path.join(FONT_DIR, "Inter-SemiBold.otf")
if not os.path.exists(FONT_BOLD):  # fallback
    FONT_BOLD = FONT_SEMI = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

# Scene timeline (seconds)
SCENES = [
    ("hook",     0.0,  4.0),
    ("wave",     4.0,  9.0),
    ("mic",      9.0, 15.0),
    ("flip",    15.0, 21.0),
    ("cancel",  21.0, 28.0),
    ("limits",  28.0, 34.0),
    ("outro",   34.0, 38.0),
]
DURATION = SCENES[-1][2]
ANC_ON_T = 21.0 + 2.4       # moment the ANC badge flips ON (audio follows)

# ---------------------------------------------------------------- helpers
_font_cache = {}


def font(size, bold=True):
    key = (int(size), bold)
    if key not in _font_cache:
        _font_cache[key] = ImageFont.truetype(FONT_BOLD if bold else FONT_SEMI, max(1, int(size)))
    return _font_cache[key]


def clamp(x, a=0.0, b=1.0):
    return max(a, min(b, x))


def prog(t, start, dur):
    """0..1 progress of t through [start, start+dur]."""
    return clamp((t - start) / dur) if dur > 0 else float(t >= start)


def ease_out(x):
    return 1 - (1 - x) ** 3


def ease_in_out(x):
    return 3 * x * x - 2 * x * x * x


def ease_back(x):
    c1, c3 = 1.70158, 2.70158
    return 1 + c3 * (x - 1) ** 3 + c1 * (x - 1) ** 2


def lerp(a, b, x):
    return a + (b - a) * x


def rgba(c, a):
    return (c[0], c[1], c[2], int(255 * clamp(a)))


class Canvas:
    """Thin wrapper so scene code works in 1080x1920 coordinates."""

    def __init__(self, img):
        self.img = img
        self.d = ImageDraw.Draw(img, "RGBA")

    def line(self, pts, color, width, alpha=1.0):
        if len(pts) < 2:
            return
        self.d.line([(x * SS, y * SS) for x, y in pts], fill=rgba(color, alpha),
                    width=int(width * SS), joint="curve")
        # round caps
        r = width * SS / 2
        for x, y in (pts[0], pts[-1]):
            self.d.ellipse([x * SS - r, y * SS - r, x * SS + r, y * SS + r], fill=rgba(color, alpha))

    def dashed(self, p0, p1, color, width, alpha=1.0, dash=18, gap=14, offset=0.0):
        (x0, y0), (x1, y1) = p0, p1
        L = math.hypot(x1 - x0, y1 - y0)
        if L == 0:
            return
        ux, uy = (x1 - x0) / L, (y1 - y0) / L
        s = -(offset % (dash + gap))
        while s < L:
            a, b = max(0, s), min(L, s + dash)
            if b > a:
                self.line([(x0 + ux * a, y0 + uy * a), (x0 + ux * b, y0 + uy * b)], color, width, alpha)
            s += dash + gap

    def circle(self, cx, cy, r, fill=None, outline=None, width=0, alpha=1.0):
        box = [(cx - r) * SS, (cy - r) * SS, (cx + r) * SS, (cy + r) * SS]
        self.d.ellipse(box, fill=rgba(fill, alpha) if fill else None,
                       outline=rgba(outline, alpha) if outline else None, width=int(width * SS))

    def rrect(self, x0, y0, x1, y1, r, fill=None, outline=None, width=0, alpha=1.0):
        self.d.rounded_rectangle([x0 * SS, y0 * SS, x1 * SS, y1 * SS], radius=r * SS,
                                 fill=rgba(fill, alpha) if fill else None,
                                 outline=rgba(outline, alpha) if outline else None,
                                 width=int(width * SS))

    def poly(self, pts, fill, alpha=1.0):
        self.d.polygon([(x * SS, y * SS) for x, y in pts], fill=rgba(fill, alpha))

    def text(self, s, x, y, size, color, alpha=1.0, anchor="mm", bold=True):
        self.d.text((x * SS, y * SS), s, font=font(size * SS, bold), fill=rgba(color, alpha), anchor=anchor)

    def text_w(self, s, size, bold=True):
        return self.d.textlength(s, font=font(size * SS, bold)) / SS

    # --- word-by-word "pop" caption, the reel staple ---------------------
    def caption(self, tokens, t, start, cy, size=78, per_word=0.09, max_w=940,
                line_gap=1.18, alpha=1.0):
        """tokens: list of (word, color). Words pop in one at a time from `start`."""
        space = self.text_w(" ", size)
        lines, cur, cur_w = [], [], 0.0
        for word, col in tokens:
            if word == "\n":
                lines.append(cur); cur, cur_w = [], 0.0
                continue
            w = self.text_w(word, size)
            if cur and cur_w + space + w > max_w:
                lines.append(cur); cur, cur_w = [], 0.0
            cur.append((word, col, w))
            cur_w += (space if len(cur) > 1 else 0) + w
        if cur:
            lines.append(cur)
        lh = size * line_gap
        y = cy - lh * (len(lines) - 1) / 2
        i = 0
        for ln in lines:
            total = sum(w for _, _, w in ln) + space * (len(ln) - 1)
            x = W / 2 - total / 2
            for word, col, w in ln:
                p = prog(t, start + i * per_word, 0.22)
                if p > 0:
                    sc = lerp(0.6, 1.0, ease_back(p))
                    self.text(word, x + w / 2, y + (1 - ease_out(p)) * 18, size * sc, col, alpha * min(1, p * 2.5))
                x += w + space
                i += 1
            y += lh


def wave_pts(x0, x1, cy, amp, fn, step=4):
    pts = []
    x = x0
    while x <= x1:
        pts.append((x, cy - amp * fn(x)))
        x += step
    return pts


def label_pill(c, s, x, y, color, alpha=1.0, size=34):
    w = c.text_w(s, size) + 44
    c.rrect(x - w / 2, y - size * 0.95, x + w / 2, y + size * 0.95, size * 0.95,
            fill=color, alpha=0.16 * alpha, outline=color, width=3)
    c.text(s, x, y + 1, size, color, alpha)


# ---------------------------------------------------------------- scenes
def noise_fn(t, k=1.0):
    """A rumbly, irregular 'engine noise' waveform (deterministic in x & t)."""
    def f(x):
        u = x / 1080.0
        return k * (0.55 * math.sin(2 * math.pi * (2.0 * u - 0.9 * t)) +
                    0.30 * math.sin(2 * math.pi * (5.3 * u - 2.1 * t) + 1.3) +
                    0.18 * math.sin(2 * math.pi * (11.7 * u - 3.7 * t) + 0.4) +
                    0.10 * math.sin(2 * math.pi * (23.1 * u + 5.0 * t) + 2.0))
    return f


def scene_hook(c, t, dur):
    # chaotic noise waves filling the screen
    for i, (yy, k, a) in enumerate([(760, 1.0, 1.0), (980, 0.7, 0.55), (1200, 0.5, 0.35)]):
        shake = 1 + 0.25 * math.sin(t * 9 + i)
        c.line(wave_pts(-20, W + 20, yy, 130 * k * shake, noise_fn(t * 1.6 + i)), RED, 8 - i * 2, a * prog(t, 0, 0.4))
    c.caption([("Noise-cancelling", WHITE), ("headphones", WHITE)], t, 0.15, 380, size=84)
    c.caption([("fight", WHITE), ("sound", WHITE), ("with", WHITE), ("MORE", RED), ("sound.", RED)],
              t, 1.0, 1480, size=92, per_word=0.12)
    c.caption([("Here's how", GREY), ("↓", GREY)], t, 2.6, 1640, size=48)


def scene_wave(c, t, dur):
    c.caption([("Sound", CYAN), ("is", WHITE), ("just", WHITE), ("air", WHITE), ("pressure", WHITE),
               ("going", WHITE), ("up", WHITE), ("&", WHITE), ("down", WHITE)], t, 0.1, 400, size=72, max_w=980)
    cy, amp = 1000, 220
    reveal = ease_out(prog(t, 0.3, 1.2))
    xr = lerp(80, 1000, reveal)
    ph = t * 0.8
    f = lambda x: math.sin(2 * math.pi * ((x - 80) / 460.0 - ph))
    # pressure particles (density follows the wave)
    for col in range(30):
        bx = 80 + col * 31.5
        if bx > xr:
            break
        dens = f(bx)
        for row in range(7):
            yy = cy - 330 + row * 15 * (1.4 - 0.5 * dens)
            jitter = 6 * dens * math.sin(row * 3.1 + col)
            c.circle(bx + jitter, cy + 330 + row * 14 * (1.2 - 0.5 * dens) - 40, 3.5, fill=GREY, alpha=0.18 + 0.25 * (dens + 1) / 2)
            _ = yy
    c.dashed((80, cy), (1000, cy), DIM, 3, alpha=0.8)
    c.line(wave_pts(80, xr, cy, amp, f), CYAN, 9)
    # labels at the current peak & trough
    if t > 1.6:
        a = prog(t, 1.6, 0.4)
        peak_x = 80 + 460 * ((0.25 + ph) % 1.0)
        trough_x = 80 + 460 * ((0.75 + ph) % 1.0)
        c.circle(peak_x, cy - amp, 14, fill=WHITE, alpha=a)
        c.circle(trough_x, cy + amp, 14, fill=WHITE, alpha=a)
        c.text("PEAK", peak_x, cy - amp - 55, 38, WHITE, a)
        c.text("TROUGH", trough_x, cy + amp + 55, 38, WHITE, a)
    c.caption([("Peaks", WHITE), ("=", GREY), ("squeezed", CYAN), ("air.", WHITE), ("\n", None),
               ("Troughs", WHITE), ("=", GREY), ("stretched", CYAN), ("air.", WHITE)],
              t, 2.2, 1520, size=62, per_word=0.08)


def draw_earcup(c, cx, cy, a=1.0, mic_glow=0.0, chip_glow=0.0, spk_glow=0.0):
    # head / ear on the right
    c.rrect(cx + 210, cy - 300, cx + 600, cy + 300, 140, fill=(40, 34, 52), alpha=a)
    c.d.ellipse([(cx + 175) * SS, (cy - 95) * SS, (cx + 255) * SS, (cy + 95) * SS],
                fill=rgba((70, 58, 84), a), outline=rgba((110, 95, 130), a), width=4 * SS)
    # cushion
    c.rrect(cx + 120, cy - 230, cx + 175, cy + 230, 26, fill=(58, 64, 88), alpha=a)
    # cup shell
    c.rrect(cx - 170, cy - 260, cx + 130, cy + 260, 70, fill=PANEL, outline=(80, 92, 125), width=5, alpha=a)
    # mic (outside, left)
    mx, my = cx - 170, cy - 140
    if mic_glow > 0:
        c.circle(mx, my, 34 + 10 * mic_glow, fill=RED, alpha=0.25 * mic_glow * a)
    c.circle(mx, my, 22, fill=(30, 34, 50), outline=RED if mic_glow > 0 else GREY, width=5, alpha=a)
    c.circle(mx, my, 8, fill=RED if mic_glow > 0 else GREY, alpha=a)
    # chip
    kx, ky = cx - 40, cy + 30
    if chip_glow > 0:
        c.rrect(kx - 70, ky - 70, kx + 70, ky + 70, 22, fill=YELLOW, alpha=0.22 * chip_glow * a)
    c.rrect(kx - 50, ky - 50, kx + 50, ky + 50, 12, fill=(36, 40, 60), outline=YELLOW if chip_glow > 0 else GREY, width=5, alpha=a)
    for i in range(4):
        o = -30 + i * 20
        for (x0, y0, x1, y1) in [(kx + o, ky - 62, kx + o, ky - 50), (kx + o, ky + 50, kx + o, ky + 62),
                                 (kx - 62, ky + o, kx - 50, ky + o), (kx + 50, ky + o, kx + 62, ky + o)]:
            c.line([(x0, y0), (x1, y1)], GREY, 4, a)
    # speaker cone facing the ear
    sx, sy = cx + 85, cy + 30
    col = CYAN if spk_glow > 0 else GREY
    c.poly([(sx - 30, sy - 35), (sx + 20, sy - 90), (sx + 20, sy + 90), (sx - 30, sy + 35)], (36, 40, 60), a)
    c.line([(sx - 30, sy - 35), (sx + 20, sy - 90), (sx + 20, sy + 90), (sx - 30, sy + 35), (sx - 30, sy - 35)], col, 5, a)
    return (mx, my), (kx, ky), (sx, sy)


def scene_mic(c, t, dur):
    c.caption([("STEP 1", RED)], t, 0.05, 300, size=46)
    c.caption([("A", WHITE), ("tiny", WHITE), ("mic", RED), ("on", WHITE), ("the", WHITE), ("outside", WHITE),
               ("listens", WHITE), ("to", WHITE), ("the", WHITE), ("world", WHITE)], t, 0.15, 420, size=74)
    cx, cy = 470, 1020
    mic_g = prog(t, 1.4, 0.3)
    chip_g = prog(t, 2.6, 0.3)
    spk_g = prog(t, 3.6, 0.3)
    (mx, my), (kx, ky), (sx, sy) = draw_earcup(c, cx, cy, a=ease_out(prog(t, 0, 0.5)),
                                              mic_glow=mic_g * (0.8 + 0.2 * math.sin(t * 8)),
                                              chip_glow=chip_g, spk_glow=spk_g)
    # incoming noise from the left
    head = lerp(-40, mx - 26, ease_out(prog(t, 0.4, 1.0)))
    c.line(wave_pts(-40, head, my, 60, noise_fn(t * 1.5)), RED, 7)
    c.text("NOISE", 150, my - 110, 36, RED, prog(t, 0.6, 0.4))
    # signal path mic -> chip -> speaker
    if t > 2.0:
        p1 = ease_out(prog(t, 2.0, 0.6))
        end1 = (lerp(mx, kx - 55, p1), lerp(my, ky, p1))
        c.dashed((mx, my), end1, RED, 5, offset=-t * 120)
    if t > 3.0:
        p2 = ease_out(prog(t, 3.0, 0.6))
        end2 = (lerp(kx + 55, sx - 30, p2), ky)
        c.dashed((kx + 55, ky), end2, CYAN, 5, offset=-t * 120)
    c.text("MIC", mx + 70, my, 34, RED, mic_g)
    c.text("CHIP", kx, ky + 100, 34, YELLOW, chip_g)
    c.text("SPEAKER", sx + 10, sy + 135, 34, CYAN, spk_g)
    c.caption([("It", WHITE), ("measures", WHITE), ("the", WHITE), ("noise", RED), ("thousands", WHITE),
               ("of", WHITE), ("times", WHITE), ("a", WHITE), ("second.", WHITE)], t, 4.0, 1560, size=58, per_word=0.07)


def scene_flip(c, t, dur):
    c.caption([("STEP 2", YELLOW)], t, 0.05, 300, size=46)
    c.caption([("A", WHITE), ("chip", YELLOW), ("builds", WHITE), ("the", WHITE), ("exact", WHITE),
               ("OPPOSITE", CYAN), ("wave", WHITE)], t, 0.15, 420, size=78)
    ya, yb, amp = 800, 1170, 130
    f = noise_fn(t * 0.6)
    a_in = ease_out(prog(t, 0.2, 0.6))
    c.dashed((60, ya), (1020, ya), DIM, 3, alpha=a_in)
    c.line(wave_pts(60, 1020, ya, amp, f), RED, 8, a_in)
    label_pill(c, "NOISE", 190, ya - 200, RED, a_in)
    # copy appears, then flips through zero to inverted
    a_b = ease_out(prog(t, 1.2, 0.5))
    flip = ease_in_out(prog(t, 2.0, 1.4))
    k = math.cos(math.pi * flip)        # 1 -> -1
    col = tuple(int(lerp(RED[i], CYAN[i], flip)) for i in range(3))
    c.dashed((60, yb), (1020, yb), DIM, 3, alpha=a_b)
    c.line(wave_pts(60, 1020, yb, amp * k, f), col, 8, a_b)
    label_pill(c, "ANTI-NOISE", 250, yb + 200, CYAN, prog(t, 3.2, 0.4))
    if flip > 0.02 and flip < 0.98:
        c.text("FLIPPING…", 820, yb + 200, 36, YELLOW, a_b)
    # alignment guides: red peak sits over cyan trough
    if t > 3.6:
        ga = prog(t, 3.6, 0.4)
        best_x, best_v = 540, -9
        for x in range(300, 800, 4):
            v = f(x)
            if v > best_v:
                best_x, best_v = x, v
        c.dashed((best_x, ya - amp * best_v - 20), (best_x, yb + amp * best_v + 20), WHITE, 3, alpha=ga, offset=t * 40)
        c.circle(best_x, ya - amp * best_v, 12, fill=WHITE, alpha=ga)
        c.circle(best_x, yb + amp * best_v, 12, fill=WHITE, alpha=ga)
        c.text("peak ↔ trough", best_x, (ya + yb) / 2, 34, WHITE, ga)
    c.caption([("Every", WHITE), ("peak", RED), ("becomes", WHITE), ("a", WHITE), ("trough.", CYAN),
               ("(180°", GREY), ("flipped)", GREY)], t, 3.9, 1600, size=58, per_word=0.07)


def scene_cancel(c, t, dur):
    c.caption([("STEP 3", CYAN)], t, 0.05, 300, size=46)
    c.caption([("The", WHITE), ("speaker", CYAN), ("plays", WHITE), ("it", WHITE), ("into", WHITE),
               ("your", WHITE), ("ear…", WHITE)], t, 0.15, 420, size=78)
    cy, amp = 1000, 130
    f = noise_fn(t * 0.6)
    conv = ease_in_out(prog(t, 0.6, 1.6))
    ya, yb = lerp(cy - 230, cy, conv), lerp(cy + 230, cy, conv)
    merged = prog(t, ANC_ON_T - 21.0, 0.5)
    wa = 1 - merged
    c.line(wave_pts(60, 1020, ya, amp, f), RED, 8, wa)
    c.line(wave_pts(60, 1020, yb, -amp, f), CYAN, 8, wa)
    if merged > 0:
        # sum of the two = (almost) nothing. Tiny residual for honesty.
        resid = lambda x: 0.06 * math.sin(x / 9.0 + t * 20) * math.sin(x / 80.0)
        flash = 1 - prog(t, ANC_ON_T - 21.0, 0.8)
        if flash > 0:
            c.rrect(40, cy - 120, 1040, cy + 120, 60, fill=YELLOW, alpha=0.18 * flash)
        c.line(wave_pts(60, 1020, cy, amp, resid), YELLOW, 9, merged)
        c.text("= SILENCE", 540, cy + 110, 52, YELLOW, prog(t, ANC_ON_T - 21.0 + 0.3, 0.4))
    # ANC badge
    on = t >= ANC_ON_T - 21.0
    bx, by = 540, 640
    col = GREEN if on else GREY
    c.rrect(bx - 150, by - 46, bx + 150, by + 46, 46, fill=col, alpha=0.18, outline=col, width=4)
    c.text("ANC", bx - 52, by, 40, WHITE)
    kx = bx + 70
    c.rrect(kx - 50, by - 26, kx + 50, by + 26, 26, fill=col, alpha=0.9)
    knob = lerp(kx - 24, kx + 24, ease_out(prog(t, ANC_ON_T - 21.0, 0.2)))
    c.circle(knob, by, 20, fill=WHITE)
    c.caption([("Peak", RED), ("+", WHITE), ("trough", CYAN), ("=", WHITE), ("zero.", YELLOW), ("\n", None),
               ("It's", WHITE), ("called", WHITE), ("destructive", YELLOW), ("interference.", YELLOW)],
              t, 3.4, 1520, size=62, per_word=0.09)


def scene_limits(c, t, dur):
    c.caption([("So", WHITE), ("why", WHITE), ("can", WHITE), ("you", WHITE), ("still", WHITE),
               ("hear", WHITE), ("voices?", ORANGE)], t, 0.1, 380, size=80)
    a1 = ease_out(prog(t, 0.6, 0.5))
    a2 = ease_out(prog(t, 1.8, 0.5))
    # panel 1: low hum
    c.rrect(60, 560, 1020, 960, 40, fill=PANEL, alpha=a1)
    slow = lambda x: math.sin(2 * math.pi * (x / 420.0 - t * 0.5))
    c.line(wave_pts(110, 970, 760, 90, slow), CYAN, 8, a1)
    c.text("Engine hum, AC, plane drone", 540, 620, 38, WHITE, a1, bold=False)
    label_pill(c, "EASY ✓", 540, 895, GREEN, a1, size=36)
    # panel 2: fast random
    c.rrect(60, 1010, 1020, 1410, 40, fill=PANEL, alpha=a2)

    def fast(x):
        return (0.5 * math.sin(x / 7.0 + t * 30) + 0.4 * math.sin(x / 3.3 - t * 47) *
                math.sin(x / 60.0 + t * 3) + 0.3 * math.sin(x / 1.9 + t * 13))
    c.line(wave_pts(110, 970, 1210, 80, fast, step=3), ORANGE, 5, a2)
    c.text("Voices, clicks, barking dogs", 540, 1070, 38, WHITE, a2, bold=False)
    label_pill(c, "HARD ✗", 540, 1345, ORANGE, a2, size=36)
    c.caption([("Slow,", CYAN), ("steady", CYAN), ("sounds", WHITE), ("are", WHITE), ("easy", WHITE),
               ("to", WHITE), ("predict.", WHITE), ("Fast", ORANGE), ("ones", ORANGE), ("get", WHITE),
               ("blocked", WHITE), ("by", WHITE), ("the", WHITE), ("ear", WHITE), ("cushions", WHITE),
               ("instead.", WHITE)], t, 2.6, 1580, size=52, per_word=0.07)


def scene_outro(c, t, dur):
    # calm flat line breathing
    c.line(wave_pts(60, 1020, 960, 10, lambda x: math.sin(x / 50 + t * 2)), YELLOW, 6, 0.8)
    c.caption([("Your", WHITE), ("headphones", WHITE), ("aren't", WHITE), ("blocking", WHITE), ("noise.", WHITE)],
              t, 0.1, 640, size=74)
    c.caption([("They're", WHITE), ("ERASING", YELLOW), ("it.", YELLOW)], t, 1.0, 1240, size=96, per_word=0.15, max_w=1020)
    c.caption([("Follow", GREY), ("for", GREY), ("more", GREY), ("“how", GREY), ("it", GREY),
               ("works”", GREY)], t, 2.2, 1560, size=46, per_word=0.05)


SCENE_FNS = {
    "hook": scene_hook, "wave": scene_wave, "mic": scene_mic, "flip": scene_flip,
    "cancel": scene_cancel, "limits": scene_limits, "outro": scene_outro,
}

# ---------------------------------------------------------------- rendering
_bg = None


def background():
    global _bg
    if _bg is None:
        y = np.linspace(0, 1, H)[:, None, None]
        top, bot = np.array(BG_TOP, float), np.array(BG_BOT, float)
        grad = np.broadcast_to(top + (bot - top) * y, (H, W, 3)).copy()
        # soft vignette
        yy, xx = np.mgrid[0:H, 0:W]
        r = np.sqrt(((xx - W / 2) / W) ** 2 + ((yy - H / 2) / H) ** 2)
        grad *= (1.05 - 0.6 * r ** 2)[..., None]
        # subtle dot grid
        grid = ((xx % 60 == 30) & (yy % 60 == 30))
        grad[grid] += 14
        _bg = Image.fromarray(np.clip(grad, 0, 255).astype(np.uint8)).resize((W * SS, H * SS), Image.BILINEAR)
    return _bg


def render_frame(i):
    t = i / FPS
    for name, s, e in SCENES:
        if s <= t < e or (name == SCENES[-1][0] and t >= s):
            break
    lt, dur = t - s, e - s
    bg = background()
    img = bg.copy()
    SCENE_FNS[name](Canvas(img), lt, dur)
    # scene fade in/out
    fade = min(prog(lt, 0, 0.25), 1 - prog(lt, dur - 0.25, 0.25))
    if name == "hook":
        fade = min(1.0, 1 - prog(lt, dur - 0.25, 0.25))
    if fade < 1:
        img = Image.blend(bg, img, fade)
    small = img.resize((W, H), Image.LANCZOS)
    # bloom
    arr = np.asarray(small).astype(np.float32)
    glow = np.asarray(small.resize((W // 4, H // 4), Image.BILINEAR)
                      .filter(ImageFilter.GaussianBlur(6)).resize((W, H), Image.BILINEAR)).astype(np.float32)
    out = np.clip(arr + 0.55 * np.maximum(glow - 25, 0), 0, 255).astype(np.uint8)
    return out.tobytes()


# ---------------------------------------------------------------- audio
def fft_filter(x, lo=None, hi=None):
    X = np.fft.rfft(x)
    f = np.fft.rfftfreq(len(x), 1 / SR)
    m = np.ones_like(f)
    if lo:
        m *= 1 / (1 + (lo / np.maximum(f, 1e-3)) ** 4)
    if hi:
        m *= 1 / (1 + (f / hi) ** 4)
    return np.fft.irfft(X * m, len(x))


def env(tt, pts):
    """piecewise-linear envelope from [(time, value), ...]"""
    ts, vs = zip(*pts)
    return np.interp(tt, ts, vs)


def make_audio(path):
    rng = np.random.default_rng(7)
    n = int(DURATION * SR)
    tt = np.arange(n) / SR

    # engine / plane rumble: low harmonics + brown-ish noise
    wob = 1 + 0.004 * np.sin(2 * np.pi * 0.3 * tt)
    rumble = sum(a * np.sin(2 * np.pi * f * tt * wob + p)
                 for f, a, p in [(55, 0.5, 0), (110, 0.35, 1), (165, 0.22, 2), (220, 0.12, 0.5)])
    rumble = rumble + 2.5 * fft_filter(rng.standard_normal(n), lo=30, hi=350)
    rumble /= np.max(np.abs(rumble))
    # how much rumble gets through: loud hook, ducked under explanation,
    # swells before ANC, then cancelled (~ -30 dB) when the badge flips.
    r_env = env(tt, [(0, 0), (0.15, 1.0), (3.7, 1.0), (4.2, 0.32), (21.0, 0.32), (22.5, 0.65),
                     (ANC_ON_T, 0.65), (ANC_ON_T + 0.12, 0.03), (DURATION, 0.03)])

    # high-frequency hiss/chatter that ANC does NOT remove (that's realistic)
    hiss = fft_filter(rng.standard_normal(n), lo=2500, hi=9000)
    hiss /= np.max(np.abs(hiss))
    h_env = env(tt, [(0, 0), (0.15, 0.25), (3.7, 0.25), (4.2, 0.06), (DURATION - 1, 0.06), (DURATION, 0)])

    # soft pad (Am9-ish), enters after the hook, blooms when ANC turns on
    pad = np.zeros(n)
    for f in [110.0, 164.81, 220.0, 261.63, 329.63, 493.88]:
        pad += np.sin(2 * np.pi * f * tt) * (0.6 + 0.4 * np.sin(2 * np.pi * 0.11 * tt + f))
    pad /= np.max(np.abs(pad))
    p_env = env(tt, [(0, 0), (3.8, 0), (5.5, 0.12), (ANC_ON_T, 0.12), (ANC_ON_T + 0.6, 0.26),
                     (DURATION - 1.5, 0.26), (DURATION, 0)])

    # transition whooshes
    fx = np.zeros(n)
    for _, s, _e in SCENES[1:]:
        L = int(0.5 * SR)
        i0 = max(0, int((s - 0.25) * SR))
        burst = fft_filter(rng.standard_normal(L), lo=600, hi=5000)
        shape = np.sin(np.linspace(0, np.pi, L)) ** 2
        seg = burst / np.max(np.abs(burst)) * shape * 0.12
        fx[i0:i0 + L] += seg[: n - i0]
    # ANC switch "click" + soft chime
    i0 = int(ANC_ON_T * SR)
    L = int(1.2 * SR)
    ct = np.arange(L) / SR
    chime = (np.sin(2 * np.pi * 880 * ct) + 0.5 * np.sin(2 * np.pi * 1318.5 * ct)) * np.exp(-ct * 4) * 0.12
    chime[:200] += rng.standard_normal(200) * np.linspace(0.5, 0, 200)
    fx[i0:i0 + L] += chime[: n - i0]

    mix = 0.55 * rumble * r_env + 0.25 * hiss * h_env + pad * p_env + fx
    mix = np.tanh(mix * 1.1) * 0.85
    st = np.stack([mix, mix], axis=1)
    pcm = (st * 32767).astype(np.int16)
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    here = os.path.dirname(os.path.abspath(__file__))
    ap.add_argument("--out", default=os.path.join(here, "noise_cancellation_reel.mp4"))
    ap.add_argument("--preview", type=float, help="render a single still at this time (PNG)")
    ap.add_argument("--workers", type=int, default=os.cpu_count())
    args = ap.parse_args()

    if args.preview is not None:
        i = int(args.preview * FPS)
        img = Image.frombytes("RGB", (W, H), render_frame(i))
        p = os.path.splitext(args.out)[0] + f"_t{args.preview:05.2f}.png"
        img.save(p)
        print(p)
        return

    wav = os.path.splitext(args.out)[0] + ".wav"
    print("synthesising audio…", flush=True)
    make_audio(wav)

    nframes = int(DURATION * FPS)
    cmd = ["ffmpeg", "-y", "-loglevel", "error",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
           "-i", wav,
           "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", args.out]
    ff = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    with Pool(args.workers) as pool:
        for k, buf in enumerate(pool.imap(render_frame, range(nframes), chunksize=4)):
            ff.stdin.write(buf)
            if k % FPS == 0:
                print(f"\rframe {k}/{nframes}", end="", flush=True)
    ff.stdin.close()
    ff.wait()
    os.remove(wav)
    print(f"\ndone -> {args.out}")
    sys.exit(ff.returncode)


if __name__ == "__main__":
    main()
