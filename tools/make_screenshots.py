#!/usr/bin/env python3
"""
make_screenshots.py - renders the README gifs/pngs w/o needing a pi or a jellyfin

  python3 tools/make_screenshots.py            # -> docs/screenshots/

it fakes the clock so every animation runs at real speed even tho we render
way faster than real time. hacky but works. needs pillow + numpy like the app.
"""
import os
import random
import sys
import time as _time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(ROOT, "docs", "screenshots")
sys.path.insert(0, ROOT)
os.environ.setdefault("NP_STATE", os.path.join(OUT, ".state.json"))   # never touch the real one
sys.argv = [sys.argv[0]]

import nowplaying as n  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402

FPS = 6


class FakeClock:
    """stands in for the `time` module inside nowplaying"""
    now = 1_700_000_005.0   # some fixed moment, keeps the demo movie in its "playing" window

    def time(self):
        return self.now

    def strftime(self, fmt, *a):
        return "21:30" if fmt == "%H:%M" else _time.strftime(fmt, *a)

    def localtime(self, *a):
        return _time.localtime(12 * 3600)   # noon, so "sleepy" doesn't get extra weight


clk = FakeClock()
n.time = clk


def scene():
    return {"monster": n.Monster(), "demon": n.Demon(), "aquarium": n.Aquarium(),
            "graveyard": n.Graveyard(), "snow": n.Snowscape(), "city": n.City(), "desktop": n.Desktop()}


def tick(state, faces, T, t0):
    clk.now += 1 / FPS
    return n.render(state, faces, clk.now - t0, T)


def idle_state():
    s = n.State()
    s.idle_since = clk.now
    s.server_name = "MyJellyfin"
    return s


def playing_state(paused=False):
    s = n.State(demo=True)
    # land inside the demo's "movie is on" window. always FORWARD in time, going back made
    # t negative and the cyberpunk glitch fired on every frame lol
    clk.now = (clk.now // 90 + 1) * 90 + 5
    s.now = s._demo()
    s.now["paused"] = paused
    s.stamp = clk.now
    return s


def save_gif(frames, name, every=1):
    frames = frames[::every]
    path = os.path.join(OUT, name)
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=int(1000 / FPS * every),
                   loop=0, optimize=True)
    print(f"  {name}  ({os.path.getsize(path) // 1024} KB)")


def save_png(img, name):
    img.save(os.path.join(OUT, name), optimize=True)
    print(f"  {name}")


def theme_gif(theme, seconds, name, faces_seq=None, setup=None):
    random.seed(7)
    T = n.Theme(theme)
    f = scene()
    f["monster"].born -= 100   # skip the "just booted" surprised face
    if setup:
        setup(f)
    st = idle_state()
    t0 = clk.now
    frames = []
    total = int(seconds * FPS)
    for i in range(total):
        if faces_seq:   # force a sequence of faces, evenly split
            name_ = faces_seq[min(len(faces_seq) - 1, i * len(faces_seq) // total)]
            f["monster"]._mood = lambda *a, _n=name_: _n
        frames.append(tick(st, f, T, t0))
    save_gif(frames, name)
    return frames


def grid(images, cols, cell=(240, 160), pad=6, bg=(24, 24, 32)):
    rows = (len(images) + cols - 1) // cols
    W_ = cols * cell[0] + (cols + 1) * pad
    H_ = rows * cell[1] + (rows + 1) * pad
    out = Image.new("RGB", (W_, H_), bg)
    for i, im in enumerate(images):
        x = pad + (i % cols) * (cell[0] + pad)
        y = pad + (i // cols) * (cell[1] + pad)
        out.paste(im.resize(cell, Image.LANCZOS), (x, y))
    return out


def main():
    os.makedirs(OUT, exist_ok=True)
    n.LANG = "en"
    print("rendering ->", OUT)

    # hero: pico + a tour of faces
    theme_gif("pico", 12, "hero.gif",
              ["neutral", "happy", "joy", "surprised", "angry", "sad", "love", "wink", "dizzy", "sleepy"])

    # every face, labeled
    cells = []
    for face in n.EXPRS:
        random.seed(3)
        T = n.Theme("pico")
        f = scene()
        m = f["monster"]
        m.born -= 100
        m._mood = lambda *a, _f=face: _f
        m.next_blink = clk.now + 999
        st = idle_state()
        t0 = clk.now
        for _ in range(8):
            img = tick(st, f, T, t0)
        small = img.resize((240, 160), Image.NEAREST)
        d = ImageDraw.Draw(small)
        d.rectangle([0, 148, 240, 160], fill=(0, 0, 0))
        n.text(d, 4, 150, face, (255, 236, 39))
        cells.append(small)
    save_png(grid(cells, 4), "expressions.png")

    # playing screen + paused
    random.seed(1)
    T = n.Theme("pico")
    f = scene()
    p = playing_state()
    t0 = clk.now
    frames = [tick(p, f, T, t0) for _ in range(36)]
    save_png(frames[6], "playing.png")
    save_gif(frames, "playing.gif")

    # all the themes, idle + playing
    themes = [t for t, th in n.THEMES.items() if not th.get("hidden")]
    idle_cells, play_cells = [], []
    for th in themes:
        random.seed(5)
        T = n.Theme(th)
        f = scene()
        f["monster"].born -= 100
        f["monster"].next_blink = clk.now + 999
        f["city"].next_glitch = 1e12
        f["desktop"].next_dialog = 1e12
        st = idle_state()
        t0 = clk.now
        for _ in range(10):
            img = tick(st, f, T, t0)
        idle_cells.append(img)
        p = playing_state()
        for _ in range(4):
            img = tick(p, f, T, t0)
        play_cells.append(img)
    save_png(grid(idle_cells, 3), "themes-idle.png")
    save_png(grid(play_cells, 3), "themes-playing.png")

    # the animated ones get their own gif
    theme_gif("miami", 6, "theme-miami.gif")
    theme_gif("aquarium", 8, "theme-aquarium.gif")
    theme_gif("halloween", 8, "theme-halloween.gif", setup=lambda f: setattr(f["graveyard"], "next_ghost", 0.5))
    theme_gif("christmas", 8, "theme-christmas.gif", setup=lambda f: setattr(f["snow"], "next_sleigh", 1.0))
    theme_gif("cyberpunk", 9, "theme-cyberpunk.gif",
              setup=lambda f: (setattr(f["city"], "next_blimp", 0.3), setattr(f["city"], "next_glitch", 5.5)))
    theme_gif("win95", 14, "theme-win95.gif", setup=lambda f: setattr(f["desktop"], "next_dialog", 6.0))

    try:
        os.remove(os.environ["NP_STATE"])
    except OSError:
        pass
    print("done")


if __name__ == "__main__":
    main()
