"""
themes.py - color themes for the now playing lcd

NOTE: keep numpy/PIL OUT of this file. the `display` cli imports it and
importing numpy on a pi 3 takes like 1-2s, way too slow just to flip a color.

adding a theme: copy one of the blocks below, rename, go nuts.
any ui key you skip falls back to pico, so u don't have to define everything.

  poster  -> palette the movie poster gets crunched into (8-16 colors is the sweet spot)
  swatch  -> just what `display list` shows in the terminal
  ui      -> colors for every bit of the screen
  fx      -> optional background effect: "road" | "scanlines" | "fire" | "aquarium" | "graveyard"
             | "snow" | "city"
  prog_sprite -> lil guy riding the progress bar head: "fish" | "pumpkin" | "gift" | "car"
  scanlines   -> optional crt darkening on odd rows, 0..1 (lower = darker). fx "scanlines" = 0.55
  idle_colors -> optional, idle text gets drawn letter by letter cycling thru these
  idle_dots   -> False to skip the animated "..." after the idle text
  idle/label  -> plain string, or {"en": ..., "pt": ...} to translate it
"""
import json
import os

STATE_PATH = os.environ.get("NP_STATE", "/var/lib/nowplaying/state.json")
DEFAULT_THEME = "pico"

# old names -> new names. keeps an old state.json working after a rename
# (+ a couple of shorthands bc i'm lazy)
ALIASES = {"aquario": "aquarium", "xmas": "christmas", "cyber": "cyberpunk"}


def resolve(name):
    name = (name or "").lower()
    return ALIASES.get(name, name)


def hx(s):
    s = s.lstrip("#")
    return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))


