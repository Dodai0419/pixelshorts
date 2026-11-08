#!/usr/bin/env python3
"""Turn a one-page story into a 60-second vertical animated short: isometric pixel dioramas, score, SFX, captions.

No API key, no GPU, no Node, no Remotion. Python and ffmpeg only. Same story and seed, same frames.
"""
import argparse, array, math, os, pathlib, random, re, shutil, struct, subprocess, sys, tempfile, wave, zlib

W, H, FPS, OUT_W, OUT_H, OUT_FPS = 270, 480, 12, 1080, 1920, 30
PAD = 18
BW, BH = W + 2 * PAD, H + 2 * PAD
CAP_Y, CAP_CHARS, CAP_LINES = 344, 21, 5
WORDS_PER_SEC, MIN_BEAT, SR = 3.7, 1.8, 22050
PUNCT = {"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"', "\u2014": "-", "\u2013": "-", "\u2026": "...", "\u00a0": " "}
PLUM = (58, 30, 70)
BAYER = ((0, 8, 2, 10), (12, 4, 14, 6), (3, 11, 1, 9), (15, 7, 13, 5))
LIGHTS = {
    "day": dict(tint=(1, 1, 1), bg=(255, 255, 255), text=(36, 30, 54)),
    "golden": dict(tint=(1.06, .88, .7), bg=(255, 243, 224), text=(70, 36, 34)),
    "night": dict(tint=(.42, .46, .74), bg=(18, 20, 42), text=(236, 236, 246)),
    "storm": dict(tint=(.62, .66, .76), bg=(92, 100, 116), text=(255, 255, 255)),
}
ACTIONS = ("stand", "walk", "march", "crowd", "talk", "shake", "collapse", "flash", "rain", "boom")
MINOR = {"night", "storm"}
FONT = bytes.fromhex(
    "0000000000 00005F0000 0007000700 147F147F14 242A7F2A12 2313086462 3649562050 0008070300 001C224100 0041221C00 2A1C7F1C2A 08083E0808 0080703000 0808080808 0000606000 2010080402"
    "3E5149453E 00427F4000 7249494946 2141494D33 1814127F10 2745454539 3C4A494931 4121110907 3649494936 464949291E 0000140000 0040340000 0008142241 1414141414 0041221408 0201590906"
    "3E415D594E 7C1211127C 7F49494936 3E41414122 7F4141413E 7F49494941 7F09090901 3E41415173 7F0808087F 00417F4100 2040413F01 7F08142241 7F40404040 7F021C027F 7F0408107F 3E4141413E"
    "7F09090906 3E4151215E 7F09192946 2649494932 03017F0103 3F4040403F 1F2040201F 3F4038403F 6314081463 0304780403 6159494D43 007F414141 0204081020 004141417F 0402010204 4040404040"
    "0003070800 2054547840 7F28444438 3844444428 384444287F 3854545418 00087E0902 18A4A49C78 7F08040478 00447D4000 2040403D00 7F10284400 00417F4000 7C04780478 7C08040478 3844444438"
    "FC18242418 18242418FC 7C08040408 4854545424 04043F4424 3C4040207C 1C2040201C 3C4030403C 4428102844 4C9090907C 4464544C44 0008364100 0000770000 0041360800 0201020402")
FIGURE = {
    "stand": ("..hhh..|.hhhhh.|.hehhe.|.hhhhh.|..hhh..|..ccc..|.ccccc.|c.ccc.c|h.ccc.h|..l.l..|..l.l..|..b.b.."),
    "stepa": ("..hhh..|.hhhhh.|.hehhe.|.hhhhh.|..hhh..|..ccc..|.ccccc.|c.ccc.c|h.ccc.h|..l.l..|.l...l.|.b...b."),
    "stepb": ("..hhh..|.hhhhh.|.hehhe.|.hhhhh.|..hhh..|..ccc..|.ccccc.|c.ccc.c|h.ccc.h|...l...|...l...|..bb..."),
    "wave": ("h.hhh.h|chhhhhc|chehhec|chhhhhc|.chhhc.|..ccc..|..ccc..|..ccc..|..ccc..|..l.l..|..l.l..|..b.b.."),
    "talk": ("..hhh..|.hhhhh.|.hehhe.|.hhmhh.|..hhh..|..ccc..|.ccccc.|c.ccc.c|h.ccc.h|..l.l..|..l.l..|..b.b.."),
}
COATS = ((214, 74, 66), (64, 120, 196), (236, 176, 60), (88, 168, 104), (156, 92, 180), (70, 170, 170))
SKINS = ((250, 214, 178), (226, 174, 132), (170, 118, 84), (248, 224, 196))


class Refused(Exception):
    pass


def mix(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def ramp(c, k):
    """Hue-shifted shade: shadows drift to violet, lights to warm yellow (k from -1 to 1)."""
    return mix(c, (64, 48, 104), -k * .62) if k < 0 else mix(c, (255, 240, 190), k * .5)


class Canvas:
    def __init__(self, w, h, bg):
        self.w, self.h, self.bg = w, h, bytes(bg)
        self.px = bytearray(self.bg * (w * h))
        self.patterns = {}

    def row_pattern(self, a, b, level, y):
        key = (a, b, level, y % 4)
        if key not in self.patterns:
            ba, bb = bytes(a), bytes(b)
            cells = b"".join(bb if BAYER[y % 4][x] < level else ba for x in range(4))
            self.patterns[key] = cells * (self.w // 4 + 2)
        return self.patterns[key]

    def poly(self, pts, a, b=None, level=0, table=None):
        """Scanline fill. Flat, dithered between a and b, or remapped through a translate table."""
        ys = [p[1] for p in pts]
        n, ba = len(pts), bytes(a) if a else b""
        for y in range(max(0, int(min(ys))), min(self.h, int(max(ys)) + 1)):
            xs = []
            for i in range(n):
                (x0, y0), (x1, y1) = pts[i], pts[(i + 1) % n]
                if (y0 <= y + .5) != (y1 <= y + .5):
                    xs.append(x0 + (y + .5 - y0) * (x1 - x0) / (y1 - y0))
            xs.sort()
            for i in range(0, len(xs) - 1, 2):
                x0, x1 = max(0, int(xs[i] + .5)), min(self.w, int(xs[i + 1] + .5))
                if x1 > x0:
                    s, e = (y * self.w + x0) * 3, (y * self.w + x1) * 3
                    if table:
                        self.px[s:e] = self.px[s:e].translate(table)
                    elif b:
                        self.px[s:e] = self.row_pattern(a, b, level, y)[x0 * 3:x1 * 3]
                    else:
                        self.px[s:e] = ba * (x1 - x0)

    def rect(self, x, y, w, h, c):
        x0, x1 = max(0, x), min(self.w, x + w)
        if x1 > x0:
            line, c = bytes(c) * (x1 - x0), None
            for r in range(max(0, y), min(self.h, y + h)):
                self.px[(r * self.w + x0) * 3:(r * self.w + x1) * 3] = line


DARKEN = bytes(int(i * .64) for i in range(256))


class View:
    """Isometric projection plus the light of one shot."""

    def __init__(self, canvas, light, zoom, focus):
        self.c, self.tint, self.tw, self.th = canvas, LIGHTS[light]["tint"], 20 * zoom, 10 * zoom
        fx, fy = focus
        self.cx = BW / 2 - (fx - fy) * self.tw / 2
        self.cy = 210 - (fx + fy) * self.th / 2

    def at(self, x, y, z=0):
        return self.cx + (x - y) * self.tw / 2, self.cy + (x + y) * self.th / 2 - z * self.th * 1.2

    def lit(self, c):
        return tuple(min(255, int(c[i] * self.tint[i])) for i in range(3))

    def tile(self, x, y, c, dx=1, dy=1, z=0, c2=None, level=0):
        pts = [self.at(x, y, z), self.at(x + dx, y, z), self.at(x + dx, y + dy, z), self.at(x, y + dy, z)]
        self.c.poly(pts, self.lit(c), self.lit(c2) if c2 else None, level)

    def shadow(self, x, y, dx, dy, dz):
        k = dz * .9
        pts = [self.at(x, y), self.at(x + dx, y), self.at(x + dx + k, y - k * .25), self.at(x + dx + k, y + dy - k * .25), self.at(x + k, y + dy - k * .25), self.at(x, y + dy)]
        self.c.poly(pts, None, table=DARKEN)

    def box(self, x, y, z, dx, dy, dz, c):
        top = z + dz
        self.c.poly([self.at(x, y + dy, z), self.at(x + dx, y + dy, z), self.at(x + dx, y + dy, top), self.at(x, y + dy, top)], self.lit(ramp(c, -.18)), self.lit(ramp(c, -.34)), 5)
        self.c.poly([self.at(x + dx, y, z), self.at(x + dx, y + dy, z), self.at(x + dx, y + dy, top), self.at(x + dx, y, top)], self.lit(ramp(c, -.62)), self.lit(ramp(c, -.78)), 5)
        self.c.poly([self.at(x, y, top), self.at(x + dx, y, top), self.at(x + dx, y + dy, top), self.at(x, y + dy, top)], self.lit(ramp(c, .28)))

    def roof(self, x, y, z, dx, dy, dz, c):
        mid = (x + dx / 2, y + dy / 2, z + dz)
        peak = self.at(*mid)
        self.c.poly([self.at(x, y + dy, z), self.at(x + dx, y + dy, z), peak], self.lit(ramp(c, -.1)))
        self.c.poly([self.at(x + dx, y, z), self.at(x + dx, y + dy, z), peak], self.lit(ramp(c, -.6)))

    def tree(self, x, y, c=(92, 168, 88)):
        self.box(x + .38, y + .38, 0, .24, .24, .9, (120, 84, 62))
        self.box(x + .1, y + .1, .8, .8, .8, .7, c)
        self.box(x + .24, y + .24, 1.5, .52, .52, .55, ramp(c, .15))

    def house(self, x, y, wall=(232, 214, 186), roof=(198, 84, 66), dx=2, dy=2, dz=1.5):
        self.box(x, y, 0, dx, dy, dz, wall)
        self.roof(x, y, dz, dx, dy, 1.1, roof)
        self.box(x + dx * .3, y + dy, .0, dx * .24, .03, .75, (116, 78, 64))
        self.box(x + dx, y + dy * .3, .7, .03, dy * .3, .42, (120, 170, 210))


GRASS, GRASS2, ROAD, WATER, WATER2, SAND, FLOOR, FLOOR2 = (146, 200, 110), (136, 192, 106), (214, 192, 150), (92, 164, 214), (108, 176, 222), (232, 214, 160), (204, 196, 186), (192, 184, 176)
PLACES = {}


def place(name, n, path, focus, hero_tall):
    def deco(fn):
        PLACES[name] = dict(n=n, path=path, focus=focus, draw=fn, hero_tall=hero_tall)
        return fn
    return deco


def ground(v, n, cell):
    skirt = (150, 112, 86)
    bg = v.c.bg
    bg = (bg[0], bg[1], bg[2])
    for i in range(6):
        z0, z1 = -i * .5, -(i + 1) * .5
        a, b = v.lit(ramp(skirt, -.3 - i * .06)), bg
        v.c.poly([v.at(0, n, z0), v.at(n, n, z0), v.at(n, n, z1), v.at(0, n, z1)], a, b, i * 3)
        v.c.poly([v.at(n, 0, z0), v.at(n, n, z0), v.at(n, n, z1), v.at(n, 0, z1)], v.lit(ramp(skirt, -.6 - i * .05)), b, i * 3)
    for y in range(n):
        for x in range(n):
            c, c2 = cell(x, y)
            v.tile(x, y, c, c2=c2, level=5 if c2 else 0)


@place("bridge", 12, ((2.2, 6, 1.32), (9.8, 6, 1.32)), (6, 6), 2.2)
def _bridge(v, hero, broken):
    def cell(x, y):
        return (GRASS, GRASS2) if x < 2 or x > 9 else (WATER, WATER2)
    if not hero:
        ground(v, 12, cell)
        for x, y in ((.2, 1), (10.2, 9.6), (.3, 9), (10, 1.4)):
            v.shadow(x, y, 1.4, 1.4, 1.6)
        for x, y in ((.2, 1), (.3, 9)):
            v.house(x, y, dx=1.5, dy=1.5, dz=1.2)
        v.tree(10.4, 2.6); v.tree(10.8, 9.4); v.tree(.5, 3.6); v.tree(1, 7.8)
        v.box(0, 5.2, .02, 2, 1.6, .05, ROAD); v.box(10, 5.2, .02, 2, 1.6, .05, ROAD)
        v.box(1.5, 5.1, 0, .6, 1.8, 1.25, (170, 160, 150)); v.box(9.9, 5.1, 0, .6, 1.8, 1.25, (170, 160, 150))
        return
    iron = (92, 88, 110)
    for x in (4, 6, 8):
        v.box(x - .2, 5.3, -.2, .4, 1.4, 1.4, (150, 142, 140))
    v.box(2, 5.2, 1.2, 8, 1.6, .12, (156, 118, 90))
    for x in range(2, 11):
        v.box(x - .05, 5.15, 1.3, .1, .1, .9, iron)
        v.box(x - .05, 6.75, 1.3, .1, .1, .9, iron)
    v.box(2, 5.15, 2.15, 8, .1, .1, iron)
    v.box(2, 6.75, 2.15, 8, .1, .1, iron)


@place("town", 12, ((1, 7.5, 0), (11, 7.5, 0)), (6, 6), 5)
def _town(v, hero, broken):
    def cell(x, y):
        return (ROAD, None) if y in (7, 8) else (GRASS, GRASS2)
    if hero:
        v.shadow(5, 2, 2, 2, 3)
        v.box(5, 2, 0, 2, 2, 3.4, (214, 206, 190))
        v.box(5.6, 4, 2.2, .8, .03, .8, (250, 246, 230))
        v.roof(5, 2, 3.4, 2, 2, 1.6, (92, 104, 150))
        return
    ground(v, 12, cell)
    for spot in ((.6, .8), (8.8, .8), (.8, 3.8), (9, 3.6), (1, 9.4), (8.4, 9.4)):
        v.shadow(*spot, 2, 2, 1.8)
    v.house(.6, .8); v.house(8.8, .8, roof=(84, 128, 170)); v.house(.8, 3.8, wall=(240, 224, 170)); v.house(9, 3.6, roof=(116, 150, 92))
    v.house(1, 9.4, wall=(226, 200, 200)); v.house(8.4, 9.4, roof=(170, 110, 170))
    v.tree(3.8, 5.2); v.tree(4.2, 9.8); v.tree(7.4, 5.6)


@place("harbor", 12, ((.5, 10, .34), (7, 10, .34)), (6, 6), 4)
def _harbor(v, hero, broken):
    def cell(x, y):
        return (SAND, None) if y > 10 or x < 1 else (WATER, WATER2)
    if hero:
        wood = (140, 92, 62)
        v.box(4, 3, 0, 5, 1.8, 1.1, wood)
        v.box(5, 3.2, 1.1, 3, 1.4, .4, ramp(wood, .2))
        for x in (5.2, 6.8, 8.2):
            v.box(x, 3.8, 1.1, .16, .16, 2.9, (98, 70, 58))
            v.box(x - .03, 3.1, 2.8, .2, 1.6, .9, (246, 240, 224))
        return
    ground(v, 12, cell)
    v.box(1, 9.4, 0, 8, 1.3, .3, (176, 130, 90))
    for x in (2, 4, 6, 8):
        v.box(x, 9.3, -.2, .2, .2, .7, (120, 88, 66))
    v.shadow(.1, 3, 1, 3, 1.4)
    v.house(.1, 3, dx=1, dy=3, dz=1.2, roof=(120, 104, 130))
    v.box(10, 10.6, .0, .9, .9, .8, (168, 124, 80)); v.box(.2, 10.8, 0, .8, .8, .7, (168, 124, 80))
    v.tree(2, 10.9); v.tree(5.6, 10.9)


@place("lab", 10, ((1, 7, 0), (9, 7, 0)), (5, 5), 3)
def _lab(v, hero, broken):
    def cell(x, y):
        return (FLOOR, None) if (x + y) % 2 else (FLOOR2, None)
    if hero:
        v.shadow(3.6, 1, 3, 2, 2)
        v.box(3.6, 1, 0, 3, 2, 2.4, (120, 140, 168))
        v.box(4.2, 3, .9, 1.8, .03, 1.1, (40, 60, 80))
        for x in (4.4, 5, 5.6):
            v.box(x, 3.02, 1.4, .2, .02, .2, (120, 230, 150))
        return
    ground(v, 10, cell)
    v.box(0, 0, 0, 10, .2, 3.2, (226, 220, 232)); v.box(0, 0, 0, .2, 10, 3.2, (238, 232, 240))
    v.box(.2, 1, 0, 1.2, 3, 1, (170, 130, 96)); v.box(.4, 1.3, 1, .4, .4, .5, (130, 200, 220)); v.box(.5, 2.6, 1, .3, .3, .7, (230, 150, 120))
    v.box(7.6, .2, 0, 2, 1, 2.4, (150, 170, 150))
    v.box(2.4, 4.4, 0, 3, 1.1, .9, (190, 170, 150)); v.box(3, 4.6, .9, .7, .7, .1, (240, 240, 230)); v.box(4.2, 4.6, .9, .5, .5, .38, (150, 210, 180))


@place("field", 12, ((1, 8.6, 0), (11, 8.6, 0)), (6, 6), 5)
def _field(v, hero, broken):
    def cell(x, y):
        return (ROAD, None) if y == 8 else ((226, 204, 110), (214, 190, 96)) if x > 7 and y < 7 else (GRASS, GRASS2)
    if hero:
        v.shadow(3, 2, 2.4, 2.4, 3)
        v.box(3, 2, 0, 2.4, 2.4, 3.2, (230, 222, 206))
        v.roof(3, 2, 3.2, 2.4, 2.4, 1.2, (180, 90, 70))
        cx, cy = v.at(4.2, 4.42, 3)
        for dx, dy in ((26, 13), (-26, -13), (-13, 22), (13, -22)):
            v.c.poly([(cx, cy - 1), (cx + dx * v.tw / 20, cy + dy * v.tw / 20 - 1), (cx + dx * v.tw / 20, cy + dy * v.tw / 20 + 2), (cx, cy + 2)], v.lit((246, 240, 224)))
        return
    ground(v, 12, cell)
    for i in range(9):
        v.tree(.3 + (i * 37 % 7) * .4, 9.6 + (i % 3) * .8, (84 + i * 3, 160, 92))
    v.tree(10, 10); v.tree(8.6, 9.8); v.tree(.8, .6); v.tree(6.6, .8); v.tree(1, 5)
    for x in (8.3, 9.3, 10.3):
        v.box(x, 3, 0, .5, .5, .5, (212, 170, 80))


def parse(text, captions=True):
    title, beats = "", []
    for no, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        low = line.lower()
        if low.startswith("title:"):
            title = clean(line[6:].strip(), no, captions)
            continue
        if ":" not in line:
            raise Refused(f"line {no}: no colon. Write 'place light action: narration'")
        head, _, said = line.partition(":")
        words = head.lower().split()
        if len(words) != 3:
            raise Refused(f"line {no}: start with place light action, got '{head.strip()}'")
        for word, valid, kind in zip(words, (PLACES, LIGHTS, ACTIONS), ("place", "light", "action")):
            if word not in valid:
                raise Refused(f"line {no}: unknown {kind} '{word}'. Use one of: {', '.join(valid)}")
        said = clean(said.strip(), no, captions)
        if not said:
            raise Refused(f"line {no}: no narration")
        beats.append(dict(place=words[0], light=words[1], action=words[2], text=said))
    if not beats:
        raise Refused("the story has no beats. Write lines like 'bridge day march: Soldiers cross in step.'")
    return dict(title=title, beats=beats)


def clean(text, no, captions):
    for a, b in PUNCT.items():
        text = text.replace(a, b)
    if captions:
        for ch in text:
            if not 32 <= ord(ch) < 127:
                raise Refused(f"line {no}: the pixel font is ASCII only and cannot show '{ch}'. Pass --no-captions with --voice for other languages")
    return text


def timed(story, voice=None):
    beats = [dict(b, dur=max(MIN_BEAT, len(b["text"].split()) / WORDS_PER_SEC)) for b in story["beats"]]
    if voice:
        k = voice / sum(b["dur"] for b in beats)
        beats = [dict(b, dur=b["dur"] * k) for b in beats]
    return beats


def lint(story):
    beats, notes = timed(story), []
    total = sum(b["dur"] for b in beats)
    if not 55 <= total <= 70:
        notes.append(f"length is {total:.0f} s at {WORDS_PER_SEC} words/s. Shorts that hold attention here run 55 to 70 s")
    if len(beats[0]["text"].split()) > 18:
        notes.append("the hook is long. State the absurd fact in one line of 18 words or fewer")
    if not any(b["action"] in ("collapse", "boom", "flash") for b in beats):
        notes.append("no snap: add a collapse, boom or flash beat where the story breaks")
    return notes


def wrap(text):
    lines, cur = [], ""
    for word in text.split():
        while len(word) > CAP_CHARS:
            if cur:
                lines.append(cur); cur = ""
            lines.append(word[:CAP_CHARS]); word = word[CAP_CHARS:]
        if cur and len(cur) + 1 + len(word) > CAP_CHARS:
            lines.append(cur); cur = word
        else:
            cur = f"{cur} {word}".strip()
    return lines + [cur] if cur else lines


def glyph_runs(ch, s):
    i = max(0, ord(ch) - 32) * 5
    runs = []
    for cx in range(5):
        col = FONT[i + cx]
        for row in range(8):
            if col >> row & 1:
                runs.append((cx * s, row * s))
    return runs


def draw_text(c, text, x, y, color, s):
    px, stride, line = c.px, c.w, bytes(color) * s
    for k, ch in enumerate(text):
        for rx, ry in glyph_runs(ch, s):
            ox = x + k * 6 * s + rx
            for r in range(s):
                off = ((y + ry + r) * stride + ox) * 3
                if 0 <= off and off + 3 * s <= len(px):
                    px[off:off + 3 * s] = line


def sprite(shape, coat, skin, soldier, s):
    grid = FIGURE[shape].split("|")
    if soldier:
        grid = ["..ppp..", "..ppp.."] + grid
    colors = {"h": skin, "c": coat, "l": (64, 56, 88), "b": (40, 34, 56), "e": PLUM, "m": (150, 60, 70), "p": (34, 34, 54)}
    h, w = len(grid), 7
    cells = {(x, y): colors[ch] for y, row in enumerate(grid) for x, ch in enumerate(row) if ch != "."}
    edge = {(x + dx, y + dy) for (x, y) in cells for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))} - set(cells)
    runs = []
    for (x, y), col in list(cells.items()) + [(e, PLUM) for e in edge]:
        for r in range(s):
            runs.append((y * s + r - h * s, (x - w // 2) * s, bytes(col) * s))
    return runs


def blit(frame, runs, x, y):
    x, y = int(x), int(y)
    for ry, rx, data in runs:
        px, py, n = x + rx, y + ry, len(data) // 3
        if 0 <= py < BH and 0 <= px and px + n <= BW:
            off = (py * BW + px) * 3
            frame[off:off + n * 3] = data


def bake(name, light, zoom, broken):
    """Background buffer, hero runs split into columns so it can fall apart, and the view."""
    info, bg = PLACES[name], LIGHTS[light]["bg"]
    canvas = Canvas(BW, BH, bg)
    view = View(canvas, light, zoom, info["focus"])
    info["draw"](view, False, broken)
    base = bytes(canvas.px)
    canvas.px[:] = base
    info["draw"](view, True, broken)
    full = bytes(canvas.px)
    columns = {}
    for y in range(BH):
        a, b = base[y * BW * 3:(y + 1) * BW * 3], full[y * BW * 3:(y + 1) * BW * 3]
        if a == b:
            continue
        x = 0
        while x < BW:
            if a[x * 3:x * 3 + 3] != b[x * 3:x * 3 + 3]:
                s = x
                while x < BW and a[x * 3:x * 3 + 3] != b[x * 3:x * 3 + 3] and x // 8 == s // 8:
                    x += 1
                columns.setdefault(s // 8, []).append((y, s, b[s * 3:x * 3]))
            else:
                x += 1
    return dict(base=base, full=base if broken else full, columns=columns, view=view, broken=broken)


def caption_strip(beat, shown, light, captions):
    """The lower third, as bytes. Words appear at narration pace, a page at a time."""
    c = Canvas(W, H - CAP_Y, LIGHTS[light]["bg"])
    if captions:
        pages = [p for p in (wrap(beat["text"])[i:i + CAP_LINES] for i in range(0, 999, CAP_LINES)) if p]
        count = 0
        for page in pages:
            size = sum(len(l.split()) for l in page)
            if shown <= count + size or page is pages[-1]:
                left = max(0, shown - count)
                for row, line in enumerate(page):
                    words = line.split()
                    text = " ".join(words[:left]) if left < len(words) else line
                    left -= len(words)
                    if left >= -len(words) and text:
                        draw_text(c, text, 6, 4 + row * 24, LIGHTS[light]["text"], 2)
                break
            count += size
    return bytes(c.px)


def beat_frames(beat):
    return max(1, round(beat["dur"] * FPS))


def frames(story, seed=1, captions=True):
    rng = random.Random(seed)
    shot_rng = random.Random(seed * 7 + 1)
    bakes, broken, prev_place = {}, set(), None
    skins = [shot_rng.choice(SKINS) for _ in range(40)]
    coats = [shot_rng.choice(COATS) for _ in range(40)]
    sprites = {}
    previous = None

    def fig(shape, i, soldier, s):
        key = (shape, i % 40 if not soldier else -1, soldier, s)
        if key not in sprites:
            sprites[key] = sprite(shape, (196, 54, 54) if soldier else coats[i % 40], skins[i % 40], soldier, s)
        return sprites[key]

    for bi, beat in enumerate(story["beats"]):
        place_name, light, action = beat["place"], beat["light"], beat["action"]
        zoom = 1.45 if bi % 3 == 1 and action not in ("collapse", "crowd", "march") else 1
        key = (place_name, light, zoom, place_name in broken)
        if key not in bakes:
            bakes[key] = bake(*key)
        shot = bakes[key]
        info, view = PLACES[place_name], shot["view"]
        n, s = beat_frames(beat), 2 if zoom > 1 else 1
        words = len(beat["text"].split())
        pan = shot_rng.choice((-1, 1)) * shot_rng.randint(6, 14)
        (ax, ay, az), (bx, by, bz) = info["path"]
        debris = [(rng.uniform(40, BW - 40), rng.uniform(-30, 30), rng.uniform(-30, 30), rng.uniform(-90, -10), rng.choice(((180, 170, 160), (110, 100, 120), (150, 118, 90)))) for _ in range(46)]
        drops = [(rng.randint(0, BW), rng.randint(0, BH), rng.randint(5, 9)) for _ in range(70)]
        fall_speed = {k: rng.uniform(.6, 1.5) for k in shot["columns"]}
        for fi in range(n):
            t = fi / max(1, n - 1)
            sec = fi / FPS
            shake_x = shake_y = 0
            layer = bytearray(shot["full"])
            if action == "shake" and place_name not in broken:
                amp = 1 + int(t * 4)
                shake_y = amp if fi % 2 else -amp
                layer = bytearray(shot["base"])
                for runs in shot["columns"].values():
                    for y, x, data in runs:
                        off = ((y + (amp if fi % 2 else -amp)) * BW + x) * 3
                        if 0 <= off and off + len(data) <= len(layer):
                            layer[off:off + len(data)] = data
            if action == "collapse" and place_name not in broken:
                drop = max(0, t - .22)
                if drop == 0:
                    shake_x = 2 if fi % 2 else -2
                else:
                    layer = bytearray(shot["base"])
                    shake_y = int((3 if fi % 2 else -3) * max(0, 1 - drop * 2))
                    for col, runs in shot["columns"].items():
                        dy = int(fall_speed[col] * (drop * 12) ** 2)
                        for y, x, data in runs:
                            if y + dy < 330:
                                off = ((y + dy) * BW + x) * 3
                                if off + len(data) <= len(layer):
                                    layer[off:off + len(data)] = data
                    for dx, sx, _sy, vy, col in debris:
                        g = drop * 12
                        yy = 190 + vy * g / 6 + 14 * g * g
                        xx = dx + sx * g / 4
                        if 0 < yy < 334:
                            blit(layer, [(r, 0, bytes(col) * 3) for r in range(3)], xx, yy)
            count = {"march": 8, "crowd": 14, "stand": 3, "talk": 2, "walk": 1, "shake": 3, "boom": 4, "flash": 3, "rain": 2, "collapse": 3}[action]
            figures = []
            for i in range(count):
                soldier = action == "march"
                if action == "crowd":
                    u, off = (i % 7) / 6, (i // 7 - .5) * 1.2
                    wx, wy, wz = ax + (bx - ax) * (u * .8 + .1), ay + off, az
                    shape = "wave" if (fi // 3 + i) % 2 else "stand"
                elif action == "march":
                    u = (min(1, sec / max(.1, beat["dur"]) * .9) + i * .1 - .35)
                    if u < 0:
                        continue
                    wx, wy, wz = ax + (bx - ax) * min(u, 1), ay + (i % 2 - .5) * .7, az
                    shape = "stepa" if (fi // 2 + i // 2) % 2 else "stepb"
                elif action == "walk":
                    wx, wy, wz = ax + (bx - ax) * t, ay, az
                    shape = "stepa" if fi // 2 % 2 else "stepb"
                elif action == "talk":
                    wx, wy, wz = (ax + bx) / 2 + (i - .5) * 1.5, ay, az
                    shape = "talk" if (fi // 3) % 2 == i else "stand"
                else:
                    wx, wy, wz = ax + (bx - ax) * (.2 + i * .28), ay, az
                    shape = "wave" if action in ("boom", "flash", "collapse") and fi // 2 % 2 else "stand"
                if place_name in broken and place_name == "bridge" and 2.3 < wx < 9.7:
                    wx = 1.2 if wx < 6 else 10.8
                    wz = 0
                    shape = "stand"
                sx, sy = view.at(wx, wy, wz)
                figures.append((sy, sx, fig(shape, i + bi * 3, soldier, s), action == "march" and fi // 2 % 2))
            for sy, sx, runs, bob in sorted(figures, key=lambda f: f[0]):
                blit(layer, runs, sx, sy - (1 if bob else 0))
            if action == "talk":
                sx, sy = view.at((ax + bx) / 2 + ((fi // 12) % 2 - .5) * 1.5, ay, az)
                for k in range(3):
                    if k <= (fi // 2) % 3:
                        blit(layer, [(r, 0, bytes(PLUM) * 2) for r in range(2)], sx - 5 + k * 5, sy - 22 * s)
            if action == "rain" or light == "storm":
                for dx, dy, length in drops:
                    x, y = (dx - fi * 3) % BW, (dy + fi * 22) % 340
                    for r in range(length):
                        off = ((y + r) * BW + x) * 3
                        if off + 3 < len(layer):
                            layer[off:off + 3] = b"\xc8\xd8\xf0"
            if action == "boom" and t > .2:
                g = (t - .2) * 12
                for dx, sx, sy, vy, col in debris:
                    xx, yy = BW / 2 + sx * g / 2, 190 + sy * g / 3 + vy * g / 5 + 6 * g * g
                    if yy < 334:
                        blit(layer, [(r, 0, b"\xf0\x8c\x40" * 3 if int(dx) % 2 else bytes(col) * 3) for r in range(3)], xx, yy)
                shake_y = (3 if fi % 2 else -3) if t < .5 else 0
            ox = PAD + int(pan * (t - .5)) + shake_x
            oy = PAD + shake_y
            ox, oy = max(0, min(2 * PAD, ox)), max(0, min(2 * PAD, oy))
            frame = bytearray(W * H * 3)
            for y in range(CAP_Y):
                st = ((y + oy) * BW + ox) * 3
                frame[y * W * 3:(y + 1) * W * 3] = layer[st:st + W * 3]
            frame[CAP_Y * W * 3:] = caption_cache(beat, min(words, int(words * (t * 1.08) + 1)), light, captions)
            if story.get("title") and bi == 0 and sec < 3 and captions:
                tc = Canvas(W, H, LIGHTS[light]["bg"])
                tc.px[:] = frame
                for row, line in enumerate(wrap(story["title"])[:3]):
                    draw_text(tc, line, max(4, (W - len(line) * 12) // 2), 12 + row * 20, LIGHTS[light]["text"], 2)
                frame = tc.px
            if action == "flash":
                frame = bytearray([255]) * (W * H * 3) if fi < 2 else frame
            if previous is not None and fi < 3 and prev_place != place_name:
                for y in range(H):
                    if BAYER[y % 4][0] > fi * 5 + 3:
                        frame[y * W * 3:(y + 1) * W * 3] = previous[y * W * 3:(y + 1) * W * 3]
            yield bytes(frame)
        previous = bytes(frame)
        prev_place = place_name
        if action == "collapse" and bi:  # A collapse as the very first beat is a flash-forward hook.
            broken.add(place_name)


def caption_cache(beat, shown, light, captions):
    key = (beat["text"], shown, light, captions)
    if key not in caption_cache.cache:
        if len(caption_cache.cache) > 40:
            caption_cache.cache.clear()
        caption_cache.cache[key] = caption_strip(beat, shown, light, captions)
    return caption_cache.cache[key]


caption_cache.cache = {}


def write_png(path, rgb, w=W, h=H):
    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))
    raw = b"".join(b"\0" + rgb[y * w * 3:(y + 1) * w * 3] for y in range(h))
    pathlib.Path(path).write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def tone(freq, dur, vol, kind="square"):
    n, out, period = int(SR * dur), [], SR / max(1, freq)
    for i in range(n):
        env = min(1, i / 60) * (1 - i / n) ** 1.4
        ph = (i % period) / period
        v = (1 if ph < .5 else -1) if kind == "square" else (4 * abs(ph - .5) - 1)
        out.append(v * env * vol)
    return out


def noise(rng, dur, vol, decay=2.0, smooth=0):
    n, out, last = int(SR * dur), [], 0
    for i in range(n):
        v = rng.uniform(-1, 1)
        last = last * smooth + v * (1 - smooth)
        out.append(last * vol * (1 - i / n) ** decay)
    return out


def add(track, at, src):
    start = int(at * SR)
    for i, v in enumerate(src[:max(0, len(track) - start)]):
        track[start + i] += v


def score(beats, path, seed=1):
    """March score plus synthesized SFX, on the same beat table the picture uses."""
    rng = random.Random(seed)
    total = sum(beat_frames(b) for b in beats) / FPS
    track = array.array("f", bytes(4 * int(total * SR)))
    major, minor = (0, 4, 7, 12, 7, 4, 9, 7), (0, 3, 7, 12, 7, 3, 8, 7)
    cache, t, sad = {}, 0.0, False
    steps = int(total * 4)
    bars = []
    for b in beats:
        d = beat_frames(b) / FPS
        bars.append((t, t + d, b))
        t += d

    def note(f, d, v, kind):
        k = (f, d, v, kind)
        if k not in cache:
            cache[k] = tone(f, d, v, kind)
        return cache[k]

    root = 110 * 2 ** (rng.randint(0, 5) / 12)
    idx = 0
    for st in range(steps):
        at = st * .25
        while idx + 1 < len(bars) and at >= bars[idx][1]:
            if bars[idx][2]["action"] == "collapse":
                sad = True
            idx += 1
        b = bars[idx][2]
        scale = minor if sad or b["light"] in MINOR else major
        if st % 4 == 0:
            add(track, at, note(root * 2 ** (scale[(st // 16) % 2 * 2] / 12), .4, .16, "tri"))
        if st % 2 == 0:
            add(track, at, note(root * 2 * 2 ** (scale[(st // 2) % 8] / 12), .22, .055, "square"))
        if st % 4 == 2:
            add(track, at, noise(rng, .09, .1))
    for start, end, b in bars:
        act = b["action"]
        if act == "march":
            k = start
            while k < end:
                add(track, k, noise(rng, .07, .3, smooth=.75))
                k += .5
        elif act == "walk":
            k = start
            while k < end:
                add(track, k, noise(rng, .05, .18, smooth=.75))
                k += .5
        elif act == "shake":
            add(track, start, noise(rng, end - start, .22, decay=.2, smooth=.97))
        elif act == "collapse":
            add(track, start + (end - start) * .2, noise(rng, min(1.6, end - start), .75, decay=1.5, smooth=.6))
            if b["place"] in ("bridge", "harbor"):
                add(track, start + (end - start) * .5, noise(rng, min(1.2, end - start), .4, decay=1, smooth=.3))
        elif act == "boom":
            add(track, start + (end - start) * .2, noise(rng, min(1.2, end - start), .8, decay=2, smooth=.8))
        elif act == "flash":
            add(track, start, noise(rng, .35, .55, decay=3, smooth=.9))
        elif act == "talk":
            k = start
            while k < end:
                add(track, k, note(rng.choice((520, 620, 700)), .05, .05, "square"))
                k += .24
        if act == "rain" or b["light"] == "storm":
            add(track, start, noise(rng, end - start, .07, decay=0))
        add(track, start, noise(rng, .12, .06, decay=1))
    peak = max(max(track), -min(track), 1e-6)
    k = 29000 / peak
    pcm = array.array("h", (int(v * k) for v in track))
    if sys.byteorder == "big":
        pcm.byteswap()
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR); w.writeframes(pcm.tobytes())


def need_tools():
    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            raise Refused(f"{tool} is not on PATH. Install ffmpeg and try again")


def probe_seconds(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    try:
        d = float(r.stdout.strip())
    except ValueError:
        raise Refused(f"{path} is not a readable audio or video file")
    if r.returncode or d <= 0:
        raise Refused(f"{path} is not a readable audio or video file")
    return d


def read_story(path, captions=True):
    try:
        return parse(pathlib.Path(path).read_text(encoding="utf-8-sig"), captions)
    except (OSError, UnicodeDecodeError) as e:
        raise Refused(f"cannot read {path}: {e}")


def render(story, out, voice=None, captions=True, seed=1, size=(OUT_W, OUT_H)):
    need_tools()
    out = pathlib.Path(out)
    length = probe_seconds(voice) if voice else None
    beats = timed(story, length)
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        bed = pathlib.Path(tmp) / "bed.wav"
        score(beats, bed, seed)
        cmd = ["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", "-i", str(bed)]
        if voice:
            cmd += ["-i", str(voice), "-filter_complex", f"[0:v]scale={size[0]}:{size[1]}:flags=neighbor,fps={OUT_FPS}[v];[2:a]aformat=sample_rates=48000:channel_layouts=mono,asplit=2[vc][key];[1:a]aformat=sample_rates=48000:channel_layouts=mono[bedr];[bedr][key]sidechaincompress=threshold=0.02:ratio=8:release=250[duck];[duck][vc]amix=inputs=2:normalize=0,loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000[a]", "-map", "[v]", "-map", "[a]"]
        else:
            cmd += ["-vf", f"scale={size[0]}:{size[1]}:flags=neighbor,fps={OUT_FPS}", "-map", "0:v", "-map", "1:a", "-af", "loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000"]
        cmd += ["-c:v", "libx264", "-crf", "18", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", "-shortest", "-movflags", "+faststart", str(out)]
        err = tempfile.TemporaryFile()
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=err)
        count = 0
        try:
            for frame in frames(dict(title=story["title"], beats=beats), seed, captions):
                proc.stdin.write(frame)
                count += 1
            proc.stdin.close()
        except OSError:
            pass
        if proc.wait() or not out.exists():
            err.seek(0)
            out.unlink(missing_ok=True)
            tail = err.read().decode("utf-8", "replace").strip().splitlines()[-1:] or ["unknown error"]
            raise Refused(f"ffmpeg could not render: {tail[0]}")
    return dict(frames=count, seconds=sum(beat_frames(b) for b in beats) / FPS, beats=len(beats))


def qa(path):
    """Upstream's frame-by-frame check: black, white, frozen and pop frames, plus loudness."""
    need_tools()
    dur = probe_seconds(path)
    w, h = 54, 96
    p = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vf", f"fps={FPS},scale={w}:{h}:flags=area,format=gray", "-f", "rawvideo", "-"], capture_output=True)
    data = p.stdout
    n = len(data) // (w * h)
    if not n:
        raise Refused(f"{path} has no video frames")
    fr = [data[i * w * h:(i + 1) * w * h] for i in range(n)]
    means = [sum(f) / len(f) for f in fr]
    problems, black = [], sum(1 for m in means if m < 10)
    whitest = 0
    run = cur = 0
    for a, b in zip(fr, fr[1:]):
        cur = cur + 1 if sum(abs(x - y) for x, y in zip(a[::3], b[::3])) / (len(a) / 3) < .15 else 0
        run = max(run, cur)
    pops = 0
    for i in range(1, n - 1):
        d1 = abs(means[i] - means[i - 1])
        d2 = abs(means[i] - means[i + 1])
        if d1 > 60 and d2 > 60:
            pops += 1
        whitest += means[i] > 253
    if black:
        problems.append(f"{black} black frames")
    if run / FPS > 2.5:
        problems.append(f"frozen for {run / FPS:.1f} s")
    if pops:
        problems.append(f"{pops} pop frames")
    if whitest > FPS:
        problems.append(f"{whitest} blank white frames")
    a = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-af", "ebur128=framelog=quiet", "-f", "null", "-"], capture_output=True, encoding="utf-8", errors="replace")
    m = re.search(r"I:\s+(-?[\d.]+) LUFS", a.stderr)
    lufs = float(m.group(1)) if m and "Audio:" in a.stderr else None
    if lufs is None or lufs < -40:
        problems.append("no audible sound")
    print(f"{dur:.1f} s, {n} frames checked, loudness {lufs if lufs is not None else 'none'} LUFS")
    for item in problems:
        print(f"problem: {item}")
    print("qa: " + ("FAILED" if problems else "clean"))
    return 1 if problems else 0


def stills(story, out, captions=True, seed=1):
    out = pathlib.Path(out)
    out.mkdir(parents=True, exist_ok=True)
    beats = timed(story)
    marks, at = {}, 0
    for i, b in enumerate(beats):
        n = beat_frames(b)
        marks[at + int(n * .6)] = i
        at += n
    for fi, frame in enumerate(frames(dict(title=story["title"], beats=beats), seed, captions)):
        if fi in marks:
            write_png(out / f"beat{marks[fi] + 1:02d}.png", frame)
    return len(marks)


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(prog="pixelshorts", description=__doc__.splitlines()[0])
    ap.add_argument("--demo", action="store_true", help="render the bundled story to a temp folder and check it")
    sub = ap.add_subparsers(dest="cmd")
    r = sub.add_parser("render", help="story file to mp4")
    r.add_argument("story"); r.add_argument("-o", "--out", default="short.mp4")
    r.add_argument("--voice", help="a narration file. The picture stretches to fit it and the score ducks under it")
    r.add_argument("--no-captions", action="store_true"); r.add_argument("--seed", type=int, default=1)
    r.add_argument("--2k", dest="two_k", action="store_true", help="export 1440x2560 instead of 1080x1920")
    c = sub.add_parser("check", help="read the story and print the plan, renders nothing")
    c.add_argument("story"); c.add_argument("--no-captions", action="store_true")
    s = sub.add_parser("stills", help="one PNG per beat, to judge the look quickly")
    s.add_argument("story"); s.add_argument("-o", "--out", default="stills")
    s.add_argument("--no-captions", action="store_true"); s.add_argument("--seed", type=int, default=1)
    q = sub.add_parser("qa", help="check a finished video for black, frozen, pop and silent problems")
    q.add_argument("video")
    args = ap.parse_args(argv)
    try:
        if args.demo:
            story = read_story(pathlib.Path(__file__).resolve().parent / "examples" / "bridge.txt")
            out = pathlib.Path(tempfile.gettempdir()) / "pixelshorts-demo.mp4"
            info = render(story, out)
            print(f"wrote {out}: {info['seconds']:.1f} s, {info['beats']} beats, {OUT_W}x{OUT_H}")
            return qa(out)
        if args.cmd == "render":
            cap = not args.no_captions
            if pathlib.Path(args.out).resolve() in (pathlib.Path(args.story).resolve(), pathlib.Path(args.voice).resolve() if args.voice else None):
                raise Refused("the output would overwrite an input")
            story = read_story(args.story, cap)
            if args.voice and not pathlib.Path(args.voice).is_file():
                raise Refused(f"no such voice file: {args.voice}")
            size = (1440, 2560) if args.two_k else (OUT_W, OUT_H)
            info = render(story, args.out, args.voice, cap, args.seed, size)
            print(f"wrote {args.out}: {info['seconds']:.1f} s, {info['beats']} beats, {size[0]}x{size[1]}")
            return 0
        if args.cmd == "check":
            story = read_story(args.story, not args.no_captions)
            beats = timed(story)
            print(f"{len(beats)} beats, {sum(b['dur'] for b in beats):.1f} s at {WORDS_PER_SEC} words/s")
            for i, b in enumerate(beats, 1):
                print(f"{i:>2}. {b['dur']:>4.1f} s  {b['place']} {b['light']} {b['action']}")
            for note in lint(story):
                print(f"note: {note}")
            return 0
        if args.cmd == "stills":
            cap = not args.no_captions
            n = stills(read_story(args.story, cap), args.out, cap, args.seed)
            print(f"wrote {n} stills to {args.out}")
            return 0
        if args.cmd == "qa":
            if not pathlib.Path(args.video).is_file():
                raise Refused(f"no such file: {args.video}")
            return qa(args.video)
        ap.print_help()
        return 2
    except Refused as e:
        print(f"pixelshorts: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
