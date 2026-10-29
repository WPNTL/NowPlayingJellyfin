#!/usr/bin/env python3
"""
nowplaying.py - jellyfin "now playing" thingy for the cheap 3.5" ILI9486 spi lcd

  - something playing -> crunchy pixel poster + title + chunky progress bar
  - nothing playing   -> lil monster eyes looking around, blinking, eventually dozing off
  - themes are hot-swappable w/ the `display` cli (display miami, display snes, ...)

draws straight into the framebuffer (/dev/fb1). no X, no desktop, no pygame.
deps: python3-pil python3-numpy (apt)

env vars (the installer puts these in /etc/nowplaying/nowplaying.env):
  JELLYFIN_URL      default http://127.0.0.1:8096 (jellyfin's default port)
  JELLYFIN_API_KEY  required (dashboard > api keys)
  FB_DEVICE         auto-detects the ili9486 fb, falls back to /dev/fb1
  NP_ROTATE         0/90/180/270 if the image comes out upside down
  NP_SLEEP_MIN      mins idle before the monster falls asleep (default 10)
  NP_THEME          theme to use if `display` was never run (default pico)
  NP_LANG           on-screen text language, en or pt (default en). `display lang` wins over this
  NP_STATE          where the current theme is saved (default /var/lib/nowplaying/state.json)

testing stuff:
  --demo            fakes a movie, no jellyfin needed
  --preview DIR     dumps pngs into DIR instead of hitting the lcd
  --theme NAME      force a theme, ignores the state file
"""
import io
import math
import os
import random
import sys
import threading
import time
import unicodedata
import urllib.request

import numpy as np
from PIL import Image, ImageDraw, ImageOps

from themes import THEMES, DEFAULT_THEME, STATE_PATH, hx, load_state, resolve

# tried pygame first. turns out SDL2 dropped the fbcon driver so it just... doesn't. RIP
# import pygame
# os.environ["SDL_FBDEV"] = "/dev/fb1"
# os.environ["SDL_VIDEODRIVER"] = "fbcon"

__version__ = "1.0"   # 1.0 lets gooo (win95 update)

# ---------------------------------------------------------------- config ----
# JF_URL = os.environ.get("JELLYFIN_URL", "http://127.0.0.1:8097").rstrip("/")   # my box runs on 8097, oops
JF_URL = os.environ.get("JELLYFIN_URL", "http://127.0.0.1:8096").rstrip("/")
JF_KEY = os.environ.get("JELLYFIN_API_KEY", "")
ROTATE = int(os.environ.get("NP_ROTATE", "0"))
SLEEP_AFTER = float(os.environ.get("NP_SLEEP_MIN", "10")) * 60
POLL_SEC = 2.0
# spi @ 16mhz tops out ~6.5 full frames/s, don't get greedy.
# if u bump the overlay to speed=32000000 u can try NP_FPS=10, smoother road
FPS = float(os.environ.get("NP_FPS", "6"))
PIXEL_POSTER = os.environ.get("NP_PIXEL_POSTER") == "1"
MAX_RESPONSE = 8 * 1024 * 1024   # bytes, see jf_get
# DEBUG = os.environ.get("NP_DEBUG") == "1"

W, H = 240, 160  # logical canvas, gets 2x'd to 480x320 at the very end
SCALE = 2
CLEAR = (0, 0, 0, 0)


# ------------------------------------------------------------------ i18n ----
# started out pt-only (it's my native lang). english is the default now since this
# went public. adding a language = add a column here + in the themes w/ custom text
LANGS = ("en", "pt")
LANG = "en"   # ThemeWatch flips this when `display lang xx` runs
STRINGS = {
    "paused":  {"en": "PAUSED", "pt": "PAUSADO"},
    "movie":   {"en": "MOVIE", "pt": "FILME"},
    "episode": {"en": "EPISODE", "pt": "SERIE"},
    "playing": {"en": "PLAYING", "pt": "TOCANDO"},          # "+1 PLAYING" when 2+ ppl watch
    "offline": {"en": "NO CONNECTION", "pt": "SEM CONEXAO"},
    "idle":    {"en": "NOTHING PLAYING", "pt": "NADA TOCANDO"},
    # win95 bits
    "start":   {"en": "START", "pt": "INICIAR"},
    "menu":    {"en": ("FILE", "EDIT", "HELP"), "pt": ("ARQUIVO", "EDITAR", "AJUDA")},
    "error":   {"en": "ERROR", "pt": "ERRO"},
    "monster": {"en": "MONSTER.EXE", "pt": "MONSTRO.EXE"},
    "icons":   {"en": ("MOVIES", "SHOWS", "ANIME", "TRASH", "PC"),
                "pt": ("FILMES", "SERIES", "ANIMES", "LIXO", "PC")},
    # popups. max ~18 chars a line or they spill outta the box
    "popups":  {"en": (("NOT ENOUGH POPCORN", "MEMORY."),
                       ("ERROR 404:", "MOVIE NOT FOUND."),
                       ("MONSTER.EXE DID AN", "ILLEGAL OPERATION."),
                       ("COUCH IS EMPTY.", "WATCH SOMETHING!")),
                "pt": (("MEMORIA DE PIPOCA", "INSUFICIENTE."),
                       ("ERRO 404:", "FILME NAO ACHADO."),
                       ("O MONSTRO FEZ UMA", "OPERACAO ILEGAL."),
                       ("SOFA VAZIO.", "ASSISTE ALGO AI!"))},
    # cyberpunk blimp ad
    "blimp":   {"en": "MOVIES 24/7", "pt": "FILMES 24H"},
}


def tr(key):
    v = STRINGS[key]
    return v.get(LANG, v["en"])


def pick_lang(v):
    """theme strings can be a plain str or {"en": ..., "pt": ...}"""
    return v.get(LANG, v.get("en")) if isinstance(v, dict) else v


# ----------------------------------------------------------------- theme ----
class Theme:
    """wraps a THEMES entry so i can do T.bg instead of T['ui']['bg'] everywhere"""

    def __init__(self, name):
        name = resolve(name)   # "aquario" -> "aquarium" etc
        if name not in THEMES:
            print(f"[theme] '{name}' doesn't exist, falling back to {DEFAULT_THEME}", flush=True)
            name = DEFAULT_THEME
        raw = THEMES[name]
        self.name = name
        ui = {**THEMES[DEFAULT_THEME]["ui"], **raw["ui"]}   # missing keys -> pico
        self.c = {k: hx(v) for k, v in ui.items()}
        self.fire = [hx(v) for v in raw.get("fire", [])]
        self.label = pick_lang(raw.get("label", "NOW PLAYING"))
        # self.idle = raw.get("idle", "NADA TOCANDO")
        self.idle = pick_lang(raw["idle"]) if "idle" in raw else tr("idle")
        self.fx = raw.get("fx")
        self.face = raw.get("face", "monster")
        # self.prog_fish = raw.get("prog_fish", False)
        self.prog_sprite = raw.get("prog_sprite")   # None | "fish" | "pumpkin"
        self.raw = raw   # fx stuff (water gradient, fish colors...) reads straight from here

        # PIL wants a full 768 byte palette. pad w/ the FIRST color, not zeros,
        # otherwise pure black sneaks in as an option and the snes posters go muddy
        cols = [hx(v) for v in raw["poster"]]
        flat = [ch for rgb in cols for ch in rgb] + list(cols[0]) * (256 - len(cols))
        self.pal = Image.new("P", (1, 1))
        self.pal.putpalette(flat)

    def __getattr__(self, k):
        try:
            return self.__dict__["c"][k]
        except KeyError:
            raise AttributeError(k)


class ThemeWatch:
    """polls the state file the `display` cli writes. an mtime check/sec is basically free"""

    def __init__(self, forced=None):
        global LANG
        self.forced = forced
        self.mtime = self._mtime()
        name, LANG = self._read()
        self.T = Theme(forced or name)
        self.next_check = 0

    def _mtime(self):
        try:
            return os.stat(STATE_PATH).st_mtime_ns
        except OSError:
            return None

    def _read(self):
        st = load_state()
        name = resolve(st.get("theme") or os.environ.get("NP_THEME", DEFAULT_THEME))
        lang = (st.get("lang") or os.environ.get("NP_LANG", "en")).lower()
        return name, lang if lang in LANGS else "en"

    def get(self):
        global LANG
        if self.forced:
            return self.T
        now = time.time()
        if now >= self.next_check:
            self.next_check = now + 1
            m = self._mtime()
            if m != self.mtime:
                self.mtime = m
                name, lang = self._read()
                if name != self.T.name or lang != LANG:
                    print(f"[theme] {self.T.name}/{LANG} -> {name}/{lang}", flush=True)
                    LANG = lang                   # set BEFORE building the Theme, it reads it
                    self.T = Theme(name)
        return self.T


# ------------------------------------------------------------ 5x7 font ----
# hand typed. yes, really. took way longer than i'd like to admit
_G = {
    "A": "01110 10001 10001 11111 10001 10001 10001",
    "B": "11110 10001 10001 11110 10001 10001 11110",
    "C": "01110 10001 10000 10000 10000 10001 01110",
    "D": "11110 10001 10001 10001 10001 10001 11110",
    "E": "11111 10000 10000 11110 10000 10000 11111",
    "F": "11111 10000 10000 11110 10000 10000 10000",
    "G": "01110 10001 10000 10111 10001 10001 01111",
    "H": "10001 10001 10001 11111 10001 10001 10001",
    "I": "01110 00100 00100 00100 00100 00100 01110",
    "J": "00111 00010 00010 00010 00010 10010 01100",
    "K": "10001 10010 10100 11000 10100 10010 10001",
    "L": "10000 10000 10000 10000 10000 10000 11111",
    "M": "10001 11011 10101 10101 10001 10001 10001",
    "N": "10001 10001 11001 10101 10011 10001 10001",
    "O": "01110 10001 10001 10001 10001 10001 01110",
    "P": "11110 10001 10001 11110 10000 10000 10000",
    "Q": "01110 10001 10001 10001 10101 10010 01101",
    "R": "11110 10001 10001 11110 10100 10010 10001",
    "S": "01111 10000 10000 01110 00001 00001 11110",
    "T": "11111 00100 00100 00100 00100 00100 00100",
    "U": "10001 10001 10001 10001 10001 10001 01110",
    "V": "10001 10001 10001 10001 10001 01010 00100",
    "W": "10001 10001 10001 10101 10101 10101 01010",
    "X": "10001 10001 01010 00100 01010 10001 10001",
    "Y": "10001 10001 10001 01010 00100 00100 00100",
    "Z": "11111 00001 00010 00100 01000 10000 11111",
    "0": "01110 10001 10011 10101 11001 10001 01110",
    "1": "00100 01100 00100 00100 00100 00100 01110",
    "2": "01110 10001 00001 00010 00100 01000 11111",
    "3": "11111 00010 00100 00010 00001 10001 01110",
    "4": "00010 00110 01010 10010 11111 00010 00010",
    "5": "11111 10000 11110 00001 00001 10001 01110",
    "6": "00110 01000 10000 11110 10001 10001 01110",
    "7": "11111 00001 00010 00100 01000 01000 01000",
    "8": "01110 10001 10001 01110 10001 10001 01110",
    "9": "01110 10001 10001 01111 00001 00010 01100",
    " ": "00000 00000 00000 00000 00000 00000 00000",
    ".": "00000 00000 00000 00000 00000 01100 01100",
    ",": "00000 00000 00000 00000 01100 00100 01000",
    ":": "00000 01100 01100 00000 01100 01100 00000",
    ";": "00000 01100 01100 00000 01100 00100 01000",
    "-": "00000 00000 00000 11111 00000 00000 00000",
    "_": "00000 00000 00000 00000 00000 00000 11111",
    "!": "00100 00100 00100 00100 00100 00000 00100",
    "?": "01110 10001 00001 00010 00100 00000 00100",
    "/": "00001 00001 00010 00100 01000 10000 10000",
    "(": "00010 00100 01000 01000 01000 00100 00010",
    ")": "01000 00100 00010 00010 00010 00100 01000",
    "'": "00100 00100 01000 00000 00000 00000 00000",
    '"': "01010 01010 00000 00000 00000 00000 00000",
    "&": "01100 10010 10100 01000 10101 10010 01101",
    "#": "01010 01010 11111 01010 11111 01010 01010",
    "+": "00000 00100 00100 11111 00100 00100 00000",
    "@": "01110 10001 10111 10101 10111 10000 01111",
    "%": "11001 11010 00010 00100 01000 01011 10011",
}
FONT = {ch: [[c == "1" for c in row] for row in rows.split()] for ch, rows in _G.items()}


def clean(s):
    """strip accents + uppercase, font is ascii only (sorry 'Anéis')"""
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()
    return "".join(ch if ch in FONT else "?" for ch in s.upper())


def text(d, x, y, s, color, scale=1):
    for ch in clean(s):
        for r, row in enumerate(FONT[ch]):
            for c, on in enumerate(row):
                if on:
                    d.rectangle([x + c * scale, y + r * scale,
                                 x + (c + 1) * scale - 1, y + (r + 1) * scale - 1], fill=color)
        x += 6 * scale


def wrap(s, max_chars, max_lines):
    words, lines, cur = clean(s).split(), [], ""
    for w in words:
        while len(w) > max_chars:          # some absurdly long word, just chop it
            if cur:
                lines.append(cur)
                cur = ""
            lines.append(w[:max_chars])
            w = w[max_chars:]
        if not cur:
            cur = w
        elif len(cur) + 1 + len(w) <= max_chars:
            cur += " " + w
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1][:max_chars - 2].rstrip() + ".."
    return lines


def fmt_time(sec):
    sec = max(0, int(sec))
    h, m, s = sec // 3600, sec % 3600 // 60, sec % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