THEMES = {
    # ------------------------------------------------------------------ pico --
    "pico": {
        "desc": "the og look, pico-8 palette",
        "swatch": ["1d2b53", "7e2553", "00e436", "29adff", "ffec27", "ff77a8"],
        "poster": ["000000", "1d2b53", "7e2553", "008751", "ab5236", "5f574f",
                   "c2c3c7", "fff1e8", "ff004d", "ffa300", "ffec27", "00e436",
                   "29adff", "83769c", "ff77a8", "ffccaa"],
        "ui": {
            "bg": "0c0e20", "bar": "1d2b53", "accent": "00e436", "paused": "ffec27",
            "eq": "ff77a8", "clock": "c2c3c7",
            "frame": "c2c3c7", "frame_in": "000000",
            "kind_bg": "7e2553", "kind_fg": "ffccaa",
            "title": "fff1e8", "sub": "29adff", "dim": "5f574f", "warn": "ffa300",
            "prog_border": "5f574f", "prog_bg": "000000", "prog_a": "00e436",
            "prog_b": "008751", "prog_off": "1d2b53", "prog_head": "fff1e8",
            "time": "c2c3c7", "pct": "ffec27", "play": "00e436",
            "eye": "fff1e8", "eye_line": "0c0e20", "pupil": "000000", "shine": "fff1e8",
            "lid_line": "7e2553", "closed": "83769c", "cheek": "ff77a8",
            "zzz": "83769c", "idle_txt": "83769c", "err": "ff004d",
            # expression props (brows, tears, hearts, anger mark, the !/? pops)
            "mbrow": "fff1e8", "tear": "29adff", "heart": "ff004d", "vein": "ff004d", "pop": "ffec27",
        },
    },

    # ------------------------------------------------------------------ snes --
    # us snes: that grey box + the purple/lavender buttons. light theme for once
    "snes": {
        "desc": "super nintendo (us) - purple + light grey",
        "swatch": ["c8c8d0", "a3a3ae", "5d5d6b", "4f3a9e", "8c7ed6", "b9b0ea"],
        "poster": ["1a1726", "2e2b3a", "4a4658", "5d5d6b", "7e7e8c", "a3a3ae",
                   "c8c8d0", "f4f4f8", "2f1f6e", "4f3a9e", "6e5cc4", "8c7ed6",
                   "b9b0ea"],
        "ui": {
            "bg": "c8c8d0", "bar": "5d5d6b", "accent": "e8e4ff", "paused": "b9b0ea",
            "eq": "b9b0ea", "clock": "e0e0e8",
            "frame": "2e2b3a", "frame_in": "a3a3ae",
            "kind_bg": "4f3a9e", "kind_fg": "f4f4f8",
            "title": "1a1726", "sub": "4f3a9e", "dim": "6e6e7c", "warn": "6e5cc4",
            "prog_border": "5d5d6b", "prog_bg": "e6e6ee", "prog_a": "4f3a9e",
            "prog_b": "6e5cc4", "prog_off": "b0b0bc", "prog_head": "1a1726",
            "time": "2e2b3a", "pct": "4f3a9e", "play": "4f3a9e",
            "eye": "f4f4f8", "eye_line": "2e2b3a", "pupil": "1a1726", "shine": "f4f4f8",
            "lid_line": "4f3a9e", "closed": "4f3a9e", "cheek": "b9b0ea",
            "zzz": "4f3a9e", "idle_txt": "4f3a9e", "err": "b0203a",
            "mbrow": "2e2b3a", "tear": "6e5cc4", "heart": "b0203a", "vein": "b0203a", "pop": "4f3a9e",
        },
    },

    # ----------------------------------------------------------------- atari --
    "atari": {
        "desc": "red on black, w/ crt scanlines",
        "swatch": ["000000", "3d0000", "a01010", "d41e1e", "ff3b30", "ffd0c8"],
        "poster": ["000000", "1a0000", "3d0000", "6b0a0a", "a01010", "d41e1e",
                   "ff3b30", "ff7a6b", "ffd0c8"],
        "fx": "scanlines",
        "ui": {
            "bg": "000000", "bar": "6b0a0a", "accent": "ffd0c8", "paused": "ff7a6b",
            "eq": "ff3b30", "clock": "ff7a6b",
            "frame": "d41e1e", "frame_in": "1a0000",
            "kind_bg": "d41e1e", "kind_fg": "000000",
            "title": "ff3b30", "sub": "ff7a6b", "dim": "7a1414", "warn": "ffd0c8",
            "prog_border": "6b0a0a", "prog_bg": "000000", "prog_a": "ff3b30",
            "prog_b": "d41e1e", "prog_off": "2a0000", "prog_head": "ffd0c8",
            "time": "ff7a6b", "pct": "ff3b30", "play": "ff3b30",
            "eye": "ff3b30", "eye_line": "000000", "pupil": "000000", "shine": "ffd0c8",
            "lid_line": "6b0a0a", "closed": "a01010", "cheek": "6b0a0a",
            "zzz": "a01010", "idle_txt": "d41e1e", "err": "ffd0c8",
            # red eyes -> hearts gotta be pale or they vanish
            "mbrow": "ff3b30", "tear": "ff7a6b", "heart": "ffd0c8", "vein": "ffd0c8", "pop": "ffd0c8",
        },
    },

    # ----------------------------------------------------------------- miami --
    # base 5 are the ones from the brief, rest are just darker/lighter shades of em
    "miami": {
        "desc": "miami vice, neon sunset + synthwave road",
        "swatch": ["ff5fa2", "ff8a3d", "ffd166", "2de2e6", "1b1f3a"],
        "poster": ["0d0f1f", "1b1f3a", "2c3160", "b8336a", "ff5fa2", "ff8a3d",
                   "ffd166", "1a8c90", "2de2e6", "fff4e6"],
        # "fx": "grid",   # old flat grid, road looks way better
        "fx": "road",
        "ui": {
            "bg": "1b1f3a", "bar": "0d0f1f", "accent": "ff5fa2", "paused": "ffd166",
            "eq": "2de2e6", "clock": "2de2e6",
            "frame": "ff5fa2", "frame_in": "0d0f1f",
            "kind_bg": "2de2e6", "kind_fg": "1b1f3a",
            "title": "fff4e6", "sub": "2de2e6", "dim": "6a6fa0", "warn": "ff8a3d",
            "prog_border": "ff5fa2", "prog_bg": "0d0f1f", "prog_a": "ff5fa2",
            "prog_b": "ff8a3d", "prog_off": "2c3160", "prog_head": "ffd166",
            "time": "2de2e6", "pct": "ffd166", "play": "ff5fa2",
            "eye": "fff4e6", "eye_line": "1b1f3a", "pupil": "0d0f1f", "shine": "fff4e6",
            "lid_line": "ff5fa2", "closed": "2de2e6", "cheek": "ff5fa2",
            "zzz": "2de2e6", "idle_txt": "ff8a3d", "err": "ff5fa2",
            "grid": "b8336a",
            # road bits
            "road": "0d0f1f", "road_edge": "2de2e6", "rumble": "ff5fa2", "dash": "ffd166",
            "mbrow": "ff5fa2", "tear": "2de2e6", "heart": "ff5fa2", "vein": "ff8a3d", "pop": "ffd166",
        },
    },

    # -------------------------------------------------------------- aquarium --
    # the animated one. fish, bubbles, seaweed, light rays. heavier on the cpu than the
    # others (still like ~1/4 of a frame on the pi, it's fine)
    # (was "aquario" til v0.6, ALIASES up top keeps old state files working)
    "aquarium": {
        "desc": "animated fish tank - fish, bubbles, seaweed",
        "swatch": ["06142e", "123a6b", "2b84c4", "5cc2e6", "ffb35c", "ff6f91"],
        "poster": ["06142e", "0b2447", "123a6b", "1b5c96", "2b84c4", "5cc2e6",
                   "a8ecf5", "e8fbff", "ffb35c", "ff6f91", "2fb36b", "c9a96e"],
        "fx": "aquarium",
        # "prog_fish": True,   # v0.6 flag, replaced by prog_sprite
        "prog_sprite": "fish",
        # top (near the surface) -> bottom, gets dithered between bands
        "water": ["1b5c96", "16507f", "12436e", "0f385e", "0b2c4e", "082240"],
        # (body, stripe). no orange+white combo on purpose, didn't want a certain movie fish lol
        "fish": [["ffb35c", "ff6f91"], ["ffd166", "2b84c4"], ["ff6f91", "ffd166"],
                 ["8fe0a0", "1e7a4c"], ["a8ecf5", "2b84c4"]],
        "ui": {
            "bg": "0b2447", "bar": "06142e", "accent": "5cc2e6", "paused": "ffd166",
            "eq": "8fe0a0", "clock": "a8ecf5",
            "frame": "5cc2e6", "frame_in": "06142e",
            "kind_bg": "ff6f91", "kind_fg": "06142e",
            "title": "e8fbff", "sub": "ffb35c", "dim": "7fa8c9", "warn": "ffd166",
            "prog_border": "2b84c4", "prog_bg": "06142e", "prog_a": "5cc2e6",
            "prog_b": "2b84c4", "prog_off": "123a6b", "prog_head": "ffb35c",
            "time": "a8ecf5", "pct": "ffd166", "play": "8fe0a0",
            "eye": "f0fbff", "eye_line": "06142e", "pupil": "06142e", "shine": "f0fbff",
            "lid_line": "2b84c4", "closed": "a8ecf5", "cheek": "ff6f91",
            "zzz": "a8ecf5", "idle_txt": "a8ecf5", "err": "ff6f91",
            "mbrow": "a8ecf5", "tear": "5cc2e6", "heart": "ff6f91", "vein": "ff8a3d", "pop": "ffd166",
            # tank stuff
            "foam": "a8ecf5", "sand": "c9a96e", "sand_hi": "e0c48a", "sand_dk": "8a6b3e",
            "weed_a": "1e7a4c", "weed_b": "2fb36b", "coral": "ff6f91", "coral_dk": "c94f73",
            "fish_eye": "06142e",
        },
    },

    # ------------------------------------------------------------- halloween --
    # spooky szn. animated too: moon + clouds, bats, graves, fog, flickering pumpkins,
    # a ghost that floats by sometimes. monster gets glowing-in-the-dark yellow eyes
    "halloween": {
        "desc": "spooky graveyard - bats, pumpkins, fog, ghost",
        "swatch": ["140a24", "3b1a5c", "ff7518", "ffd23f", "7cff4f", "f4e9c1"],
        "poster": ["0a0610", "140a24", "24113d", "3b1a5c", "6e5a8a", "c4500c",
                   "ff7518", "ffd23f", "f4e9c1", "7cff4f", "3d7a2e"],
        "fx": "graveyard",
        "prog_sprite": "pumpkin",
        "label": "NOW HAUNTING",
        "idle": {"en": "NOBODY HOME", "pt": "NINGUEM AQUI"},
        # top of the sky -> horizon. lighter purple near the ground so the tree pops
        "sky": ["0d0618", "130a22", "1a0e2e", "22123b", "2b1748", "331b55"],
        "ui": {
            "bg": "140a24", "bar": "0a0610", "accent": "ff7518", "paused": "ffd23f",
            "eq": "7cff4f", "clock": "d9c98f",
            "frame": "ff7518", "frame_in": "0a0610",
            "kind_bg": "ff7518", "kind_fg": "0a0610",
            "title": "f4e9c1", "sub": "ffb066", "dim": "7a6699", "warn": "7cff4f",
            "prog_border": "3b1a5c", "prog_bg": "0a0610", "prog_a": "ff7518",
            "prog_b": "c4500c", "prog_off": "24113d", "prog_head": "ffd23f",
            "time": "d9c98f", "pct": "7cff4f", "play": "ff7518",
            "eye": "ffd23f", "eye_line": "c4500c", "pupil": "140a24", "shine": "fff6d6",
            "lid_line": "c4500c", "closed": "ff7518", "cheek": "ff7518",
            "zzz": "b58cff", "idle_txt": "7cff4f", "err": "ff3b5c",
            # slime tears lol
            "mbrow": "b58cff", "tear": "7cff4f", "heart": "ff7518", "vein": "7cff4f", "pop": "7cff4f",
            # scene
            "moon": "f4e9c1", "moon_dk": "d9c98f", "moon_glow": "3b2a5e", "cloud": "2a1645",
            "star": "f4e9c1", "star_dim": "6e5a8a",
            "ground": "0a0610", "grass": "1c0f2e", "tree": "0a0610",
            "grave": "5a5470", "grave_dk": "2e2a3a",
            "pumpkin": "ff7518", "pumpkin_dk": "c4500c", "stem": "3d7a2e",
            "candle": "ffd23f", "candle_b": "ff9d00",
            "bat": "0a0610", "bat_eye": "ff3b5c",
            "ghost": "eef0ff", "ghost_dk": "a9a9d6", "ghost_eye": "140a24",
            "fog": "4a3a6a", "fog_hi": "6e5a8a",
        },
    },

    # ------------------------------------------------------------- christmas --
    # jingle szn. snow (some flakes fall IN FRONT of the eyes, depth!!), tree w/ chasing
    # lights, presents, a snowman that waves, light string up top, sleigh flying by.
    # progress bar is a candy cane (red/white blocks), a gift rides the head
    "christmas": {
        "desc": "snowy night - tree, lights, snowman, sleigh",
        "swatch": ["0e1634", "1a7a46", "ff3b4e", "f4f8ff", "ffd23f", "3fa9ff"],
        "poster": ["0a1028", "0e1634", "17234d", "1a7a46", "2fa35f", "ff3b4e",
                   "b8c8e8", "f4f8ff", "ffd23f", "3fa9ff", "5a3a1e"],
        "fx": "snow",
        "prog_sprite": "gift",
        "label": "NOW JINGLING",
        "idle": {"en": "MERRY XMAS!", "pt": "FELIZ NATAL!"},
        "idle_dots": False,
        "idle_colors": ["ff3b4e", "2fa35f", "3fa9ff"],
        "sky": ["0a1028", "0e1634", "121c40", "17234d", "1d2b5a", "243466"],
        "bulbs": ["ff3b4e", "3ddc5f", "3fa9ff", "ffd23f", "ff9d3d"],
        "ui": {
            "bg": "0e1634", "bar": "0a1028", "accent": "ff3b4e", "paused": "ffd23f",
            "eq": "3ddc5f", "clock": "f4f8ff",
            "frame": "ffd23f", "frame_in": "0a1028",
            "kind_bg": "1a7a46", "kind_fg": "f4f8ff",
            "title": "f4f8ff", "sub": "ffd23f", "dim": "7b8bb5", "warn": "3ddc5f",
            # candy cane bar
            "prog_border": "2fa35f", "prog_bg": "0a1028", "prog_a": "ff3b4e",
            "prog_b": "f4f8ff", "prog_off": "17234d", "prog_head": "ffd23f",
            "time": "b8c8e8", "pct": "3ddc5f", "play": "3ddc5f",
            "eye": "f4f8ff", "eye_line": "0a1028", "pupil": "0a1028", "shine": "f4f8ff",
            "lid_line": "ff3b4e", "closed": "b8c8e8", "cheek": "ff3b4e",   # cold = rosy cheeks
            "zzz": "b8c8e8", "idle_txt": "ffd23f", "err": "ff3b4e",
            "mbrow": "b8c8e8", "tear": "3fa9ff", "heart": "ff3b4e", "vein": "ff3b4e", "pop": "ffd23f",
            # scene
            "star": "f4f8ff", "star_dim": "5a6a9a",
            "snow": "f4f8ff", "snow_dim": "b8c8e8", "hill_back": "8796c4", "snow_shadow": "c9d6f0",
            "tree_a": "0f5132", "tree_b": "1a7a46", "tree_hi": "2fa35f", "trunk": "5a3a1e",
            "tstar": "ffd23f", "tstar_b": "fff6c8", "wire": "0f5132",
            "gift": "ff3b4e", "gift_b": "2fa35f", "gift_c": "3fa9ff", "ribbon": "ffd23f",
            "coal": "1a1a26", "carrot": "ff8a3d", "scarf": "ff3b4e", "stick": "5a3a1e",
            "hat": "1a1a26", "sleigh": "8796c4", "shadow_txt": "0a1028",
        },
    },

    # ------------------------------------------------------------- cyberpunk --
    # the vibe, not the game. rainy neon city, wet street reflections, hover cars,
    # ad blimp, signs w/ dying tubes, random glitches. eyes go neon outline + pink pupils
    "cyberpunk": {
        "desc": "rainy neon city - hover cars, glitches",
        "swatch": ["07060f", "1f1440", "ff2a6d", "05d9e8", "b026ff", "f9f002"],
        "poster": ["07060f", "0d0b1e", "1f1440", "3d1a5c", "b026ff", "ff2a6d",
                   "05d9e8", "2d7cff", "f9f002", "e8fbff", "ffcc66"],
        "fx": "city",
        "prog_sprite": "car",
        "label": "NOW STREAMING",
        "idle": {"en": "AWAITING SIGNAL", "pt": "AGUARDANDO SINAL"},
        "idle_colors": ["05d9e8"],   # single color, just here for the drop shadow lol
        "scanlines": 0.8,            # subtle. atari gets the heavy 0.55
        "sky": ["07060f", "0d0b1e", "15112e", "1f1440", "2c1650", "3d1a5c"],
        "ui": {
            "bg": "0d0b1e", "bar": "07060f", "accent": "05d9e8", "paused": "f9f002",
            "eq": "ff2a6d", "clock": "05d9e8",
            "frame": "ff2a6d", "frame_in": "07060f",
            "kind_bg": "ff2a6d", "kind_fg": "07060f",
            "title": "e8fbff", "sub": "05d9e8", "dim": "6b5b9a", "warn": "f9f002",
            "prog_border": "05d9e8", "prog_bg": "07060f", "prog_a": "ff2a6d",
            "prog_b": "b026ff", "prog_off": "1f1440", "prog_head": "f9f002",
            "time": "05d9e8", "pct": "f9f002", "play": "ff2a6d",
            # dark eyes, neon outline, glowing pink pupils.
            # outline was cyan (05d9e8) at first but it blended into the cyan windows/signs.
            # toxic green pops way more against all the pink + blue
            "eye": "1d1745", "eye_line": "39ff14", "pupil": "ff2a6d", "shine": "e8fbff",
            "lid_line": "39ff14", "closed": "39ff14", "cheek": "ff2a6d",
            "zzz": "b026ff", "idle_txt": "05d9e8", "err": "f9f002",
            "mbrow": "ff2a6d", "tear": "05d9e8", "heart": "ff2a6d", "vein": "f9f002", "pop": "f9f002",
            # city
            "bld_far": "1a1538", "bld_near": "0b0918", "win_far": "4a3f7a", "win_off": "241d45",
            "win_a": "ffcc66", "win_b": "05d9e8", "win_c": "ff2a6d",
            "antenna": "3a3060", "beacon": "ff2a6d",
            "neon_a": "ff2a6d", "neon_b": "05d9e8", "neon_c": "f9f002",
            "street": "07060f", "curb": "2a2450",
            "rain": "4a5a90", "rain_hi": "9fb4e8",
            "car_body": "2a2450", "car_head": "fff6c8", "car_tail": "ff2a6d",
            "blimp": "352b66", "blimp_dk": "221a45", "screen": "07060f",
            "shadow_txt": "07060f",
        },
    },

    # ----------------------------------------------------------------- win95 --
    # the 16 color era. teal desktop, beveled grey everything, navy title bars.
    # monster lives in a window, a cursor wanders around (eyes follow it!!), error
    # popups, starfield screensaver when it falls asleep. no logos/branding, just vibes
    "win95": {
        "desc": "retro desktop - 16 colors, beveled everything",
        "swatch": ["008080", "c0c0c0", "808080", "000080", "ffffff", "000000"],
        # the actual 16 color palette. posters only use it w/ NP_PIXEL_POSTER=1, looks amazing tho
        "poster": ["000000", "800000", "008000", "808000", "000080", "800080", "008080", "c0c0c0",
                   "808080", "ff0000", "00ff00", "ffff00", "0000ff", "ff00ff", "00ffff", "ffffff"],
        "fx": "desktop",
        "label": "PLAYER.EXE",
        "idle_xy": [50, 119],      # inside the window's status bar instead of bottom center
        "clock_in_tray": True,     # clock lives in the taskbar, not the top right corner
        "brow_top": 31,            # surprised brows were jumping into the menu bar lol
        "ui": {
            # playing screen = a maximized window
            "bg": "c0c0c0", "bar": "000080", "bar_paused": "808080",   # inactive title bar when paused!
            "accent": "ffffff", "paused": "c0c0c0", "eq": "ffffff", "clock": "000000",
            "frame": "808080", "frame_in": "000000",
            "kind_bg": "000080", "kind_fg": "ffffff",   # looks like a selected item
            "title": "000000", "sub": "000080", "dim": "808080", "warn": "800000",
            # that progress bar. navy chunks on white, no blinking, it just goes
            "prog_border": "808080", "prog_bg": "ffffff", "prog_a": "000080",
            "prog_b": "000080", "prog_off": "ffffff", "prog_head": "ffffff",
            "time": "000000", "pct": "000080", "play": "000000",
            "eye": "ffffff", "eye_line": "000000", "pupil": "000000", "shine": "ffffff",
            "lid_line": "808080", "closed": "000000", "cheek": "ff0000",
            "zzz": "808080", "idle_txt": "000000", "err": "ffff00",
            "mbrow": "000000", "tear": "0000ff", "heart": "ff0000", "vein": "ff0000", "pop": "ffff00",
            # desktop stuff
            "desktop": "008080", "win_gray": "c0c0c0", "win_dark": "808080", "win_light": "ffffff",
            "win_black": "000000", "title_bar": "000080", "title_txt": "ffffff",
            "folder": "ffff00", "folder_dk": "808000", "icon_txt": "ffffff", "select": "000080",
            "screen": "0000ff", "warn_tri": "ffff00",
        },
    },

    # ------------------------------------------------------------------ ???? --
    # shhh. hidden from `display list`
    "doom": {
        "desc": "...",
        "hidden": True,
        "label": "NOW SLAYING",
        "idle": {"en": "NO SOULS HERE", "pt": "NENHUMA ALMA"},
        "face": "demon",
        "fx": "fire",
        "swatch": ["000000", "6b1a08", "c8400c", "e86a10", "f7a01c", "ffd84a"],
        "poster": ["000000", "1c0a04", "3a1206", "6b1a08", "9c2a0a", "c8400c",
                   "e86a10", "f7a01c", "ffd84a", "fff4c0", "4a4440", "8a807a"],
        # index 0 = transparent, then cold -> hot
        "fire": ["000000", "3a1206", "6b1a08", "9c2a0a", "c8400c", "e86a10",
                 "f7a01c", "ffd84a", "fff4c0"],
        "ui": {
            "bg": "0a0503", "bar": "3a1206", "accent": "f7a01c", "paused": "ffd84a",
            "eq": "e86a10", "clock": "e86a10",
            "frame": "8a807a", "frame_in": "000000",
            "kind_bg": "9c2a0a", "kind_fg": "ffd84a",
            "title": "fff4c0", "sub": "e86a10", "dim": "6b5a50", "warn": "f7a01c",
            "prog_border": "6b1a08", "prog_bg": "000000", "prog_a": "e86a10",
            "prog_b": "c8400c", "prog_off": "2a0e05", "prog_head": "ffd84a",
            "time": "c8a080", "pct": "ffd84a", "play": "e86a10",
            "eye": "ffd84a", "eye_line": "6b1a08", "pupil": "000000", "shine": "fff4c0",
            "lid_line": "3a1206", "closed": "c8400c", "cheek": "6b1a08",
            "zzz": "e86a10", "idle_txt": "c8400c", "err": "ffd84a",
            # demon-only bits
            "glow": "e86a10", "rage_eye": "ff4a1a", "brow": "6b1a08",
            "horn": "d8c8a8", "horn_dark": "8a7a60",
            "mouth": "3a1206", "mouth_line": "c8400c", "fang": "fff4c0",
        },
    },
}


def load_state(path=STATE_PATH):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(state, path=STATE_PATH):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f)
    os.replace(tmp, path)   # atomic, so the daemon never reads half a file