# -------------------------------------------------------------- jellyfin ----
def jf_get(path, raw=False, timeout=4):
    # old way. jf 10.11+ just 401s the query param now, took me a while to figure that one out.
    # leaving it here in case i ever point this at an older box
    # sep = "&" if "?" in path else "?"
    # req = urllib.request.Request(f"{JF_URL}{path}{sep}api_key={JF_KEY}")
    req = urllib.request.Request(f"{JF_URL}{path}", headers={
        "Authorization": f'MediaBrowser Client="NowPlaying8bit", Device="RaspberryPi", '
                         f'DeviceId="nowplaying-lcd", Version="{__version__}", Token="{JF_KEY}"',
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        # cap it. a pi 3 has 1gb of ram and a poster is like 50kb, nothing legit gets near 8mb
        data = r.read(MAX_RESPONSE + 1)
    if len(data) > MAX_RESPONSE:
        raise ValueError("response too big, ignoring it")
    if raw:
        return data
    import json  # lazy, only the api path needs it
    return json.loads(data)


POSTER_XY = (6, 18)      # top-left of the poster, logical px
POSTER_SIZE = (120, 180)  # REAL screen px (= 60x90 logical)


def prep_poster(img):
    """full res poster, cropped to 2:3 so weird aspect ratios don't squish"""
    return ImageOps.fit(img.convert("RGB"), POSTER_SIZE, Image.LANCZOS)

# old crunchy 8bit poster. still there w/ NP_PIXEL_POSTER=1 cuz it was kinda cool tbh
# def shrink(img):
#     return img.convert("RGB").resize((30, 45), Image.BILINEAR)


class State:
    """polls /Sessions on a thread, keeps whatever's playing"""

    def __init__(self, demo=False):
        self.lock = threading.Lock()
        self.now = None
        self.stamp = time.time()
        self.idle_since = time.time()
        self.stopped_at = 0          # last time smth went playing -> nothing
        self.error = None
        self.posters = {}
        self.demo = demo
        self.server_name = None   # ur server's display name, the cyberpunk blimp shows it

    def start(self):
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while True:
            try:
                if self.server_name is None and not self.demo:
                    self.server_name = jf_get("/System/Info/Public").get("ServerName") or "JELLYFIN"
                info = self._demo() if self.demo else self._fetch()
                with self.lock:
                    if info is None and self.now is not None:
                        self.idle_since = self.stopped_at = time.time()
                    self.now, self.stamp, self.error = info, time.time(), None
            except Exception as e:   # wifi blip, jellyfin restarting, whatever
                msg = f"{type(e).__name__}: {e}"
                if msg != self.error:
                    print(f"[jellyfin] {JF_URL} -> {msg}", flush=True)
                with self.lock:
                    self.error = msg
                    if self.now and time.time() - self.stamp > 10:
                        self.now = None   # don't keep "playing" a ghost forever
                        self.idle_since = time.time()
            time.sleep(POLL_SEC)

    def _fetch(self):
        sessions = jf_get("/Sessions?activeWithinSeconds=60")
        playing = [s for s in sessions if s.get("NowPlayingItem")]
        if not playing:
            return None
        # unpaused first. if 2 ppl are watching at once, whatever, first one wins
        playing.sort(key=lambda s: s.get("PlayState", {}).get("IsPaused", False))
        s = playing[0]
        it, ps = s["NowPlayingItem"], s.get("PlayState", {})
        is_ep = it.get("Type") == "Episode"
        poster_id = it.get("SeriesId") if is_ep and it.get("SeriesId") else it["Id"]
        if is_ep:
            title = it.get("SeriesName", it.get("Name", ""))
            sub = f"S{it.get('ParentIndexNumber', 0):02d}E{it.get('IndexNumber', 0):02d} {it.get('Name', '')}"
        else:
            title, sub = it.get("Name", ""), str(it.get("ProductionYear", ""))
        return {
            "id": poster_id, "title": title, "sub": sub,
            "kind": {"Episode": tr("episode"), "Movie": tr("movie")}.get(it.get("Type"), it.get("Type", "")),
            "pos": ps.get("PositionTicks", 0) / 1e7,
            "dur": (it.get("RunTimeTicks") or 0) / 1e7,
            "paused": ps.get("IsPaused", False),
            "who": f"{s.get('UserName', '?')} @ {s.get('DeviceName', '?')}",
            "poster": self.poster(poster_id),
            "others": len(playing) - 1,
        }

    def poster(self, item_id):
        if item_id in self.posters:
            return self.posters[item_id]
        img = None
        try:
            raw = jf_get(f"/Items/{item_id}/Images/Primary?maxHeight=360&quality=85", raw=True)
            img = prep_poster(Image.open(io.BytesIO(raw)))
        except Exception:
            pass   # no poster, we'll draw "???"
        if len(self.posters) > 20:
            self.posters.clear()
        self.posters[item_id] = img
        return img

    def _demo(self):
        t = time.time() % 90
        if t > 60:   # 60s "playing", 30s nothing, repeat
            return None
        if "demo" not in self.posters:   # fake poster, smooth so it shows off the full res
            p = Image.new("RGB", (240, 360))
            pd = ImageDraw.Draw(p)
            for y in range(360):
                pd.line([0, y, 239, y], fill=(20 + y // 2, 10 + y // 8, 90 + y // 4))
            pd.ellipse([60, 50, 180, 170], fill=(255, 180, 40))
            pd.polygon([(0, 359), (84, 200), (144, 270), (239, 180), (239, 359)], fill=(20, 20, 30))
            self.posters["demo"] = prep_poster(p)
        return {
            # big buck bunny = the open movie every media player demo uses lol
            "id": "demo", "title": "Big Buck Bunny",
            "sub": "2008", "kind": tr("movie"), "pos": 200 + t, "dur": 596,
            "paused": 45 < t < 52, "who": "demo @ Living Room TV",
            "poster": self.posters["demo"], "others": 0,
        }

    def snapshot(self):
        with self.lock:
            now = dict(self.now) if self.now else None
            if now and not now["paused"]:
                # interpolate between polls so the bar doesn't jump every 2s
                now["pos"] = min(now["dur"] or 1e9, now["pos"] + time.time() - self.stamp)
            return now, self.idle_since, self.stopped_at, self.error


_qcache = {}


def poster_for(item_id, big, T):
    """only used w/ NP_PIXEL_POSTER=1: crunch the poster into the theme palette"""
    key = (item_id, T.name)
    if key not in _qcache:
        if len(_qcache) > 40:
            _qcache.clear()   # lazy man's lru lol
        small = big.resize((30, 45), Image.BILINEAR)
        q = small.quantize(palette=T.pal, dither=Image.Dither.NONE)
        # floyd-steinberg looked kinda noisy at this res, flat reads way more "8bit" imo
        # q = small.quantize(palette=T.pal, dither=Image.Dither.FLOYDSTEINBERG)
        _qcache[key] = q.convert("RGB").resize((60, 90), Image.NEAREST)
    return _qcache[key]


# ------------------------------------------------------------ helpers ----
def blob(d, x0, y0, w, h, col, grow=0):
    """pixel-art rounded rect: 3 overlapping rects so the corners get chopped"""
    g = grow
    d.rectangle([x0 + 6 - g, y0 - g, x0 + w - 7 + g, y0 + h - 1 + g], fill=col)
    d.rectangle([x0 - g, y0 + 6 - g, x0 + w - 1 + g, y0 + h - 7 + g], fill=col)
    d.rectangle([x0 + 2 - g, y0 + 2 - g, x0 + w - 3 + g, y0 + h - 3 + g], fill=col)


# v1 of the floor. fake perspective (p*p), barely looked like it was moving on the
# actual lcd tbh. replaced by fx_road below, keeping it around for reference
# def fx_grid(d, t, T):
#     hz = 116
#     for k in range(-9, 10):
#         d.line([(120 + k * 7, hz), (120 + k * 34, H - 1)], fill=T.grid)
#     for i in range(7):
#         p = (i + (t * 0.8) % 1) / 7
#         y = hz + int(p * p * (H - hz))
#         d.line([(0, y), (W - 1, y)], fill=T.grid)
#     d.line([(0, hz), (W - 1, hz)], fill=T.accent)

ROAD_HZ = 112          # horizon line
ROAD_SPD = 1.6         # segments/sec. keep it under ~fps/2 or it strobes and looks like it's reversing (wagon wheel thing)
ROAD_HALF = 54         # road half-width @ the bottom of the screen
SUN_GAPS = {1, 2, 5, 6, 10, 14}   # rows (counted up from the horizon) cut out of the sun


def fx_road(d, t, T):
    """outrun style road. real-ish perspective this time: something at depth z lands at
    y = hz + k/z, so scrolling z over time actually feels like driving.
    no car, the monster's the one driving lol"""
    hz = ROAD_HZ
    k = (H - hz) * 0.9
    cx = W // 2
    seg_base = int(t * ROAD_SPD)
    ph = (t * ROAD_SPD) % 1

    def y_at(z):
        return hz + k / max(z, 0.05)

    def x_at(X, y):
        # X = offset from center measured at the bottom row, shrinks toward the horizon
        return cx + X * (y - hz) / (H - hz)

    # sun peeking over the horizon, w/ the classic stripes
    r = 20
    for dy in range(1, r):
        if dy in SUN_GAPS:
            continue
        hw = int(math.sqrt(r * r - dy * dy))
        col = T.pct if dy > 12 else (T.warn if dy > 6 else T.accent)
        d.line([(cx - hw, hz - dy), (cx + hw, hz - dy)], fill=col)

    # floor grid, verticals first
    for j in range(-12, 13):
        d.line([(cx + j * 4, hz), (cx + j * 26, H - 1)], fill=T.grid)
    # then horizontals coming at u
    for n in range(24):
        y = int(y_at(n + 1 - ph))
        if y - hz < 2:
            break
        if y < H:
            d.line([(0, y), (W - 1, y)], fill=T.grid)

    # road surface on top of the grid
    d.polygon([(cx - 1, hz), (cx + 1, hz), (cx + ROAD_HALF + 4, H - 1), (cx - ROAD_HALF - 4, H - 1)],
              fill=T.road)

    # rumble strips + center dashes, one quad per segment.
    # seg id comes from absolute distance so the stripes don't flicker when ph wraps around
    for n in range(24):
        y_near = min(y_at(n + 1 - ph), H - 1)
        y_far = y_at(n + 2 - ph)
        if y_near - hz < 1:
            break
        alt = (seg_base + n) % 2
        for side in (-1, 1):
            a, b = side * (ROAD_HALF - 4), side * (ROAD_HALF + 4)
            d.polygon([(x_at(a, y_far), y_far), (x_at(b, y_far), y_far),
                       (x_at(b, y_near), y_near), (x_at(a, y_near), y_near)],
                      fill=T.rumble if alt else T.road)
        if alt:
            d.polygon([(x_at(-2.5, y_far), y_far), (x_at(2.5, y_far), y_far),
                       (x_at(2.5, y_near), y_near), (x_at(-2.5, y_near), y_near)],
                      fill=T.dash)

    # road edges
    for side in (-1, 1):
        d.line([(cx, hz), (cx + side * (ROAD_HALF + 4), H - 1)], fill=T.road_edge)
    d.line([(0, hz), (W - 1, hz)], fill=T.accent)


# ------------------------------------------------------------ aquarium ----
# fish sprite, facing right. X body, S stripe, E eye. two frames = tail wag
FISH_A = ["X...XXXX....",
          "XX.XXSXXXX..",
          "XXXXXSXXXEX.",
          "XXXXXSXXXXXX",
          "XX.XXSXXXX..",
          "X...XXXX...."]
FISH_B = ["....XXXX....",
          "XX.XXSXXXX..",
          ".XXXXSXXXEX.",
          ".XXXXSXXXXXX",
          "XX.XXSXXXX..",
          "....XXXX...."]
BUBBLE_S = [".X.", "X.X", ".X."]
BUBBLE_L = [".XXX.", "X..HX", "X...X", "X...X", ".XXX."]   # H = lil highlight


def banded(colors):
    """vertical gradient in hard bands, checkerboard-dithered at each seam. very 16bit.
    (pulled out of Aquarium so the graveyard sky can use it too)"""
    n = len(colors)
    band = H / n
    a = np.zeros((H, W, 3), np.uint8)
    for y in range(H):
        i = min(n - 1, int(y / band))
        a[y, :] = colors[i]
        if i < n - 1 and (i + 1) * band - y <= 3:   # last 3 rows of a band
            a[y, (y % 2)::2] = colors[i + 1]
    return a


def pix(d, x, y, rows, pal, s=1):
    """like sprite() but w/ a char -> color map. chars not in pal are see-through"""
    for r, row in enumerate(rows):
        for c, ch in enumerate(row):
            col = pal.get(ch)
            if col:
                d.rectangle([x + c * s, y + r * s, x + (c + 1) * s - 1, y + (r + 1) * s - 1], fill=col)


def draw_fish(d, x, y, right, frame, body, stripe, eye, s=1):
    rows = FISH_A if frame else FISH_B
    if not right:
        rows = [r[::-1] for r in rows]
    pix(d, int(x), int(y), rows, {"X": body, "S": stripe, "E": eye}, s)


class Aquarium:
    """the animated theme. dithered water + light rays, sand, swaying seaweed, fish going
    back n forth, bubbles. all of it sits BEHIND the eyes (eyes are a separate layer)"""

    WEEDS = [(12, 34, 0.0), (24, 22, 1.7), (206, 30, 0.9), (220, 40, 2.4), (168, 16, 3.1)]  # x, height, phase

    def __init__(self):
        self.base = {}      # (theme, playing) -> np array. static stuff, built once
        self.theme = None
        self.fish = []
        self.bubbles = []
        self.last_t = None
        self.was_playing = None

    def _build_base(self, T, coral=True):
        img = Image.fromarray(banded([hx(c) for c in T.raw["water"]]))
        d = ImageDraw.Draw(img)
        for x in range(W):   # sand
            top = 149 + int(round(2 * math.sin(x * 0.13) + math.sin(x * 0.41)))
            d.line([(x, top), (x, H - 1)], fill=T.sand)
            d.point((x, top), fill=T.sand_hi)
            if (x * 7919) % 13 == 0:   # cheap deterministic "random" specks
                d.point((x, top + 3 + x % 5), fill=T.sand_dk)
        for cx0 in ((58, 186) if coral else ()):   # coral, kinda. not on the playing screen, it ate the timestamps
            d.rectangle([cx0, 138, cx0 + 2, 150], fill=T.coral_dk)
            d.rectangle([cx0 - 5, 141, cx0 - 3, 147], fill=T.coral)
            d.rectangle([cx0 - 5, 147, cx0, 149], fill=T.coral)
            d.rectangle([cx0 + 5, 136, cx0 + 7, 145], fill=T.coral)
            d.rectangle([cx0 + 2, 145, cx0 + 7, 147], fill=T.coral)
            d.rectangle([cx0 - 1, 134, cx0 + 3, 138], fill=T.coral)
        return np.array(img)

    def _new_fish(self, T, anywhere=False):
        big = random.random() < 0.3
        right = random.random() < 0.5
        s = 2 if big else 1
        body, stripe = random.choice(T.raw["fish"])
        x = random.uniform(0, W - 24) if anywhere else (-12 * s if right else W)
        return {"x": x, "y": random.uniform(22, 128), "right": right, "s": s,
                "spd": random.uniform(14, 26) if big else random.uniform(8, 18),
                "body": hx(body), "stripe": hx(stripe), "ph": random.uniform(0, 6)}

    def _beams(self, t):
        m = Image.new("L", (W, H), 0)
        md = ImageDraw.Draw(m)
        for i in range(3):
            x = 30 + i * 80 + int(8 * math.sin(t * 0.4 + i * 1.3))
            md.polygon([(x, 0), (x + 12, 0), (x + 40, H), (x + 14, H)], fill=1)
        return np.array(m, bool)

    def draw_bg(self, img, t, T, playing=False, emit=None):
        """emit = (x, y) where bubbles come from on the playing screen (the progress bar fish)"""
        if self.theme != T.name:
            self.theme = T.name
            self.fish = [self._new_fish(T, anywhere=True) for _ in range(4)]
        if playing != self.was_playing:
            self.bubbles = []   # otherwise idle bubbles float right over the movie title
            self.was_playing = playing
        key = (T.name, playing)
        if key not in self.base:
            self.base[key] = self._build_base(T, coral=not playing)
        dt = 0.0 if self.last_t is None else min(0.5, max(0.0, t - self.last_t))
        self.last_t = t

        a = self.base[key].copy()
        if not playing:   # light rays wobbling down from the surface
            m = self._beams(t)
            a[m] = np.minimum(a[m].astype(np.int16) + 14, 255).astype(np.uint8)
        img.paste(Image.fromarray(a), (0, 0))
        d = ImageDraw.Draw(img)

        if not playing:
            # seaweed
            for x0, hgt, ph in self.WEEDS:
                n = hgt // 3
                for s in range(n):
                    dx = 3.5 * (s / n) * math.sin(t * 1.4 + ph + s * 0.35)
                    x, y = int(x0 + dx), 149 - s * 3
                    d.rectangle([x - 1, y - 3, x + 1, y], fill=T.weed_b if s % 2 else T.weed_a)
                    if s % 3 == 1:   # lil leaf
                        lx = x + 2 if (s // 3) % 2 else x - 4
                        d.rectangle([lx, y - 2, lx + 2, y - 1], fill=T.weed_b)
            # fish
            for i, f in enumerate(self.fish):
                f["x"] += f["spd"] * dt * (1 if f["right"] else -1)
                if f["x"] > W + 2 or f["x"] < -12 * f["s"] - 2:
                    self.fish[i] = f = self._new_fish(T)
            for f in sorted(self.fish, key=lambda f: f["s"]):   # small ones = far away, draw first
                y = f["y"] + 2 * math.sin(t * 1.5 + f["ph"])
                draw_fish(d, f["x"], y, f["right"], int(t * 4 + f["ph"]) % 2,
                          f["body"], f["stripe"], T.fish_eye, f["s"])
            # self.fish.append(self._new_fish(T))   # lol no. tried 12 fish once, way too busy

        # bubbles
        if playing:
            # v1 had them rising up the right edge, went straight thru the title. nope
            # x, y = random.uniform(218, 232), 114
            if emit and random.random() < 1.5 * dt:
                self.bubbles.append({"x": emit[0], "y": emit[1], "vy": random.uniform(8, 14),
                                     "big": False, "ph": random.uniform(0, 6)})
        elif random.random() < 3.0 * dt:
            if random.random() < 0.15:
                x, y = 118, 104   # the monster blowing one every now n then
            else:
                x, y = random.choice([w[0] for w in self.WEEDS] + [random.uniform(8, 232)]), 146
            self.bubbles.append({"x": x, "y": y, "vy": random.uniform(10, 22),
                                 "big": random.random() < 0.35, "ph": random.uniform(0, 6)})
        top = 104 if playing else 14   # playing: pop before reaching the "who" line
        for b in self.bubbles:
            b["y"] -= b["vy"] * dt
        self.bubbles = [b for b in self.bubbles if b["y"] > top][-30:]
        for b in self.bubbles:
            bx = b["x"] + 1.5 * math.sin(t * 3 + b["ph"])
            pix(d, int(bx), int(b["y"]), BUBBLE_L if b["big"] else BUBBLE_S,
                {"X": T.foam, "H": T.title})

        # wavy surface right under the top bar
        for x in range(0, W, 2):
            y = 11 + int(1.5 * math.sin(x * 0.12 + t * 2.2))
            d.rectangle([x, y, x + 1, y], fill=T.foam)


# ----------------------------------------------------------- graveyard ----
BAT_UP = ["X.........X",
          "XX..X.X..XX",
          "XXX.RXR.XXX",
          ".XXXXXXXXX.",
          "....XXX...."]
BAT_DN = ["....X.X....",
          "....RXR....",
          ".XXXXXXXXX.",
          "XXX.XXX.XXX",
          "X.........X"]
GHOST = ["...XXXXX...",
         ".XXXXXXXXX.",
         ".XXXXXXXXX.",
         "XXEEXXXEEXX",
         "XXEEXXXEEXX",
         "XXXXXXXXXXX",
         "XXXXXEXXXXX",
         "XXXXEXEXXXX",
         "XXXXXEXXXXX",
         "XXXXXXXXXXX"]
GHOST_HEM = [["XX.XXX.XXX.", "X...X...X.."],   # 2 frames for the wavy bottom
             [".XXX.XXX.XX", "..X...X...X"]]
PUMPKIN = ["....G....",
           ".DOOGOOD.",
           "OOOOOOOOO",
           "OOYODOYOO",
           "OOOOYOOOO",
           "OYOOOOOYO",
           "OOYYYYYOO",
           ".DOOOOOD."]


def draw_pumpkin(d, x, y, T, s=1):
    glow = T.candle if random.random() < 0.7 else T.candle_b   # candle flicker
    pix(d, int(x), int(y), PUMPKIN, {"O": T.pumpkin, "D": T.pumpkin_dk, "G": T.stem, "Y": glow}, s)


class Graveyard:
    """halloween bg: purple night, twinkly stars, moon w/ clouds drifting past, a hill w/
    graves + a dead tree, fog rolling along the ground, flickering pumpkins, bats, and a
    lil ghost that floats by every now n then. spooky szn"""

    MOON = (206, 36, 15)                 # x, y, r
    GRAVES = ((50, "rip"), (104, "cross"), (156, "stone"))
    PUMPKINS = (18, 178)

    def __init__(self):
        self.base = {}
        self.theme = None
        self.stars = []
        self.bats = []
        self.ghost = None
        self.next_ghost = 6.0
        self.pbat = None                 # the one bat on the playing screen
        self.next_pbat = 4.0
        self.last_t = None
        # fog texture: blurry noise, 2 screens wide so it can scroll + wrap
        rs = np.random.RandomState(4)
        f = rs.random_sample((26, W * 2))
        for _ in range(4):               # poor man's blur
            f = (f + np.roll(f, 1, 1) + np.roll(f, -1, 1) + np.roll(f, 2, 1) + np.roll(f, -2, 1)) / 5
            f = (f + np.roll(f, 1, 0) + np.roll(f, -1, 0)) / 3
        self.fog = (f - f.min()) / (f.max() - f.min())

    @staticmethod
    def ground_y(x):
        return 134 + int(round(3 * math.sin(x * 0.025 + 1) + 1.5 * math.sin(x * 0.11)))

    def _build_base(self, T, playing):
        img = Image.fromarray(banded([hx(c) for c in T.raw["sky"]]))
        d = ImageDraw.Draw(img)
        if playing:
            # just a lil strip of ground under the timestamps, nothing behind the text
            for x in range(W):
                d.line([(x, 151 + (x // 7) % 2), (x, H - 1)], fill=T.ground)
            return np.array(img)

        # stars (dim ones baked in, twinkle happens per frame)
        rs = random.Random(13)
        mx, my, r = self.MOON
        self.stars = []
        while len(self.stars) < 26:
            x, y = rs.randrange(2, W - 2), rs.randrange(14, 110)
            if math.hypot(x - mx, y - my) > r + 8:
                self.stars.append((x, y))
                d.point((x, y), fill=T.star_dim)

        # moon + dithered glow ring
        for yy in range(my - r - 6, my + r + 7):
            for xx in range(mx - r - 6, mx + r + 7):
                dd = math.hypot(xx - mx, yy - my)
                if r < dd <= r + 5 and (xx + yy) % 2 == 0:
                    d.point((xx, yy), fill=T.moon_glow)
        d.ellipse([mx - r, my - r, mx + r, my + r], fill=T.moon)
        for cx0, cy0, cr in ((-6, -4, 3), (4, 5, 2), (5, -7, 2), (-3, 7, 1)):   # craters
            d.ellipse([mx + cx0 - cr, my + cy0 - cr, mx + cx0 + cr, my + cy0 + cr], fill=T.moon_dk)

        # dead tree on the right, under the moon. classic
        tb = self.ground_y(226)
        for seg, wdt in ((((226, tb), (222, 96)), 3), (((223, 106), (212, 94)), 2), (((212, 94), (205, 96)), 1),
                         (((212, 94), (210, 85)), 1), (((222, 100), (233, 91)), 2), (((233, 91), (238, 84)), 1),
                         (((222, 96), (218, 84)), 2), (((218, 84), (221, 77)), 1)):
            d.line(seg, fill=T.tree, width=wdt)

        # hill
        for x in range(W):
            gy = self.ground_y(x)
            d.line([(x, gy), (x, H - 1)], fill=T.ground)
            if x % 4 == 0:   # grass tuft
                d.point((x, gy - 1), fill=T.grass)

        # graves (sunk a couple px into the hill)
        for gx, kind in self.GRAVES:
            gy = self.ground_y(gx + 8) + 2
            if kind == "stone":     # cross-shaped stone
                d.rectangle([gx + 5, gy - 22, gx + 9, gy], fill=T.grave)
                d.rectangle([gx, gy - 17, gx + 14, gy - 13], fill=T.grave)
                d.line([(gx + 9, gy - 22), (gx + 9, gy)], fill=T.grave_dk)
            else:
                wd = 20 if kind == "rip" else 14
                d.rectangle([gx, gy - 16, gx + wd, gy], fill=T.grave)
                d.ellipse([gx, gy - 22, gx + wd, gy - 10], fill=T.grave)
                d.line([(gx + wd, gy - 16), (gx + wd, gy)], fill=T.grave_dk)   # shade
                if kind == "rip":
                    text(d, gx + 2, gy - 15, "RIP", T.grave_dk)
                else:
                    d.rectangle([gx + 6, gy - 17, gx + 7, gy - 6], fill=T.grave_dk)
                    d.rectangle([gx + 3, gy - 14, gx + 10, gy - 13], fill=T.grave_dk)
        return np.array(img)

    def _new_bat(self, playing=False):
        right = random.random() < 0.5
        s = 1 if playing or random.random() < 0.7 else 2
        return {"x": -11 * s if right else W, "right": right, "s": s,
                "y": random.uniform(104, 108) if playing else random.uniform(18, 74),
                "spd": random.uniform(20, 34), "ph": random.uniform(0, 6)}

    def _move(self, o, dt):
        o["x"] += o["spd"] * dt * (1 if o["right"] else -1)
        return -30 < o["x"] < W + 2   # still on screen?

    def _draw_bat(self, d, b, t, T):
        flap = int(t * 7 + b["ph"]) % 2
        y = b["y"] + 4 * math.sin(t * 2.3 + b["ph"])
        pix(d, int(b["x"]), int(y), BAT_UP if flap else BAT_DN, {"X": T.bat, "R": T.bat_eye}, b["s"])

    def draw_bg(self, img, t, T, playing=False, emit=None):
        if self.theme != T.name:
            self.theme = T.name
            self.bats = [dict(self._new_bat(), x=random.uniform(0, W - 20)) for _ in range(3)]
        key = (T.name, playing)
        if key not in self.base:
            self.base[key] = self._build_base(T, playing)
        dt = 0.0 if self.last_t is None else min(0.5, max(0.0, t - self.last_t))
        self.last_t = t

        a = self.base[key].copy()
        if not playing:
            # fog: 2 layers of the noise texture scrolling at diff speeds, dithered in
            y0 = 126
            rows = np.arange(self.fog.shape[0])[:, None]
            cols = np.arange(W)[None, :]
            checker = (rows + cols + int(t * 2)) % 2 == 0
            # thresholds were .52/.62 at first, looked like tv static down there. thinner = spookier
            for spd, thr, col in ((5, 0.6, T.fog), (11, 0.72, T.fog_hi)):
                off = int(t * spd) % (W * 2)
                tex = np.roll(self.fog, -off, axis=1)[:, :W]
                m = (tex > thr) & checker
                a[y0:y0 + self.fog.shape[0]][m] = col
                checker = ~checker   # 2nd layer on the other checker squares
        img.paste(Image.fromarray(a), (0, 0))
        d = ImageDraw.Draw(img)

        if playing:
            # one bat crossing the gap between the text and the progress bar now n then
            if self.pbat is None and t > self.next_pbat:
                self.pbat = self._new_bat(playing=True)
            if self.pbat:
                if self._move(self.pbat, dt):
                    self._draw_bat(d, self.pbat, t, T)
                else:
                    self.pbat = None
                    self.next_pbat = t + random.uniform(10, 25)
            return

        # twinkle
        for i, (x, y) in enumerate(self.stars):
            if (int(t * 3) + i * 7) % 9 == 0:
                d.point((x, y), fill=T.star)
                for ox, oy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    d.point((x + ox, y + oy), fill=T.star_dim)

        # clouds sliding across the moon
        for i, (yy, wd, spd) in enumerate(((30, 34, 3.0), (44, 26, 4.5))):
            cx0 = (t * spd + i * 140) % (W + 80) - 40
            d.ellipse([cx0, yy - 4, cx0 + wd, yy + 4], fill=T.cloud)
            d.ellipse([cx0 + wd * 0.25, yy - 8, cx0 + wd * 0.7, yy + 2], fill=T.cloud)

        # ghost, every 15-35s
        if self.ghost is None and t > self.next_ghost:
            self.ghost = {"x": -12.0, "right": True, "spd": random.uniform(9, 14),
                          "y": random.uniform(56, 84), "ph": random.uniform(0, 6)}
        if self.ghost:
            g = self.ghost
            if self._move(g, dt):
                gy = int(g["y"] + 3 * math.sin(t * 2 + g["ph"]))
                rows = GHOST + GHOST_HEM[int(t * 3) % 2]
                pix(d, int(g["x"]), gy, rows, {"X": T.ghost, "E": T.ghost_eye})
                d.line([(int(g["x"]) + 10, gy + 2), (int(g["x"]) + 10, gy + 9)], fill=T.ghost_dk)  # lil shading
            else:
                self.ghost = None
                self.next_ghost = t + random.uniform(15, 35)

        # bats
        for i, b in enumerate(self.bats):
            if not self._move(b, dt):
                self.bats[i] = b = self._new_bat()
            self._draw_bat(d, b, t, T)
        # self.bats.append(self._new_bat())   # swarm mode. looked cool for like 2 sec then just noise

        # pumpkins go last so they sit in front of the fog
        for px in self.PUMPKINS:
            draw_pumpkin(d, px, self.ground_y(px + 9) - 14, T, 2)


# ----------------------------------------------------------- christmas ----
TREE_STAR = ["...X...",
             "...X...",
             "XXXXXXX",
             ".XXXXX.",
             "..XXX..",
             ".XX.XX.",
             "X.....X"]
GIFT = ["..Y.Y..",
        "...Y...",
        "RRRYRRR",
        "RRRYRRR",
        "YYYYYYY",
        "RRRYRRR",
        "RRRYRRR"]
# sleigh + 2 reindeer, flying left. one sprite, simpler than lining up 3
SLEIGH = ["X.X.......X.X......................",
          ".XX........XX..........XX..........",
          "XXX.......XXX.........XXXX.........",
          ".XXXXXXX..XXXXXXX...XXXXXXXXXX..X..",
          ".XXXXXXXX..XXXXXXXX.XXXXXXXXXXXXX..",
          ".X.X..X.X..X.X..X.X..XXXXXXXXXXX...",
          ".....................X.........X...",
          "....................XXXXXXXXXXXXX.."]
SLEIGH_LEGS = [".X.X..X.X..X.X..X.X", "..X.X..X.X..X.X..X."]   # trot frames for row 5


def draw_gift(d, x, y, T, s=1):
    pix(d, int(x), int(y), GIFT, {"R": T.gift, "Y": T.ribbon}, s)


class Snowscape:
    """xmas bg. starry night, snowy hills, a tree w/ chasing lights + glowing star, presents,
    a snowman who waves sometimes, a string of bulbs across the top, the odd sleigh, and
    snow in 2 layers. the near layer is drawn AFTER the eyes (draw_fg) so flakes fall in
    front of the monster. depth!! very proud of this one ngl"""

    TREE_X = 30
    TIERS = ((80, 99, 12), (92, 113, 18), (106, 128, 24))   # top y, bottom y, half width
    TREE_LIGHTS = ((27, 89), (34, 94), (23, 101), (31, 106), (39, 108), (18, 116), (27, 119),
                   (36, 121), (45, 124), (13, 125), (22, 127))
    SNOWMAN_X = 212

    def __init__(self):
        self.base = {}
        self.theme = None
        self.stars = []
        self.far, self.near = [], []
        self.sleigh = None
        self.next_sleigh = 8.0
        self.last_t = None

    @staticmethod
    def ground_y(x):
        return 132 + int(round(2 * math.sin(x * 0.03 + 0.5) + 1.2 * math.sin(x * 0.13)))

    def _flake(self, near, anywhere=False):
        top = 14
        return {"x": random.uniform(0, W), "y": random.uniform(top, H) if anywhere else top - 2,
                "spd": random.uniform(14, 24) if near else random.uniform(5, 11),
                "amp": random.uniform(2, 5) if near else random.uniform(0.5, 2),
                "ph": random.uniform(0, 6)}

    def _build_base(self, T, playing):
        img = Image.fromarray(banded([hx(c) for c in T.raw["sky"]]))
        d = ImageDraw.Draw(img)
        if playing:
            for x in range(W):   # snow strip under the timestamps
                d.line([(x, 151 + (x // 9) % 2), (x, H - 1)], fill=T.snow)
            return np.array(img)

        rs = random.Random(25)
        self.stars = []
        for _ in range(24):
            x, y = rs.randrange(2, W - 2), rs.randrange(24, 100)
            self.stars.append((x, y))
            d.point((x, y), fill=T.star_dim)

        # hills, back one first (bluish) then the front one (white)
        for x in range(W):
            by = 124 + int(round(4 * math.sin(x * 0.02 + 2) + 2 * math.sin(x * 0.07)))
            d.line([(x, by), (x, H - 1)], fill=T.hill_back)
        for x in range(W):
            gy = self.ground_y(x)
            d.line([(x, gy), (x, H - 1)], fill=T.snow)
            if (x * 37) % 11 == 0:
                d.point((x, gy + 4 + x % 6), fill=T.snow_shadow)

        # tree
        cx = self.TREE_X
        d.rectangle([cx - 3, 126, cx + 3, self.ground_y(cx) + 1], fill=T.trunk)
        for ty0, ty1, hw in reversed(self.TIERS):   # bottom tier first
            d.polygon([(cx, ty0), (cx + hw, ty1), (cx - hw, ty1)], fill=T.tree_a)
            d.polygon([(cx, ty0 + 3), (cx - 1, ty1 - 2), (cx - hw + 5, ty1 - 2)], fill=T.tree_b)   # lit side
            for x in range(cx - hw + 1, cx + hw, 2):   # snow on the branch tips, jaggy
                d.point((x, ty1 - (x // 2) % 2), fill=T.snow)
        # presents
        for gx, gy, wd, hh, col in ((40, 125, 10, 9, T.gift), (51, 128, 8, 6, T.gift_b), (6, 128, 8, 7, T.gift_c)):
            gy = min(gy, self.ground_y(gx + wd // 2) + 1 - hh)
            d.rectangle([gx, gy, gx + wd, gy + hh], fill=col)
            d.line([(gx + wd // 2, gy), (gx + wd // 2, gy + hh)], fill=T.ribbon)
            d.line([(gx, gy + hh // 2), (gx + wd, gy + hh // 2)], fill=T.ribbon)
            d.point((gx + wd // 2 - 1, gy - 1), fill=T.ribbon)
            d.point((gx + wd // 2 + 1, gy - 1), fill=T.ribbon)

        # snowman body (arms/scarf tail are animated, drawn per frame)
        sx = self.SNOWMAN_X
        for cy0, r in ((124, 11), (108, 8), (95, 6)):
            d.ellipse([sx - r, cy0 - r, sx + r, cy0 + r], fill=T.snow)
            d.arc([sx - r, cy0 - r, sx + r, cy0 + r], 300, 60, fill=T.snow_shadow, width=2)   # shading on the right
        d.rectangle([sx - 6, 87, sx + 6, 88], fill=T.hat)        # hat brim
        d.rectangle([sx - 4, 79, sx + 4, 87], fill=T.hat)
        d.rectangle([sx - 4, 85, sx + 4, 86], fill=T.scarf)      # hat band
        for ex in (-3, 2):
            d.rectangle([sx + ex, 93, sx + ex + 1, 94], fill=T.coal)
        d.polygon([(sx - 1, 96), (sx - 7, 97), (sx - 1, 98)], fill=T.carrot)   # nose points at the monster lol
        for by_ in (105, 110, 115):
            d.rectangle([sx, by_, sx + 1, by_ + 1], fill=T.coal)
        d.rectangle([sx - 6, 100, sx + 6, 102], fill=T.scarf)
        d.line([(sx - 7, 106), (sx - 16, 99)], fill=T.stick, width=1)   # left arm, static
        d.line([(sx - 13, 101), (sx - 15, 104)], fill=T.stick)          # lil twig
        return np.array(img)

    def _lights_string(self, d, t, T, sag=6):
        bulbs = [hx(c) for c in T.raw["bulbs"]]
        pts = []
        for x in range(0, W + 1, 4):
            u = ((x % 80) - 40) / 40
            pts.append((x, 13 + int(sag * (1 - u * u))))
        d.line(pts, fill=T.wire)
        for i, x in enumerate(range(5, W, 10)):
            u = ((x % 80) - 40) / 40
            y = 13 + int(sag * (1 - u * u)) + 1
            if (i + int(t * 3)) % 4 == 0:   # some off at any given time = chase effect
                continue
            col = bulbs[(i + int(t * 1.5)) % len(bulbs)]
            d.rectangle([x, y, x + 1, y + 2], fill=col)

    def draw_bg(self, img, t, T, playing=False, emit=None):
        if self.theme != T.name:
            self.theme = T.name
            self.far = [self._flake(False, True) for _ in range(45)]
            self.near = [self._flake(True, True) for _ in range(14)]
        key = (T.name, playing)
        if key not in self.base:
            self.base[key] = self._build_base(T, playing)
        dt = 0.0 if self.last_t is None else min(0.5, max(0.0, t - self.last_t))
        self.last_t = t
        self.dt = dt
        img.paste(Image.fromarray(self.base[key]), (0, 0))
        d = ImageDraw.Draw(img)

        # far snow (behind everything)
        # tried a few flakes on the playing screen too, but 1px flakes between letters
        # read like random apostrophes lol ("USER @'LIVING"). idle only
        # n_far = 14 if playing else len(self.far)
        if not playing:
            for f in self.far:
                f["y"] += f["spd"] * dt
                if f["y"] > self.ground_y(int(f["x"]) % W):
                    f.update(self._flake(False))
                x = int(f["x"] + f["amp"] * math.sin(t * 1.2 + f["ph"])) % W
                d.point((x, int(f["y"])), fill=T.snow_dim)

        self._lights_string(d, t, T, sag=3 if playing else 6)   # tighter when playing, clears the FILME tag
        if playing:
            return

        for i, (x, y) in enumerate(self.stars):
            if (int(t * 3) + i * 5) % 11 == 0:
                d.point((x, y), fill=T.star)

        # sleigh, every 25-50s
        if self.sleigh is None and t > self.next_sleigh:
            self.sleigh = {"x": float(W), "y": random.uniform(26, 38), "spd": random.uniform(20, 28)}
        if self.sleigh:
            s = self.sleigh
            s["x"] -= s["spd"] * dt
            if s["x"] < -40:
                self.sleigh = None
                self.next_sleigh = t + random.uniform(25, 50)
            else:
                rows = list(SLEIGH)
                rows[5] = SLEIGH_LEGS[int(t * 6) % 2] + rows[5][19:]
                pix(d, int(s["x"]), int(s["y"] + 2 * math.sin(t * 3)), rows, {"X": T.sleigh})

        # tree star glow + chasing lights
        cx = self.TREE_X
        glow = int(t * 2) % 2
        pix(d, cx - 3, 73, TREE_STAR, {"X": T.tstar_b if glow else T.tstar})
        if glow:
            for ox, oy in ((-5, 3), (5, 3), (0, -3)):
                d.point((cx + ox, 76 + oy), fill=T.tstar)
        bulbs = [hx(c) for c in T.raw["bulbs"]]
        for i, (x, y) in enumerate(self.TREE_LIGHTS):
            if (i + int(t * 4)) % 5:
                d.rectangle([x, y, x + 1, y + 1], fill=bulbs[(i + int(t * 2)) % len(bulbs)])

        # snowman: right arm waves for ~2s every 8s, scarf tail flaps
        sx = self.SNOWMAN_X
        waving = (t % 8) < 2
        tip = (sx + 17, 96 if int(t * 5) % 2 else 92) if waving else (sx + 16, 101)
        d.line([(sx + 7, 106), tip], fill=T.stick)
        sway = int(1.5 * math.sin(t * 3))
        d.rectangle([sx + 3, 102, sx + 5, 108 + sway], fill=T.scarf)
        d.rectangle([sx + 5 + sway, 108 + sway, sx + 7 + sway, 109 + sway], fill=T.scarf)

    def draw_fg(self, d, t, T, playing=False):
        """near flakes, in front of the eyes. playing screen skips this, text stays readable"""
        if playing:
            return
        for f in self.near:
            f["y"] += f["spd"] * self.dt
            if f["y"] > H:
                f.update(self._flake(True))
            x = int(f["x"] + f["amp"] * math.sin(t * 1.6 + f["ph"])) % W
            d.rectangle([x, int(f["y"]), x + 1, int(f["y"]) + 1], fill=T.snow)
        # for f in self.near: d.point(...)   # 1px flakes in front looked like dead pixels lol


# ----------------------------------------------------------- cyberpunk ----
# hover car, facing right. T tail light, H headlight, W windows, G hover jets
CAR = ["...XXXXX....",
       "..XWWWWWX...",
       "TXXXXXXXXXXH",
       ".XXXXXXXXXX.",
       "..G......G.."]


def draw_car(d, x, y, T, right=True, s=1):
    rows = CAR if right else [r[::-1] for r in CAR]
    jets = T.neon_b if random.random() < 0.6 else T.neon_a   # jets flicker
    pal = {"X": T.car_body, "W": T.win_b, "T": T.car_tail, "H": T.car_head, "G": jets}
    pix(d, int(x), int(y), rows, pal, s)


class City:
    """cyberpunk bg. rainy neon city at night: 2 layers of skyline w/ flickering windows,
    blinking antenna lights, neon signs w/ dying tubes, wet street that reflects all of it,
    rain (near drops go in front of the eyes, same trick as the snow), hover cars, an ad
    blimp showing ur jellyfin server name, and every so often the whole frame glitches.
    not any specific game/movie, just the whole aesthetic"""

    STREET = 136
    # x, y, text, vertical?, border color, text color
    SIGNS = ((5, 48, "HOTEL", True, "neon_a", "neon_a"),
             (197, 64, "RAMEN", False, "neon_b", "neon_c"),
             (205, 94, "24H", False, "neon_a", "neon_b"))
    # near buildings: x, width, top. sides are tall, middle stays low so the eyes have room
    NEAR = ((0, 22, 30), (22, 18, 56), (40, 16, 44), (60, 30, 112), (96, 22, 118),
            (124, 26, 110), (156, 28, 116), (186, 16, 58), (202, 22, 40), (224, 16, 52))

    def __init__(self):
        self.base = {}
        self.theme = None
        self.server = "JELLYFIN"      # render() fills this in w/ the real server name
        self.flicker = []             # windows that turn on/off on their own
        self.beacons = []
        self.sign_off_until = [0.0] * len(self.SIGNS)
        self.far, self.near, self.splashes, self.cars = [], [], [], []
        self.next_car = 2.0
        self.blimp = None
        self.next_blimp = 5.0
        self.glitch_until = 0.0
        self.next_glitch = 4.0
        self.last_t = None
        self.dt = 0.0

    def _drop(self, near, anywhere=False):
        return {"x": random.uniform(0, W + 40), "y": random.uniform(-20, H) if anywhere else random.uniform(-20, 4),
                "vy": random.uniform(130, 170) if near else random.uniform(90, 130),
                "len": random.randint(8, 12) if near else random.randint(4, 6)}

    def _build_base(self, T, playing):
        img = Image.fromarray(banded([hx(c) for c in T.raw["sky"]]))
        d = ImageDraw.Draw(img)
        if playing:
            d.rectangle([0, 151, W - 1, H - 1], fill=T.street)
            d.line([(0, 151), (W - 1, 151)], fill=T.neon_b)   # neon curb, that's all u get here
            return np.array(img)

        rs = random.Random(77)
        # far skyline, all the way across, tiny dim windows
        x = 0
        while x < W:
            w, top = rs.randint(10, 24), rs.randint(62, 100)
            d.rectangle([x, top, x + w - 1, self.STREET], fill=T.bld_far)
            for wy in range(top + 3, self.STREET - 3, 5):
                for wx in range(x + 2, x + w - 2, 4):
                    if rs.random() < 0.35:
                        d.point((wx, wy), fill=T.win_far if rs.random() < 0.6 else T.win_off)
            x += w + rs.randint(0, 3)

        # near layer
        self.flicker, self.beacons = [], []
        wins = [T.win_a] * 7 + [T.win_b] * 2 + [T.win_c] * 2   # mostly warm, some neon
        for bx, bw, top in self.NEAR:
            d.rectangle([bx, top, bx + bw - 1, self.STREET], fill=T.bld_near)
            for wy in range(top + 4, self.STREET - 4, 6):
                for wx in range(bx + 2, bx + bw - 3, 5):
                    if rs.random() < 0.45:
                        col = rs.choice(wins)
                        d.rectangle([wx, wy, wx + 1, wy + 1], fill=col)
                        if rs.random() < 0.12:
                            self.flicker.append((wx, wy, col))
            if top < 70:   # antenna + blinky light on the tall ones
                ax = bx + bw // 2
                d.line([(ax, top - 9), (ax, top - 1)], fill=T.antenna)
                self.beacons.append((ax, top - 10))

        d.rectangle([0, self.STREET, W - 1, H - 1], fill=T.street)
        d.line([(0, self.STREET), (W - 1, self.STREET)], fill=T.curb)
        return np.array(img)

    def _signs(self, d, t, T):
        for i, (sx, sy, txt, vert, bc, tc) in enumerate(self.SIGNS):
            # dying neon tube: every so often a sign stutters for a sec
            if t > self.sign_off_until[i] + 1 and random.random() < 0.02:
                self.sign_off_until[i] = t + random.uniform(0.4, 1.2)
            if t < self.sign_off_until[i] and random.random() < 0.6:
                continue
            border, col = getattr(T, bc), getattr(T, tc)
            if vert:
                d.rectangle([sx - 1, sy - 2, sx + 7, sy + 8 * len(txt)], outline=border)
                for k, ch in enumerate(txt):
                    text(d, sx + 1, sy + k * 8, ch, col)
            else:
                wd = 6 * len(txt) + 2
                d.rectangle([sx - 2, sy - 2, sx + wd, sy + 8], outline=border)
                text(d, sx, sy, txt, col)

    def _blimp(self, img, d, t, T):
        b = self.blimp
        x, y = int(b["x"]), int(b["y"] + math.sin(t * 0.8))
        d.polygon([(x, y + 3), (x - 6, y - 2), (x - 6, y + 14), (x, y + 10)], fill=T.blimp_dk)   # tail fins
        d.ellipse([x, y, x + 44, y + 14], fill=T.blimp)
        d.rectangle([x + 18, y + 14, x + 26, y + 16], fill=T.blimp_dk)                           # gondola
        if int(t * 2) % 2:
            d.point((x + 44, y + 7), fill=T.beacon)
        # the ad screen. text scrolls, clipped to the screen by drawing on a lil scratch img
        msg = f"{self.server} - {tr('blimp')} - "
        scr = Image.new("RGB", (26, 9), T.screen)
        off = int(t * 14) % (6 * len(msg))
        sd = ImageDraw.Draw(scr)
        text(sd, 1 - off, 1, msg + msg, T.neon_c)
        img.paste(scr, (x + 9, y + 3))

    def draw_bg(self, img, t, T, playing=False, emit=None):
        if self.theme != T.name:
            self.theme = T.name
            self.far = [self._drop(False, True) for _ in range(40)]
            self.near = [self._drop(True, True) for _ in range(10)]
        key = (T.name, playing)
        if key not in self.base:
            self.base[key] = self._build_base(T, playing)
        dt = 0.0 if self.last_t is None else min(0.5, max(0.0, t - self.last_t))
        self.last_t, self.dt = t, dt
        img.paste(Image.fromarray(self.base[key]), (0, 0))
        if playing:
            return
        d = ImageDraw.Draw(img)

        for i, (wx, wy, col) in enumerate(self.flicker):
            if (int(t * 1.5) * 7 + i * 13) % 5 == 0:   # someone flipped the light switch
                d.rectangle([wx, wy, wx + 1, wy + 1], fill=T.win_off)
        if int(t * 1.2) % 2:
            for bx, by in self.beacons:
                d.point((bx, by), fill=T.beacon)
        self._signs(d, t, T)

        # blimp every 30-60s
        if self.blimp is None and t > self.next_blimp:
            self.blimp = {"x": -50.0, "y": random.uniform(15, 22)}
        if self.blimp:
            self.blimp["x"] += 6 * dt
            if self.blimp["x"] > W + 8:
                self.blimp = None
                self.next_blimp = t + random.uniform(30, 60)
            else:
                self._blimp(img, d, t, T)

        # hover cars
        if t > self.next_car:
            right = random.random() < 0.5
            self.cars.append({"x": -14.0 if right else float(W), "right": right,
                              "y": random.uniform(28, 44), "spd": random.uniform(45, 70)})
            self.next_car = t + random.uniform(3, 9)
        for c in self.cars:
            c["x"] += c["spd"] * dt * (1 if c["right"] else -1)
            tail = c["x"] - 8 if c["right"] else c["x"] + 12
            d.line([(tail, c["y"] + 2), (tail + 7, c["y"] + 2)], fill=T.win_off)   # lil light trail
            draw_car(d, c["x"], c["y"], T, c["right"])
        self.cars = [c for c in self.cars if -20 < c["x"] < W + 6]

        # wet street: mirror whats right above it, dim it, wobble each row a bit
        a = np.array(img)
        st = self.STREET
        street = np.array(T.street, np.float32)
        for k in range(H - st - 1):
            src = a[st - 1 - k].astype(np.float32) * 0.38 + street * 0.62
            a[st + 1 + k] = np.roll(src.astype(np.uint8), int(2 * math.sin(t * 4 + k * 0.9)), axis=0)
        img.paste(Image.fromarray(a), (0, 0))
        d = ImageDraw.Draw(img)

        # far rain + splashes
        for r in self.far:
            r["y"] += r["vy"] * dt
            r["x"] -= r["vy"] * dt / 4
            if r["y"] > st:
                self.splashes.append((r["x"], st + random.randint(2, 20), t))
                r.update(self._drop(False))
            d.line([(r["x"], r["y"]), (r["x"] + r["len"] / 4, r["y"] - r["len"])], fill=T.rain)
        self.splashes = [s for s in self.splashes if t - s[2] < 0.2][-30:]
        for sx, sy, _ in self.splashes:
            d.point((sx - 1, sy - 1), fill=T.rain_hi)
            d.point((sx + 1, sy - 1), fill=T.rain_hi)

    def draw_fg(self, d, t, T, playing=False):
        if playing:
            return
        for r in self.near:
            r["y"] += r["vy"] * self.dt
            r["x"] -= r["vy"] * self.dt / 4
            if r["y"] > H + 10:
                r.update(self._drop(True))
            d.line([(r["x"], r["y"]), (r["x"] + r["len"] / 4, r["y"] - r["len"])], fill=T.rain_hi)

    def post(self, img, t, playing=False):
        """the glitch. rgb split + a few slices torn sideways, for like half a sec"""
        if t > self.next_glitch:
            self.glitch_until = t + random.uniform(0.25, 0.6)
            self.next_glitch = t + (random.uniform(25, 45) if playing else random.uniform(6, 14))
        if t > self.glitch_until:
            return img
        a = np.array(img)
        k = random.choice((1, 2, 3))
        a[..., 0] = np.roll(a[..., 0], k, axis=1)
        a[..., 2] = np.roll(a[..., 2], -k, axis=1)
        for _ in range(random.randint(2, 5)):
            y0, h = random.randrange(0, H - 4), random.randint(2, 8)
            a[y0:y0 + h] = np.roll(a[y0:y0 + h], random.randint(-10, 10), axis=1)
        # a[::3] = 0   # interlace blackout, too much, made it look broken-broken
        return Image.fromarray(a)


# --------------------------------------------------------------- win95 ----
FOLDER = ["..DDDD........",
          ".DYYYYD.......",
          "DDDDDDDDDDDDD.",
          "DYYYYYYYYYYYYD",
          "DYYYYYYYYYYYYD",
          "DYYYYYYYYYYYYD",
          "DYYYYYYYYYYYYD",
          "DYYYYYYYYYYYYD",
          "DYYYYYYYYYYYYD",
          "DDDDDDDDDDDDDD"]
TRASH = ["...KKKK...",
         "KKKKKKKKKK",
         "KGGGGGGGGK",
         ".KWGWGWGK.",
         ".KWGWGWGK.",
         ".KWGWGWGK.",
         ".KWGWGWGK.",
         ".KWGWGWGK.",
         "..KKKKKK.."]
PC = ["KKKKKKKKKKKKKK",
      "KGGGGGGGGGGGGK",
      "KGTTTTTTTTTTGK",
      "KGTTTTTTTTTTGK",
      "KGTTTTTTTTTTGK",
      "KGTTTTTTTTTTGK",
      "KGGGGGGGGGGGGK",
      "KKKKKKKKKKKKKK",
      ".....KGGK.....",
      "...KKKKKKKK...",
      "...KGGGGGGK...",
      "...KKKKKKKK..."]
CURSOR = ["K.......",
          "KK......",
          "KWK.....",
          "KWWK....",
          "KWWWK...",
          "KWWWWK..",
          "KWWWWWK.",
          "KWWWWWWK",
          "KWWWWKKK",
          "KWKWWK..",
          "KK.KWWK.",
          "K..KWWK.",
          "....KK.."]
WARN = ["......K......",
        ".....KYK.....",
        ".....KYK.....",
        "....KYYYK....",
        "....KYKYK....",
        "...KYYKYYK...",
        "...KYYKYYK...",
        "..KYYYYYYYK..",
        "..KYYYKYYYK..",
        ".KYYYYYYYYYK.",
        "KKKKKKKKKKKKK"]
# popup messages live in STRINGS["popups"] up top now (they got translated)


def bevel(d, x0, y0, x1, y1, T, raised=True, fill=None):
    """the 3d box. raised: white top-left, black bottom-right + a dark grey inner shadow.
    sunken is the same thing flipped. this one function is like 80% of the look lol"""
    if fill is not None:
        d.rectangle([x0, y0, x1, y1], fill=fill)
    if raised:
        d.line([(x0, y1), (x0, y0), (x1, y0)], fill=T.win_light)
        d.line([(x0, y1), (x1, y1), (x1, y0)], fill=T.win_black)
        d.line([(x0 + 1, y1 - 1), (x1 - 1, y1 - 1), (x1 - 1, y0 + 1)], fill=T.win_dark)
    else:
        d.line([(x0, y1), (x0, y0), (x1, y0)], fill=T.win_dark)
        d.line([(x0, y1), (x1, y1), (x1, y0)], fill=T.win_light)
        d.line([(x0 + 1, y1 - 1), (x0 + 1, y0 + 1), (x1 - 1, y0 + 1)], fill=T.win_black)


def title_buttons(d, x1, y, T):
    """_ [] X, right aligned at x1. 9x8 each, lil gap before the X like the real deal"""
    for i, kind in enumerate(("x", "max", "min")):
        bx = x1 - 9 - i * 9 - (2 if i else 0)
        bevel(d, bx, y, bx + 8, y + 7, T, fill=T.win_gray)
        if kind == "x":
            d.line([(bx + 2, y + 2), (bx + 5, y + 5)], fill=T.win_black)
            d.line([(bx + 5, y + 2), (bx + 2, y + 5)], fill=T.win_black)
        elif kind == "max":
            d.rectangle([bx + 2, y + 2, bx + 6, y + 5], outline=T.win_black)
            d.line([(bx + 2, y + 3), (bx + 6, y + 3)], fill=T.win_black)
        else:
            d.line([(bx + 2, y + 5), (bx + 5, y + 5)], fill=T.win_black)


def window(d, x0, y0, x1, y1, title, T, active=True):
    bevel(d, x0, y0, x1, y1, T, fill=T.win_gray)
    d.rectangle([x0 + 3, y0 + 3, x1 - 3, y0 + 12], fill=T.title_bar if active else T.win_dark)
    text(d, x0 + 5, y0 + 5, title, T.title_txt if active else T.win_gray)
    title_buttons(d, x1 - 4, y0 + 4, T)


class Desktop:
    """win95-ish bg. teal desktop w/ icons, the monster living inside a MONSTER.EXE window,
    taskbar w/ the clock in the tray, a mouse cursor wandering around (the eyes FOLLOW it,
    fav detail of the whole project ngl), random error popups the cursor goes and clicks
    away, and a starfield screensaver once the monster dozes off.
    no logos, no real branding, just the vibe"""

    # WIN = (44, 16, 196, 128)   # brows were poking thru the menu bar, gave it more headroom
    WIN = (44, 4, 196, 130)
    # sprite x, y, label, kind
    # labels come from tr("icons") now, the 3rd field is just so i remember which is which
    ICONS = ((11, 16, "movies", "folder"), (11, 46, "shows", "folder"), (11, 76, "anime", "folder"),
             (13, 106, "trash", "trash"), (213, 16, "pc", "pc"))
    DIALOG = (100, 92, 236, 140)
    TASKBAR = 147

    def __init__(self):
        self.theme = None
        self.idle_for = 0.0           # render() keeps this updated
        self.saver = False
        self.cursor = [150.0, 140.0]
        self.target = [150.0, 140.0]
        self.next_move = 0.0
        self.aim_icon = None
        self.selected = (None, 0.0)   # (icon idx, until)
        self.dialog = None            # {"msg":..., "arrived": t or None, "click": t or None}
        self.next_dialog = 25.0
        self.mood = None              # (expression, secs) for the monster, render() hands it over
        self.stars = []
        self.zzz = [40.0, 40.0, 18.0, 12.0]   # x, y, vx, vy for the bouncing text
        self.zzz_col = 0
        self.last_t = None
        self.dt = 0.0

    # ---- bits
    def _icon(self, d, i, T, t):
        x, y, _, kind = self.ICONS[i]
        label = tr("icons")[i]
        sel = self.selected[0] == i and t < self.selected[1]
        if kind == "folder":
            pix(d, x, y, FOLDER, {"D": T.folder_dk, "Y": T.select if sel else T.folder})
            cx = x + 7
        elif kind == "trash":
            pix(d, x, y, TRASH, {"K": T.win_black, "G": T.select if sel else T.win_gray, "W": T.win_light})
            cx = x + 5
        else:
            pix(d, x, y, PC, {"K": T.win_black, "G": T.win_gray, "T": T.select if sel else T.screen})
            cx = x + 7
        lx, ly = cx - 3 * len(label), y + 14
        if sel:   # selected label = navy box behind it
            d.rectangle([lx - 1, ly - 1, lx + 6 * len(label) - 1, ly + 7], fill=T.select)
        text(d, lx, ly, label, T.icon_txt)

    def _taskbar(self, d, t, T, task):
        y = self.TASKBAR
        d.rectangle([0, y, W - 1, H - 1], fill=T.win_gray)
        d.line([(0, y), (W - 1, y)], fill=T.win_light)
        bevel(d, 2, y + 2, 47, H - 2, T)
        text(d, 4, y + 3, tr("start"), T.win_black)
        text(d, 5, y + 3, tr("start"), T.win_black)   # fake bold, 2 passes 1px apart
        tw = 6 * len(task) + 6
        bevel(d, 51, y + 2, 51 + tw, H - 2, T, raised=False, fill=T.win_light)   # pressed = active task
        text(d, 54, y + 3, task, T.win_black)
        bevel(d, 199, y + 2, W - 3, H - 2, T, raised=False)   # tray
        text(d, 205, y + 3, time.strftime("%H:%M"), T.win_black)

    def _move_cursor(self, t, dt):
        if self.dialog:
            x0, y0, x1, y1 = self.DIALOG
            self.target = [(x0 + x1) / 2 - 2, y1 - 8]    # the OK button
        elif t > self.next_move:
            if random.random() < 0.4:
                i = random.randrange(len(self.ICONS))
                ix, iy, _, _ = self.ICONS[i]
                self.target, self.aim_icon = [ix + 6, iy + 5], i
            else:
                self.target, self.aim_icon = [random.uniform(20, 225), random.uniform(20, 140)], None
            self.next_move = t + random.uniform(2, 5)
        k = min(1.0, dt * 4)
        for j in (0, 1):
            self.cursor[j] += (self.target[j] - self.cursor[j]) * k
        arrived = abs(self.cursor[0] - self.target[0]) < 2 and abs(self.cursor[1] - self.target[1]) < 2
        if arrived and self.aim_icon is not None:   # "double click" the icon
            self.selected = (self.aim_icon, t + 2.5)
            self.aim_icon = None
        return arrived

    def _starfield(self, img, t, dt, T):
        d = ImageDraw.Draw(img)
        d.rectangle([0, 0, W - 1, H - 1], fill=T.win_black)
        if not self.stars:
            self.stars = [[random.uniform(-1, 1), random.uniform(-1, 1), random.uniform(0.05, 1)] for _ in range(70)]
        for s in self.stars:
            s[2] -= dt * 0.35
            sx, sy = 120 + s[0] / max(s[2], 0.01) * 50, 80 + s[1] / max(s[2], 0.01) * 50
            if s[2] < 0.05 or not (0 <= sx < W and 0 <= sy < H):
                s[:] = [random.uniform(-1, 1), random.uniform(-1, 1), 1.0]
                continue
            col = T.win_dark if s[2] > 0.6 else (T.win_gray if s[2] > 0.3 else T.win_light)
            sz = 1 if s[2] < 0.25 else 0
            d.rectangle([sx, sy, sx + sz, sy + sz], fill=col)
        # bouncing ZZZ, changes color every time it hits a wall. u know the one
        z = self.zzz
        z[0] += z[2] * dt
        z[1] += z[3] * dt
        if not 0 < z[0] < W - 36:
            z[2] = -z[2]
            z[0] = min(max(z[0], 1), W - 37)
            self.zzz_col += 1
        if not 0 < z[1] < H - 15:
            z[3] = -z[3]
            z[1] = min(max(z[1], 1), H - 16)
            self.zzz_col += 1
        cols = [T.folder, T.screen, T.cheek, T.desktop, T.win_light]
        text(d, int(z[0]), int(z[1]), "ZZZ", cols[self.zzz_col % len(cols)], 2)

    # ---- main hooks
    def draw_bg(self, img, t, T, playing=False, emit=None):
        dt = 0.0 if self.last_t is None else min(0.5, max(0.0, t - self.last_t))
        self.last_t, self.dt = t, dt
        self.saver = not playing and self.idle_for > SLEEP_AFTER
        if self.saver:
            self._starfield(img, t, dt, T)
            return
        d = ImageDraw.Draw(img)
        if playing:
            # playing screen is a maximized PLAYER.EXE. draw_playing does the content,
            # we just add the taskbar (bevels/buttons go on top later in draw_fg)
            self._taskbar(d, t, T, "PLAYER.EXE")
            return

        d.rectangle([0, 0, W - 1, self.TASKBAR - 1], fill=T.desktop)
        for i in range(len(self.ICONS)):
            self._icon(d, i, T, t)

        x0, y0, x1, y1 = self.WIN
        window(d, x0, y0, x1, y1, tr("monster"), T, active=self.dialog is None)
        mx = x0 + 6
        for item in tr("menu"):   # menu bar, does nothing obviously
            text(d, mx, y0 + 15, item, T.win_black)
            d.line([(mx, y0 + 22), (mx + 4, y0 + 22)], fill=T.win_black)   # the alt-key underline
            mx += 6 * len(item) + 8
        bevel(d, x0 + 4, y1 - 13, x1 - 4, y1 - 4, T, raised=False)   # status bar
        self._taskbar(d, t, T, tr("monster"))

        # popups
        if self.dialog is None and t > self.next_dialog:
            self.dialog = {"msg": random.choice(tr("popups")), "arrived": None, "click": None}
            self.mood = (random.choice(("surprised", "surprised", "eyeroll", "angry")), 1.6)
        arrived = self._move_cursor(t, dt)
        if self.dialog:
            dg = self.dialog
            if arrived and dg["arrived"] is None:
                dg["arrived"] = t
            if dg["arrived"] is not None and dg["click"] is None and t - dg["arrived"] > 0.6:
                dg["click"] = t
            if dg["click"] is not None and t - dg["click"] > 0.3:
                self.dialog = None
                self.next_dialog = t + random.uniform(40, 80)
                self.mood = ("happy", 2.0)   # phew

    def draw_fg(self, d, t, T, playing=False):
        if self.saver:
            return
        if playing:
            # sunken frames around the poster + progress bar, window buttons up top
            px, py = POSTER_XY
            bevel(d, px - 2, py - 2, px + 61, py + 91, T, raised=False)
            bevel(d, 4, PROG_Y - 2, W - 5, PROG_Y + 9, T, raised=False)
            title_buttons(d, W - 3, 2, T)
            return
        if self.dialog:
            x0, y0, x1, y1 = self.DIALOG
            dg = self.dialog
            window(d, x0, y0, x1, y1, tr("error"), T)
            pix(d, x0 + 6, y0 + 17, WARN, {"K": T.win_black, "Y": T.warn_tri})
            for k, line in enumerate(dg["msg"]):
                text(d, x0 + 24, y0 + 16 + k * 9, line, T.win_black)
            bx = (x0 + x1) // 2 - 14
            pressed = dg["click"] is not None
            bevel(d, bx, y1 - 13, bx + 26, y1 - 4, T, raised=not pressed, fill=T.win_gray)
            text(d, bx + 8 + pressed, y1 - 12 + pressed, "OK", T.win_black)
        cx, cy = int(self.cursor[0]), int(self.cursor[1])
        pix(d, cx, cy, CURSOR, {"K": T.win_black, "W": T.win_light})


# ------------------------------------------------------- screen: playing ----
PROG_Y, PROG_BLOCKS = 122, 38


def prog_head_x(now):
    """where the lil progress bar rider (fish/pumpkin) sits"""
    frac = (now["pos"] / now["dur"]) if now["dur"] else 0
    return min(max(7 + int(frac * PROG_BLOCKS) * 6 - 6, 6), W - 19)


def draw_playing(d, img, now, t, T):
    # top bar
    # some themes grey out the title bar when paused (win95 "inactive window" thing)
    bar = getattr(T, "bar_paused", T.bar) if now["paused"] else T.bar
    d.rectangle([0, 0, W - 1, 11], fill=bar)
    label = tr("paused") if now["paused"] else T.label
    if not (now["paused"] and int(t * 2) % 2):
        text(d, 4, 2, label, T.paused if now["paused"] else T.accent)
    if not now["paused"]:   # lil fake equalizer
        ex = 8 + 6 * len(label)
        for i in range(4):
            h = 2 + int(3 * (1 + math.sin(t * 7 + i * 1.7)))
            d.rectangle([ex + i * 4, 9 - h, ex + 2 + i * 4, 9], fill=T.eq)
    if not T.raw.get("clock_in_tray"):
        text(d, W - 34, 2, time.strftime("%H:%M"), T.clock)

    # poster w/ frame
    px, py = POSTER_XY
    d.rectangle([px - 2, py - 2, px + 61, py + 91], fill=T.frame)
    d.rectangle([px - 1, py - 1, px + 60, py + 90], fill=T.frame_in)
    if now["poster"] is None:
        text(d, px + 21, py + 41, "???", T.dim)
    elif PIXEL_POSTER:
        img.paste(poster_for(now["id"], now["poster"], T), (px, py))
    # else: the full res poster gets pasted in render(), AFTER the 2x upscale

    # text column
    tx = 76
    d.rectangle([tx, 18, tx + 6 * len(now["kind"]) + 2, 27], fill=T.kind_bg)
    text(d, tx + 2, 19, now["kind"], T.kind_fg)
    y = 33
    for line in wrap(now["title"], 26, 3):
        text(d, tx, y, line, T.title)
        y += 10
    y += 3
    for line in wrap(now["sub"], 26, 2):
        text(d, tx, y, line, T.sub)
        y += 10
    y = max(y + 3, 92)
    for line in wrap(now["who"], 26, 1):
        text(d, tx, y, line, T.dim)
    if now["others"]:
        text(d, tx, y + 10, f"+{now['others']} {tr('playing')}", T.warn)

    # progress bar, chunky blocks
    by, nblocks = PROG_Y, PROG_BLOCKS
    frac = (now["pos"] / now["dur"]) if now["dur"] else 0
    filled = frac * nblocks
    d.rectangle([4, by - 2, W - 5, by + 9], fill=T.prog_border)
    d.rectangle([5, by - 1, W - 6, by + 8], fill=T.prog_bg)
    for i in range(nblocks):
        x = 7 + i * 6
        if i < int(filled):
            col = T.prog_b if i % 2 else T.prog_a
        elif i == int(filled) and not now["paused"] and int(t * 3) % 2 and not T.prog_sprite:
            col = T.prog_head
        else:
            col = T.prog_off
        d.rectangle([x, by + 1, x + 4, by + 6], fill=col)
    hx_ = prog_head_x(now)
    if T.prog_sprite == "fish":
        # lil fish swimming at the head of the bar. stops wagging when paused, it's napping
        wag = 0 if now["paused"] else int(t * 5) % 2
        draw_fish(d, hx_, by + 1, True, wag, T.prog_head, T.prog_a, T.fish_eye)
    elif T.prog_sprite == "pumpkin":
        # pumpkin rolling along. bobs a lil while playing, sits still when paused
        bob = 0 if now["paused"] else int(abs(math.sin(t * 4)) * 1.5)
        draw_pumpkin(d, hx_ + 2, by - 1 - bob, T)
    elif T.prog_sprite == "gift":
        # copy paste of the pumpkin thing basically. hops a bit more, it's excited
        bob = 0 if now["paused"] else int(abs(math.sin(t * 5)) * 2)
        draw_gift(d, hx_ + 3, by - bob, T)
    elif T.prog_sprite == "car":
        # hover car cruising the bar. parked (no hover wobble) when paused
        bob = 0 if now["paused"] else int(math.sin(t * 6) > 0.3)
        draw_car(d, hx_, by + 1 - bob, T)

    text(d, 6, by + 14, fmt_time(now["pos"]), T.time)
    rest = "-" + fmt_time(now["dur"] - now["pos"]) if now["dur"] else "--:--"
    text(d, W - 6 - 6 * len(rest), by + 14, rest, T.time)
    pct = f"{int(frac * 100)}%"
    text(d, (W - 6 * len(pct)) // 2, by + 14, pct, T.pct)

    # play / pause icon
    if now["paused"]:
        d.rectangle([W - 20, 20, W - 17, 29], fill=T.paused)
        d.rectangle([W - 14, 20, W - 11, 29], fill=T.paused)
    else:
        for i in range(5):
            d.line([W - 20 + i, 20 + i, W - 20 + i, 29 - i], fill=T.play)


# -------------------------------------------------------- screen: monster ----
# tiny sprites for the cartoon props. 1 = pixel on
HEART = ["011000110", "111101111", "111111111", "111111111",
         "011111110", "001111100", "000111000", "000010000"]
SPARK = ["00100", "00100", "11011", "00100", "00100"]   # lil twinkle
DROP = ["010", "010", "111", "111", "010"]               # tear / sweat. close enough


def sprite(d, x, y, rows, col, s=1):
    for r, row in enumerate(rows):
        for c, ch in enumerate(row):
            if ch == "1":
                d.rectangle([x + c * s, y + r * s, x + (c + 1) * s - 1, y + (r + 1) * s - 1], fill=col)


# numeric stuff that gets eased frame to frame. anything can be a (left, right) tuple
FACE_DEFAULTS = {
    "lid_top": 0.0,    # 0 open .. 1 shut
    "lid_bot": 0.0,    # bottom lid pushing up -> smiley eyes
    "tilt": 0.0,       # +1 angry (inner corner cut), -1 sad (outer corner droops)
    "pupil": 1.0,      # pupil size
    "grow": 0.0,       # eye size bump, surprised
    "brow": 0.0,       # brows on/off (>0.5 = on)
    "brow_y": 0.0,     # - up, + down
    "brow_tilt": 0.0,  # same idea as tilt
}

# the whole cast. weight = how often it shows up randomly (0 = only via events)
#   kind: open | heart | arc_up (^ ^) | arc_down (u u) | line
#   look: wander | center | down | up | scan | spin | roll | cross
EXPRS = {
    "neutral":    dict(props=("blush",)),
    "happy":      dict(lid_bot=0.38, brow=1, brow_y=-5, props=("blush_big",), bob="hop",
                       dur=(3, 5), weight=3),
    "joy":        dict(kind="arc_up", brow=1, brow_y=-7, props=("blush_big", "sparkle"), bob="hop",
                       blink=False, dur=(2, 3), weight=2),
    "angry":      dict(tilt=0.9, lid_top=0.08, pupil=0.75, brow=1, brow_y=5, brow_tilt=1,
                       look="center", props=("vein",), bob="shake", dur=(2.5, 4), weight=1.5),
    "sad":        dict(tilt=-0.85, lid_top=0.12, brow=1, brow_y=-1, brow_tilt=-1, look="down",
                       props=("tears",), bob="sink", dur=(3, 5), weight=1),
    "sleepy":     dict(lid_top=0.58, look="down", props=("z",), bob="slow", blink_len=0.8,
                       dur=(4, 7), weight=1.5),
    "relaxed":    dict(kind="arc_down", props=("blush",), bob="slow", blink=False,
                       dur=(3, 5), weight=2),
    "surprised":  dict(grow=5, pupil=0.55, brow=1, brow_y=-10, look="center", props=("bang",),
                       bob="jump", blink=False, dur=(1.3, 1.8), weight=1.5),
    "suspicious": dict(lid_top=(0.0, 0.48), lid_bot=(0.0, 0.3), brow=1, brow_y=(-4, 4),
                       brow_tilt=(0, 0.8), look="scan", dur=(3, 4), weight=2),
    "love":       dict(kind="heart", props=("hearts", "blush_big"), bob="hop_slow",
                       dur=(3, 5), weight=1.5),
    "dizzy":      dict(pupil=0.8, look="spin", props=("stars",), bob="wobble", blink=False,
                       dur=(3, 4), weight=0.7),
    "wink":       dict(kind=("open", "arc_up"), lid_bot=(0.3, 0.0), brow=1, brow_y=(-5, -2),
                       props=("blush_big", "sparkle1"), blink=False, dur=(1.2, 1.8), weight=2),
    "confused":   dict(brow=1, brow_y=(-8, 2), brow_tilt=(-0.6, 0.6), look="up",
                       props=("huh", "sweat"), dur=(2.5, 4), weight=1.5),
    "eyeroll":    dict(lid_top=0.15, brow=1, brow_y=-2, look="roll", blink=False,
                       dur=(1.8, 2.2), weight=1.5),
    "crosseyed":  dict(pupil=0.85, look="cross", props=("blush",), blink=False,
                       dur=(1.5, 2.5), weight=1),
    "asleep":     dict(lid_top=1.0, look="down", props=("zzz",), bob="slow", blink=False),
}


class Monster:
    """the lil guy. random cartoon expressions every few secs + reacts to stuff:
       movie ended -> sad, jellyfin down -> dizzy, boot -> surprised, idle long -> sleepy -> asleep"""

    EYES = ((82, -1), (158, 1))   # (center x, side). side -1 = left

    def __init__(self):
        self.born = time.time()
        self.cur = [dict(FACE_DEFAULTS), dict(FACE_DEFAULTS)]
        self.look = [[0.0, 0.0], [0.0, 0.0]]
        self.target = [0.0, 0.0]
        self.next_look = 0
        self.next_blink = time.time() + 3
        self.blink_t = -1
        self.name = "neutral"
        self.expr_t0 = time.time()
        self.rand = None
        self.rand_until = 0
        self.next_rand = time.time() + random.uniform(4, 8)
        self.last_rand = None
        self.follow = None   # (x, y) to look at instead of wandering. win95 cursor sets this
        self.event = None    # (expression, until) pushed from outside, e.g. a popup showing up
        # self.forced = os.environ.get("NP_FACE")   # handy for testing one face at a time

    # ---- what face are we making
    def _mood(self, now, idle_for, since_stop, err):
        if err:
            return "dizzy"
        if since_stop < 5:
            return "sad"                        # aww the movie's over
        if idle_for > SLEEP_AFTER:
            return "asleep"
        if idle_for > SLEEP_AFTER - 30:
            return "sleepy"
        if self.event and now < self.event[1]:
            return self.event[0]
        if now - self.born < 2.5:
            return "surprised"                  # just booted, whoa
        if self.rand and now >= self.rand_until:
            self.rand = None
            self.next_rand = now + random.uniform(3, 8)
        if not self.rand and now >= self.next_rand:
            hour = time.localtime().tm_hour
            pool, weights = [], []
            for n, e in EXPRS.items():
                w = e.get("weight", 0)
                if not w or n == self.last_rand:
                    continue
                if n == "sleepy" and (hour >= 23 or hour < 6):
                    w *= 4                      # past bedtime
                pool.append(n)
                weights.append(w)
            self.rand = self.last_rand = random.choices(pool, weights)[0]
            self.rand_until = now + random.uniform(*EXPRS[self.rand].get("dur", (2, 4)))
        return self.rand or "neutral"

    @staticmethod
    def _per_eye(v, i):
        return v[i] if isinstance(v, tuple) else v

    # ---- pupils
    def _pupils(self, now, spec):
        mode = spec.get("look", "wander")
        if mode == "wander" and self.follow:
            # stare at whatever we're told to (the cursor). each eye aims on its own = lil parallax
            fx, fy = self.follow
            tgt = [(max(-11, min(11, (fx - ex) / 6)), max(-9, min(9, (fy - 72) / 5)))
                   for ex, _ in self.EYES]
        elif mode == "wander":
            if now > self.next_look:
                self.target = [0.0, 0.0] if random.random() < 0.3 else \
                    [random.uniform(-11, 11), random.uniform(-9, 9)]
                self.next_look = now + random.uniform(0.8, 3.5)
            tgt = [self.target, self.target]
        elif mode == "center":
            tgt = [(0, 0), (0, 0)]
        elif mode == "down":
            sx = 3 * math.sin(now * 0.7)
            tgt = [(sx, 9), (sx, 9)]
        elif mode == "up":
            tgt = [(3, -10), (3, -10)]
        elif mode == "scan":          # side to side, very sus
            sx = 10 * math.sin(now * 1.8)
            tgt = [(sx, 2), (sx, 2)]
        elif mode == "spin":          # opposite directions = extra dizzy
            a = now * 7
            tgt = [(7 * math.cos(a), 7 * math.sin(a)), (7 * math.cos(-a), 7 * math.sin(-a))]
        elif mode == "roll":          # ugh. left -> up -> right
            p = min(1.0, (now - self.expr_t0) / 1.6)
            a = math.pi + p * math.pi
            tgt = [(11 * math.cos(a), 10 * math.sin(a))] * 2
        elif mode == "cross":
            tgt = [(9, 3), (-9, 3)]
        else:
            tgt = [(0, 0), (0, 0)]
        k = 0.35 if mode == "wander" else 0.55
        for i in (0, 1):
            for j in (0, 1):
                self.look[i][j] += (tgt[i][j] - self.look[i][j]) * k

    # ---- body motion
    def _bob(self, t, mode):
        if mode == "hop":
            return 0, -int(abs(math.sin(t * 5)) * 4)
        if mode == "hop_slow":
            return 0, -int(abs(math.sin(t * 3)) * 3)
        if mode == "shake":
            return random.randint(-2, 2), random.randint(-1, 1)
        if mode == "sink":
            return 0, 3 + int(math.sin(t * 0.8))
        if mode == "slow":
            return 0, int(2 * math.sin(t * 0.6))
        if mode == "jump":
            return 0, -4
        if mode == "wobble":
            return int(3 * math.sin(t * 4)), int(2 * math.sin(t * 2.7))
        return 0, int(2 * math.sin(t * 1.3))

    # ---- drawing
    def _closed(self, d, cx, cy, kind, T):
        if kind == "line":
            d.rectangle([cx - 18, cy + 4, cx + 17, cy + 6], fill=T.closed)
        elif kind == "arc_up":       # ^  happy/laughing
            d.arc([cx - 17, cy - 6, cx + 16, cy + 22], 200, 340, fill=T.mbrow, width=4)
        else:                        # u  content/relaxed
            d.arc([cx - 17, cy - 14, cx + 16, cy + 10], 20, 160, fill=T.mbrow, width=4)

    def _eye(self, d, cx, cy, side, k, kind, look, lid, T):
        if kind in ("arc_up", "arc_down", "line"):
            self._closed(d, cx, cy, kind, T)
            return
        g = int(round(k["grow"]))
        w, h = 44 + 2 * g, 52 + 2 * g
        x0, y0 = cx - w // 2, cy - h // 2
        # first try was d.ellipse, way too smooth, didn't look pixel-y at all
        # d.ellipse([x0, y0, x0 + w, y0 + h], fill=T.eye)
        blob(d, x0, y0, w, h, T.eye_line, grow=2)
        blob(d, x0, y0, w, h, T.eye)

        px, py = cx + int(look[0]), cy + int(look[1])
        if kind == "heart":
            sprite(d, px - 9, py - 8, HEART, T.heart, 2)
            d.rectangle([px - 5, py - 5, px - 4, py - 4], fill=T.shine)
        else:
            s = k["pupil"]
            pw, ph = max(4, int(16 * s)), max(6, int(20 * s))
            qx, qy = px - pw // 2, py - ph // 2
            d.rectangle([qx, qy, qx + pw - 1, qy + ph - 1], fill=T.pupil)
            d.rectangle([qx + 2, qy - 2, qx + pw - 3, qy + ph + 1], fill=T.pupil)
            sh = max(2, int(4 * s))
            d.rectangle([qx + 3, qy + 2, qx + 2 + sh, qy + 1 + sh], fill=T.shine)      # sparkle
            if s > 0.7:
                d.rectangle([qx + pw - 6, qy + ph - 7, qx + pw - 5, qy + ph - 6], fill=T.shine)

        # top lid. slanted when tilt != 0 (angry / sad)
        tl = k["tilt"]
        if lid > 0.01 or abs(tl) > 0.05:
            base = y0 - 3 + lid * (h + 6)
            in_y, out_y = base + tl * 12, base - tl * 12
            xl, xr = x0 - 3, x0 + w + 2
            yl, yr = (out_y, in_y) if side < 0 else (in_y, out_y)
            # CLEAR not T.bg: eyes live on their own rgba layer now, so the lid punches a hole
            # and whatever's behind (fish, road, sun) shows through instead of a flat box
            # d.polygon([(xl, y0 - 20), (xr, y0 - 20), (xr, yr), (xl, yl)], fill=T.bg)
            d.polygon([(xl, y0 - 20), (xr, y0 - 20), (xr, yr), (xl, yl)], fill=CLEAR)
            if lid >= 0.98:
                d.rectangle([x0 + 4, cy + 4, x0 + w - 5, cy + 6], fill=T.closed)
            else:
                # lid edge line, clipped so it only shows on the white part
                xa, xb = x0 + 2, x0 + w - 3
                ya = yl + (yr - yl) * (xa - xl) / (xr - xl)
                yb = yl + (yr - yl) * (xb - xl) / (xr - xl)
                top = y0 + 2
                if max(ya, yb) > top:
                    if ya < top:
                        xa += (xb - xa) * (top - ya) / (yb - ya)
                        ya = top
                    if yb < top:
                        xb += (xa - xb) * (top - yb) / (ya - yb)
                        yb = top
                    d.line([(xa, ya), (xb, yb)], fill=T.lid_line, width=2)

        # bottom lid. elliptical cut from below = the smiley ^_^ squint
        lb = k["lid_bot"]
        if lb > 0.02 and lid < 0.98:
            rx, ry = (w + 20) / 2, h / 2
            ecy = y0 + h - lb * h + ry
            pts = []
            for x in range(x0 - 3, x0 + w + 4, 2):
                u = (x - cx) / rx
                pts.append((x, ecy - ry * math.sqrt(max(0.0, 1 - u * u))))
            pts += [(x0 + w + 3, y0 + h + 3), (x0 - 3, y0 + h + 3)]
            d.polygon(pts, fill=CLEAR)

    def _brow(self, d, cx, cy, side, k, T):
        if k["brow"] < 0.5:
            return
        g = k["grow"]
        by = cy - 26 - g - 10 + k["brow_y"]
        by = max(by, T.raw.get("brow_top", -99))   # win95: keep em under the menu bar
        bt = k["brow_tilt"]
        xo, xi = (cx - 15, cx + 13) if side < 0 else (cx + 15, cx - 13)
        d.line([(xo, by - bt * 6), (xi, by + bt * 6)], fill=T.mbrow, width=4)
        # tried fading brows in/out, but no alpha on an RGB draw so... nah
        # d.line([...], fill=(*T.mbrow, int(255 * k["brow"])), width=4)

    # ---- cartoon props
    def _props(self, d, t, props, bx, by, T):
        for p in props:
            if p in ("blush", "blush_big"):
                big = p == "blush_big"
                for x in (52, 181):
                    x += bx
                    if big:
                        d.rectangle([x - 3, 103 + by, x + 10, 108 + by], fill=T.cheek)
                    else:
                        d.rectangle([x, 104 + by, x + 7, 107 + by], fill=T.cheek)
            elif p == "tears":
                for ex, side in self.EYES:
                    x = ex + side * 12 + bx
                    d.rectangle([x, 98 + by, x + 1, 103 + by], fill=T.tear)   # the stream
                    for i in range(2):
                        ph = (t * 1.1 + i / 2) % 1
                        sprite(d, x - 2, 100 + by + int(ph * 26), DROP, T.tear, 2)
            elif p == "vein":            # the anime anger mark
                cx0, cy0 = 200 + bx, 26
                r = 7 if int(t * 3) % 2 else 5
                for sx in (-1, 1):
                    for sy in (-1, 1):
                        x1, y1 = cx0 + sx * 2, cy0 + sy * 2
                        d.line([(x1, cy0 + sy * r), (x1, y1), (cx0 + sx * r, y1)], fill=T.vein, width=2)
            elif p == "bang":
                text(d, 113, 4 + int(2 * abs(math.sin(t * 6))), "!", T.pop, 3)
            elif p == "huh":
                text(d, 194, 12 + int(2 * math.sin(t * 3)), "?", T.pop, 2)
            elif p == "sweat":
                sprite(d, 196 + bx, 40 + int((t * 0.6 % 1) * 6), DROP, T.tear, 2)
            elif p == "hearts":
                for i in range(3):
                    ph = (t * 0.5 + i / 3) % 1
                    if ph < 0.85:        # "fade out" = just stop drawing it lol
                        x = 116 + int(10 * math.sin(t * 2 + i * 2))
                        sprite(d, x, 100 - int(ph * 80), HEART, T.heart)
            elif p == "sparkle":
                for i, (x, y) in enumerate(((36, 36), (198, 30), (120, 22), (206, 92), (28, 90))):
                    if (int(t * 4) + i) % 3:
                        sprite(d, x + bx, y + by, SPARK, T.pop)
            elif p == "sparkle1":
                if int(t * 4) % 3:
                    sprite(d, 186 + bx, 30 + by, SPARK, T.pop, 2)
            elif p == "stars":           # orbiting cartoon stars, bonk
                for i in range(3):
                    a = t * 3 + i * 2.09
                    sprite(d, 118 + int(44 * math.cos(a)), 28 + int(7 * math.sin(a)), SPARK, T.pop, 2)
            elif p == "z":
                ph = (t * 0.4) % 1
                text(d, 186 + int(ph * 14), 40 - int(ph * 16), "Z", T.zzz)
            elif p == "zzz":
                for i in range(3):
                    ph = (t * 0.5 + i / 3) % 1
                    text(d, 190 + int(ph * 30) + i * 4, 40 - int(ph * 30), "Z",
                         T.zzz if ph < 0.7 else T.dim, 1 + (i == 2))

    def draw(self, d, t, idle_for, since_stop, err, T):
        now = time.time()
        name = self._mood(now, idle_for, since_stop, err)
        # if self.forced: name = self.forced
        if name != self.name:
            self.name, self.expr_t0 = name, now
        spec = EXPRS[name]

        # ease the numbers toward the target face
        for i in (0, 1):
            for key, dflt in FACE_DEFAULTS.items():
                tgt = self._per_eye(spec.get(key, dflt), i)
                if name == "asleep" and key == "lid_top" and int(t / 6) % 5 == 0:
                    tgt = 0.55               # peeks every ~30s
                self.cur[i][key] += (tgt - self.cur[i][key]) * (0.6 if key == "brow" else 0.45)

        self._pupils(now, spec)

        # blink (most faces)
        blink = 0.0
        if spec.get("blink", True):
            if now > self.next_blink and self.blink_t < 0:
                self.blink_t = now
            if self.blink_t >= 0:
                p = (now - self.blink_t) / spec.get("blink_len", 0.35)
                if p >= 1:
                    self.blink_t = -1
                    # every now n then a quick double blink
                    self.next_blink = now + random.uniform(2.5, 6) * (0.15 if random.random() < 0.2 else 1)
                else:
                    blink = 1 - abs(p * 2 - 1)

        bx, by = self._bob(t, spec.get("bob"))
        for i, (ex, side) in enumerate(self.EYES):
            k = self.cur[i]
            kind = self._per_eye(spec.get("kind", "open"), i)
            lid = max(k["lid_top"], blink)
            self._eye(d, ex + bx, 72 + by, side, k, kind, self.look[i], lid, T)
            self._brow(d, ex + bx, 72 + by, side, k, T)
        self._props(d, t, spec.get("props", ()), bx, by, T)

        if not err:
            asleep = name == "asleep"
            msg = "ZZZ..." if asleep else T.idle
            dots = not asleep and T.raw.get("idle_dots", True)
            s = msg + ("." * (int(t * 2) % 4) if dots else "")
            col = T.dim if asleep else T.idle_txt
            cols = None if asleep else T.raw.get("idle_colors")
            if T.fx == "road":
                text(d, 4, 2, s, col)   # bottom half is all road, so up top it goes
            elif T.raw.get("idle_xy"):
                text(d, *T.raw["idle_xy"], s, col)   # theme says where (win95 status bar)
            elif cols:
                # letter by letter, colors marching along. dark drop shadow so it reads on snow
                x = (W - 6 * len(msg)) // 2
                cols = [hx(c) for c in cols]
                shadow = getattr(T, "shadow_txt", T.bg)
                for i, ch in enumerate(s):
                    text(d, x + 1, 140, ch, shadow)
                    text(d, x, 139, ch, cols[(i + int(t * 2)) % len(cols)])
                    x += 6
            else:
                text(d, (W - 6 * len(msg)) // 2, 139, s, col)
        if not T.raw.get("clock_in_tray"):
            text(d, W - 34, 2, time.strftime("%H:%M"), T.dim)


# ---------------------------------------------------------- easter egg ----
class Fire:
    """good ol' cellular fire: bottom row = max heat, each cell copies the one below it
    w/ a lil random sideways drift + decay. tiny grid, gets 3x'd"""

    def __init__(self, fw=80, fh=15, levels=8):
        self.fw, self.fh, self.lv = fw, fh, levels
        self.f = np.zeros((fh, fw), np.int16)
        self.xs = np.arange(fw)

    def step(self, base):
        f = self.f
        f[-1] = base - (np.random.random(self.fw) < 0.15)   # flicker the coals a bit
        for y in range(1, self.fh):
            r = np.random.randint(0, 3, self.fw)
            dst = np.clip(self.xs - r + 1, 0, self.fw - 1)
            # classic version is `f[y] - (r & 1)` but w/ only 15 rows that never cools
            # down enough, whole thing looked like tv static lol. decay harder
            # f[y - 1, dst] = np.maximum(f[y] - (r & 1), 0)
            cool = (np.random.random(self.fw) < 0.62) + (np.random.random(self.fw) < 0.12)
            f[y - 1, dst] = np.maximum(f[y] - cool, 0)

    def draw(self, img, colors, cell=3):
        lv = np.clip(self.f, 0, len(colors) - 1)
        rgb = np.array(colors, np.uint8)[lv]
        size = (self.fw * cell, self.fh * cell)
        fi = Image.fromarray(rgb, "RGB").resize(size, Image.NEAREST)
        mask = Image.fromarray(((lv > 0) * 255).astype(np.uint8), "L").resize(size, Image.NEAREST)
        img.paste(fi, (0, H - size[1]), mask)


class Demon:
    """`display doom` easter egg. grumpy lil fire demon, reacts to stuff:
       - movie just ended / jellyfin down -> RAGE (shakes, red eyes, fire goes nuts)
       - random evil grins
       - idle for ages -> bored, half-lidded, fire dies down"""

    def __init__(self):
        self.look = [0.0, 0.0]
        self.target = [0.0, 0.0]
        self.next_look = 0
        self.next_blink = time.time() + 2
        self.blink_t = -1
        self.next_grin = time.time() + 5
        self.grin_until = 0
        self.fire = Fire()

    @staticmethod
    def _mirror(cx, side, x):
        return x if side < 0 else 2 * cx - x

    def _horn(self, d, cx, cy, side, T):
        # staircase horn growing up + outward from the outer corner of the eye
        x0, y0 = cx - 22, cy - 15
        steps = [(0, 12, 6, 1), (-3, 7, 12, 7), (-6, 2, 17, 13), (-8, -2, 21, 18), (-9, -6, 24, 22)]
        for xa, xb, ya, yb in steps:
            a, b = self._mirror(cx, side, x0 + xa), self._mirror(cx, side, x0 + xb)
            a, b = min(a, b), max(a, b)
            d.rectangle([a, y0 - ya, b, y0 - yb], fill=T.horn)
            # shade the inner edge
            sx = b - 1 if side < 0 else a
            d.rectangle([sx, y0 - ya, sx + 1, y0 - yb], fill=T.horn_dark)

    def _eye(self, d, cx, cy, side, lid, rage, T):
        w, h = 44, 30
        x0, y0 = cx - w // 2, cy - h // 2
        pulse = rage and int(time.time() * 8) % 2
        blob(d, x0, y0, w, h, T.cheek, grow=3)
        blob(d, x0, y0, w, h, T.rage_eye if pulse else T.glow, grow=1)
        blob(d, x0, y0, w, h, T.rage_eye if rage else T.eye)

        px, py = cx + int(self.look[0]), cy + int(self.look[1])
        iw = 8 if rage else 12        # pupils go tiny when it's mad
        d.rectangle([px - iw // 2, py - 10, px + iw // 2 - 1, py + 9], fill=T.glow)
        sw = 1 if rage else 2
        d.rectangle([px - sw, py - 12, px + sw - 1, py + 11], fill=T.pupil)

        # angry brow: chop a wedge off the inner top corner
        drop = 14 if rage else 10
        out_x, in_x = (x0 - 4, x0 + w + 3) if side < 0 else (x0 + w + 3, x0 - 4)
        d.polygon([(out_x, y0 - 4), (in_x, y0 - 4), (in_x, y0 + drop)], fill=T.bg)

        if lid > 0:
            lh = int((h + 8) * lid)
            d.rectangle([x0 - 4, y0 - 4, x0 + w + 3, y0 - 4 + lh], fill=T.bg)
        if lid >= 1:
            d.rectangle([x0 + 4, cy + 2, x0 + w - 5, cy + 3], fill=T.closed)
        d.line([(out_x, y0 - 5), (in_x, y0 + drop - 1)], fill=T.brow, width=3)

    def _mouth(self, d, cx, cy, grin, rage, T):
        if grin or rage:
            w, h = (64, 12) if grin else (48, 9)
            x0, y0 = cx - w // 2, cy - h // 2
            d.rectangle([x0 + 1, y0 - 1, x0 + w - 2, y0 + h + 1], fill=T.mouth_line)
            d.rectangle([x0 + 2, y0, x0 + w - 3, y0 + h], fill=T.mouth)
            for x in range(x0 + 4, x0 + w - 8, 8):        # top teeth
                d.polygon([(x, y0 + 1), (x + 6, y0 + 1), (x + 3, y0 + 5)], fill=T.fang)
            for x in range(x0 + 8, x0 + w - 10, 8):       # bottom teeth
                d.polygon([(x, y0 + h - 1), (x + 6, y0 + h - 1), (x + 3, y0 + h - 5)], fill=T.fang)
            for x in (x0 + 3, x0 + w - 9):                # the big boys
                d.polygon([(x, y0 + 1), (x + 6, y0 + 1), (x + 3, y0 + h - 1)], fill=T.fang)
        else:
            # resting frown w/ two fangs poking out
            d.rectangle([cx - 18, cy, cx + 17, cy + 1], fill=T.mouth_line)
            d.rectangle([cx - 22, cy + 2, cx - 19, cy + 3], fill=T.mouth_line)
            d.rectangle([cx + 18, cy + 2, cx + 21, cy + 3], fill=T.mouth_line)
            for x in (cx - 12, cx + 8):
                d.polygon([(x, cy + 2), (x + 4, cy + 2), (x + 2, cy + 6)], fill=T.fang)

    def draw(self, d, img, t, idle_for, since_stop, err, T):
        now = time.time()
        rage = since_stop < 6 or bool(err)
        bored = idle_for > SLEEP_AFTER and not rage

        if now > self.next_grin and not rage:
            self.grin_until = now + 1.6
            self.next_grin = now + random.uniform(7, 15)
        grin = now < self.grin_until

        if now > self.next_look:
            self.target = [0.0, 0.0] if random.random() < 0.3 else \
                [random.uniform(-11, 11), random.uniform(-2, 2)]
            self.next_look = now + random.uniform(0.6, 2.5) * (3 if bored else 1)
        k = 0.15 if bored else 0.4
        for i in (0, 1):
            self.look[i] += (self.target[i] - self.look[i]) * k

        lid = 0.0
        if now > self.next_blink and self.blink_t < 0:
            self.blink_t = now
        if self.blink_t >= 0:
            p = (now - self.blink_t) / 0.3
            if p >= 1:
                self.blink_t = -1
                self.next_blink = now + random.uniform(3, 7)
            else:
                lid = 1 - abs(p * 2 - 1)
        if rage:
            lid = 0.0                 # no blinking when ur this mad
        elif bored:
            lid = max(lid, 0.45)
        elif grin:
            lid = max(lid, 0.3)       # squinty evil grin

        # fire first so it sits behind everything
        base = len(T.fire) - 1 - (2 if bored else 0)
        for _ in range(3 if rage else 2):
            self.fire.step(base)
        self.fire.draw(img, T.fire)

        jx = random.randint(-2, 2) if rage else 0
        jy = random.randint(-1, 1) if rage else 0
        bob = int(2 * math.sin(t * 1.1))
        cy = 60 + bob + jy
        for cx, side in ((82, -1), (158, 1)):
            self._eye(d, cx + jx, cy, side, lid, rage, T)
            self._horn(d, cx + jx, cy, side, T)
        self._mouth(d, 120 + jx, 94 + bob + jy, grin, rage, T)

        if not err:
            msg = T.idle + "." * (int(t * 2) % 4)
            text(d, 4, 2, msg, T.idle_txt)
        text(d, W - 34, 2, time.strftime("%H:%M"), T.dim)


# ------------------------------------------------------------ framebuffer ----
def find_fb():
    if os.environ.get("FB_DEVICE"):
        return os.environ["FB_DEVICE"]
    base = "/sys/class/graphics"
    for name in sorted(os.listdir(base)) if os.path.isdir(base) else []:
        try:
            with open(f"{base}/{name}/name") as f:
                n = f.read().strip().lower()
            if any(k in n for k in ("ili9486", "fb_ili", "fbtft", "waveshare", "piscreen")):
                return f"/dev/{name}"
        except OSError:
            pass
    return "/dev/fb1"   # yolo


class FB:
    def __init__(self, dev):
        sysd = f"/sys/class/graphics/{os.path.basename(dev)}"
        with open(f"{sysd}/virtual_size") as f:
            self.w, self.h = map(int, f.read().strip().split(","))
        with open(f"{sysd}/bits_per_pixel") as f:
            self.bpp = int(f.read().strip())
        try:
            with open(f"{sysd}/stride") as f:
                self.stride = int(f.read().strip())
        except OSError:
            self.stride = self.w * self.bpp // 8
        self.f = open(dev, "r+b", buffering=0)
        self.last = None
        print(f"[fb] {dev}: {self.w}x{self.h} {self.bpp}bpp stride={self.stride}", flush=True)

    def show(self, img):
        if ROTATE:
            img = img.rotate(-ROTATE, expand=True)
        if (img.width < img.height) != (self.w < self.h):
            img = img.rotate(90, expand=True)
        if img.size != (self.w, self.h):
            img = img.resize((self.w, self.h), Image.NEAREST)
        a = np.asarray(img.convert("RGB"), dtype=np.uint16)
        if self.bpp == 16:   # rgb565, what fbtft gives u
            px = ((a[..., 0] >> 3) << 11) | ((a[..., 1] >> 2) << 5) | (a[..., 2] >> 3)
            data = px.astype("<u2").tobytes()
        else:                # 32bpp bgra, just in case
            b = np.full((self.h, self.w, 4), 255, np.uint8)
            b[..., 0], b[..., 1], b[..., 2] = a[..., 2], a[..., 1], a[..., 0]
            data = b.tobytes()
        rowbytes = self.w * self.bpp // 8
        if self.stride != rowbytes:
            pad = np.zeros((self.h, self.stride), np.uint8)
            pad[:, :rowbytes] = np.frombuffer(data, np.uint8).reshape(self.h, rowbytes)
            data = pad.tobytes()
        if data == self.last:   # nothing changed, don't waste spi time
            return
        self.last = data
        self.f.seek(0)
        self.f.write(data)


# ------------------------------------------------------------------ main ----
def render(state, faces, t, T):
    img = Image.new("RGB", (W, H), T.bg)
    d = ImageDraw.Draw(img)
    now, idle_since, stopped_at, err = state.snapshot()
    bg = faces.get(T.fx) if T.fx in ("aquarium", "graveyard", "snow", "city", "desktop") else None
    idle_for = 0.0 if now else time.time() - idle_since
    if bg and hasattr(bg, "server"):   # the blimp ad wants the server name. hacky but eh
        bg.server = state.server_name or "JELLYFIN"
    if bg and hasattr(bg, "idle_for"):   # desktop needs it to know when to start the screensaver
        bg.idle_for = idle_for
    if bg:
        emit = None
        if T.fx == "aquarium" and now and not now["paused"]:   # paused fish doesn't breathe? sure, why not
            emit = (prog_head_x(now) + 11, PROG_Y - 4)
        bg.draw_bg(img, t, T, playing=bool(now), emit=emit)
    if now:
        draw_playing(d, img, now, t, T)
    else:
        if T.face == "demon":
            faces["demon"].draw(d, img, t, idle_for, time.time() - stopped_at, err, T)
        elif not getattr(bg, "saver", False):   # screensaver on = no monster, it's asleep anyway
            if T.fx == "road":
                fx_road(d, t, T)
            mon = faces["monster"]
            mon.follow = getattr(bg, "cursor", None)   # win95: eyes follow the mouse
            if getattr(bg, "mood", None):              # bg wants a reaction (popup showed up etc)
                mon.event = (bg.mood[0], time.time() + bg.mood[1])
                bg.mood = None
            # eyes go on a transparent layer, see the CLEAR comment in Monster._eye
            layer = Image.new("RGBA", (W, H), CLEAR)
            mon.draw(ImageDraw.Draw(layer), t, idle_for, time.time() - stopped_at, err, T)
            img.paste(layer, (0, 0), layer)
        if err:
            text(d, 4, 2, tr("offline"), T.err)
    # stuff that goes IN FRONT of the eyes (snow, rain, popups, the cursor).
    # used to be idle-only, win95 needs it on the playing screen too for the bevels
    if bg and hasattr(bg, "draw_fg"):
        bg.draw_fg(d, t, T, playing=bool(now))

    if bg and hasattr(bg, "post"):   # whole-frame effects (the cyberpunk glitch)
        img = bg.post(img, t, playing=bool(now))

    out = img.resize((W * SCALE, H * SCALE), Image.NEAREST)
    if now and now["poster"] is not None and not PIXEL_POSTER:
        out.paste(now["poster"], (POSTER_XY[0] * SCALE, POSTER_XY[1] * SCALE))
    # if T.fx == "scanlines":   # was atari-only, now any theme can ask for it
    sl = 0.55 if T.fx == "scanlines" else T.raw.get("scanlines")
    if sl:
        a = np.array(out)
        a[1::2] = (a[1::2] * sl).astype(np.uint8)   # cheap crt vibes
        out = Image.fromarray(a)
    return out


def main():
    args = sys.argv[1:]

    def opt(name):
        i = args.index(name) if name in args else -1
        return args[i + 1] if 0 <= i < len(args) - 1 else None

    demo = "--demo" in args
    preview = opt("--preview")
    forced = opt("--theme")
    if forced:
        forced = forced.lower()
    if not JF_KEY and not demo:
        sys.exit("set JELLYFIN_API_KEY first (jellyfin dashboard > api keys)")

    state = State(demo=demo)
    state.start()
    themes = ThemeWatch(forced)
    # faces + animated backgrounds. bg ones are keyed by their fx name, render() looks em up that way
    faces = {"monster": Monster(), "demon": Demon(),
             "aquarium": Aquarium(), "graveyard": Graveyard(), "snow": Snowscape(), "city": City(),
             "desktop": Desktop()}
    t0 = time.time()
    print(f"[np] v{__version__} theme={themes.T.name} state={STATE_PATH}", flush=True)

    if preview:
        os.makedirs(preview, exist_ok=True)
        time.sleep(0.3)
        for i in range(int(os.environ.get("NP_FRAMES", "6"))):
            render(state, faces, time.time() - t0, themes.get()).save(f"{preview}/frame_{i:02d}.png")
            time.sleep(0.25)
        return

    fb = FB(find_fb())
    while True:
        start = time.time()
        frame = render(state, faces, start - t0, themes.get())
        fb.show(frame)
        # if DEBUG:
        #     frame.save("/tmp/np_frame.png")
        time.sleep(max(0, 1 / FPS - (time.time() - start)))


if __name__ == "__main__":
    main()
