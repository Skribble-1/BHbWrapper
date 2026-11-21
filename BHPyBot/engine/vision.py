"""engine/vision.py - pictures: templates, matching, frames, colours."""
import argparse
import base64
import collections
import json
import math
import os
import re
import shutil
import socket
import struct
import subprocess
import sys
import textwrap
import time
import urllib.request
import zipfile
from pathlib import Path

import cv2
import numpy as np

from engine import handlers, picker, screens
from engine.common import *  # noqa: F401,F403
from engine.game import *  # noqa: F401,F403


GRAY_TOLERANCE = 65     # original: tolerant.color.bw - white/black text may be this far off
DEFAULT_MARGIN = 40     # original: tolerant.position - how far from its usual spot a template may be


def _is_gray(c):
    return c[0] == c[1] == c[2]


def ink_map(img, ink):
    """Pixels that count as 'text colour' (exact match, or for gray inks any gray within tolerance)."""
    ink = tuple(int(v) for v in ink)
    ok = np.all(img == ink, axis=2)
    if _is_gray(ink):
        gray = (img[:, :, 0] == img[:, :, 1]) & (img[:, :, 1] == img[:, :, 2])
        ok |= gray & (np.abs(img[:, :, 0].astype(np.int16) - ink[0]) <= GRAY_TOLERANCE)
    return ok


def exact_map(img, ink):
    return np.all(img == tuple(int(v) for v in ink), axis=2)


class Frame:
    """One screenshot plus cached colour maps."""

    def __init__(self, img):
        self.img = img
        self._ink, self._exact = {}, {}

    def maps(self, ink):
        if ink not in self._ink:
            self._ink[ink] = ink_map(self.img, ink)             # plain True/False maps: cheap to make and to slice
            self._exact[ink] = exact_map(self.img, ink)
        return self._ink[ink], self._exact[ink]

    def pixel(self, x, y):
        return tuple(int(v) for v in self.img[y, x])


class Template:
    def __init__(self, name, rgb, ink=(255, 255, 255), kind="ink", at=None, on_seen=None, region=None):
        self.name, self.ink, self.at, self.on_seen, self.kind = name, tuple(ink), (tuple(at) if at else None), on_seen, kind
        self.region = tuple(region) if region else None      # (x0, y0, x1, y1): the only part of the screen this can match in
        if kind == "mask":      # pre-made masks of the original project: black = text pixel, red = must not be text
            black = np.all(rgb == 0, axis=2)
            border = np.all(rgb == (255, 0, 0), axis=2)
            self.origin = (0, 0)
            ink_px = black
        else:                   # crop of a real screenshot: text pixels -> bounding box, ring around them = border
            ok = ink_map(rgb, self.ink)
            ys, xs = np.nonzero(ok)
            if len(xs) == 0:
                raise ValueError("template '%s' has no pixels of colour %02x%02x%02x" % ((name,) + self.ink))
            x0, x1, y0, y1 = xs.min(), xs.max(), ys.min(), ys.max()
            ink_px = ok[y0:y1 + 1, x0:x1 + 1]
            ring = cv2.dilate(ink_px.astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool)
            border = ring & ~ink_px
            self.origin = (int(x0), int(y0))
        self.ink_t = ink_px.astype(np.float32)
        self.border_t = border.astype(np.float32)
        self.ink_b, self.border_b = ink_px.astype(bool), border.astype(bool)
        iy, ix = np.nonzero(self.ink_b)                  # ~12 text pixels spread over the whole label: a quick pre-filter
        pick = np.unique(np.linspace(0, len(iy) - 1, min(12, len(iy))).astype(int))
        self.samples = [(int(iy[i]), int(ix[i])) for i in pick]
        self.n_ink, self.n_border = int(ink_px.sum()), int(border.sum())
        self.h, self.w = ink_px.shape
        # map icons sit on moving artwork: a couple of ink pixels may be covered, so 'place.*' labels may miss 2% of their ink
        self.tol = max(2, int(self.n_ink * 0.02)) if name.startswith("place.") else 0

    @property
    def expected(self):
        """Usual top-left (of the text) in game coordinates, or None."""
        return (self.at[0] + self.origin[0], self.at[1] + self.origin[1]) if self.at else None

    def find_all(self, frame, margin=DEFAULT_MARGIN):
        """All positions (x, y of text top-left) where the template is on screen. margin=None: whole screen."""
        x0, y0, x1, y1 = 0, 0, GAME_W, GAME_H
        if margin is not None and self.expected:
            ex, ey = self.expected
            x0, y0 = max(0, ex - margin), max(0, ey - margin)
            x1, y1 = min(GAME_W, ex + self.w + margin), min(GAME_H, ey + self.h + margin)
        if self.region:
            x0, y0 = max(x0, self.region[0]), max(y0, self.region[1])
            x1, y1 = min(x1, self.region[2]), min(y1, self.region[3])
        if x1 - x0 < self.w or y1 - y0 < self.h:
            return []
        ink_m, exact_m = frame.maps(self.ink)
        sub = ink_m[y0:y1, x0:x1]
        H, W = sub.shape[0] - self.h + 1, sub.shape[1] - self.w + 1
        if self.tol:                                     # tolerant templates: straight comparison, border still strict
            good = cv2.matchTemplate(sub.astype(np.float32), self.ink_t, cv2.TM_CCORR) >= self.n_ink - self.tol - 0.5
            if self.n_border:
                good &= cv2.matchTemplate(exact_m[y0:y1, x0:x1].astype(np.float32), self.border_t, cv2.TM_CCORR) <= 0.5
            ys, xs = np.nonzero(good)
            return sorted((int(x) + x0, int(y) + y0) for y, x in zip(ys, xs))
        cand = np.ones((H, W), bool)                     # 1) positions where a dozen sample text pixels are all text-coloured
        for oy, ox in self.samples:
            cand &= sub[oy:oy + H, ox:ox + W]
            if not cand.any():
                return []
        cy, cx = np.nonzero(cand)
        if len(cy) > 3000:                               # very common colour: the full comparison is cheaper
            good = cv2.matchTemplate(sub.astype(np.float32), self.ink_t, cv2.TM_CCORR) >= self.n_ink - 0.5
            if self.n_border:
                good &= cv2.matchTemplate(exact_m[y0:y1, x0:x1].astype(np.float32), self.border_t, cv2.TM_CCORR) <= 0.5
            ys, xs = np.nonzero(good)
            return sorted((int(x) + x0, int(y) + y0) for y, x in zip(ys, xs))
        ex = exact_m[y0:y1, x0:x1]
        hits = []
        for y, x in zip(cy.tolist(), cx.tolist()):      # 2) full check of the few candidates
            if sub[y:y + self.h, x:x + self.w][self.ink_b].all() and not (self.n_border and ex[y:y + self.h, x:x + self.w][self.border_b].any()):
                hits.append((x + x0, y + y0))
        return sorted(hits)

    def find(self, frame, margin=DEFAULT_MARGIN):
        hits = self.find_all(frame, margin)
        if not hits:
            return None
        if self.at and len(hits) > 1:               # several matches: the one at the usual spot is the right one (not the first in reading order)
            return min(hits, key=lambda p: (p[0] - self.at[0]) ** 2 + (p[1] - self.at[1]) ** 2)
        return hits[0]

    def center_of(self, pos):
        return pos[0] + self.w // 2, pos[1] + self.h // 2


class Templates:
    def __init__(self, folder=TEMPLATE_DIR):
        self.folder = Path(folder)
        mf = self.folder / "templates.json"
        self.manifest = json.loads(mf.read_text()) if mf.is_file() else {}
        self._cache = {}

    def names(self):
        return sorted(set(self.manifest) | {p.stem for p in self.folder.glob("*.png")})

    def get(self, name):
        if name not in self._cache:
            png = self.folder / (name + ".png")
            if not png.is_file():
                close = [n for n in self.names() if name.split(".")[0] in n][:6]
                raise KeyError("no template '%s' (similar: %s)" % (name, ", ".join(close) or "-"))
            m = self.manifest.get(name, {})
            rgb = cv2.cvtColor(cv2.imread(str(png), cv2.IMREAD_COLOR), cv2.COLOR_BGR2RGB)
            ink = tuple(int(m.get("ink", "ffffff")[i:i + 2], 16) for i in (0, 2, 4))
            self._cache[name] = Template(name, rgb, ink, m.get("kind", "ink"), m.get("at"), m.get("on_seen"), m.get("region"))
        return self._cache[name]

    def save(self, name, bgr_crop, ink, at, on_seen=None):
        self.folder.mkdir(exist_ok=True)
        cv2.imwrite(str(self.folder / (name + ".png")), bgr_crop)
        entry = {"kind": "ink", "ink": "%02x%02x%02x" % tuple(ink)}
        if at:
            entry["at"] = list(at)
        if on_seen:
            entry["on_seen"] = on_seen
        self.manifest[name] = entry
        (self.folder / "templates.json").write_text(json.dumps(self.manifest, indent=1, sort_keys=True))
        self._cache.pop(name, None)

    def handlers(self):
        """Templates that say what to do when seen (on_seen: click | esc | space): the 'unknown popup' rules."""
        return [self.get(n) for n, m in sorted(self.manifest.items()) if m.get("on_seen")]


# =========================================================================== #
# Bot base: housekeeping that every task shares
# =========================================================================== #
def render_annotated(img_rgb, lines, boxes):
    """The screenshot with a caption strip on top and a green box + a small NUMBER on every template the bot recognised.
    The numbers are placed so they do not cover each other (or their own box edge); what each number is - name, position, size -
    is listed in the caption strip (`lines`) and in the *_matches.txt next to the picture."""
    bgr = cv2.cvtColor(img_rgb, cv2.COLOR_RGB2BGR)
    H, W = bgr.shape[:2]
    placed = []                                                       # rectangles already used by number tags

    def free(r):
        x0, y0, x1, y1 = r
        return x0 >= 0 and y0 >= 0 and x1 <= W and y1 <= H and all(x1 <= a or x0 >= c or y1 <= b or y0 >= d for a, b, c, d in placed)

    groups = []                                                       # matches on (nearly) the same spot share one box and one tag, e.g. trials.tier.1-12
    for i, (name, (x, y, w, h)) in enumerate(boxes, 1):
        for g in groups:
            if abs(g[0] - x) <= 4 and abs(g[1] - y) <= 4:
                g[4].append(i)
                g[2], g[3] = max(g[2], w), max(g[3], h)
                break
        else:
            groups.append([x, y, w, h, [i]])
    for x, y, w, h, ids in groups:
        cv2.rectangle(bgr, (x, y), (x + w, y + h), (0, 255, 0), 1)
        txt = str(ids[0]) if len(ids) == 1 else ("%d-%d" % (ids[0], ids[-1]) if ids[-1] - ids[0] == len(ids) - 1 else "%d+%d" % (ids[0], len(ids) - 1))
        (tw, th), _ = cv2.getTextSize(txt, cv2.FONT_HERSHEY_SIMPLEX, 0.4, 1)
        tw, th = tw + 4, th + 4
        # candidate spots around the box, then further away, until one is free
        spots = [(x, y - th), (x, y + h), (x + w, y), (x - tw, y), (x + w - tw, y - th), (x + w - tw, y + h), (x - tw, y - th), (x + w, y + h)]
        spots += [(x + dx, y + dy) for dy in (-2 * th, 2 * th, -3 * th, 3 * th) for dx in (0, tw, -tw, 2 * tw)]
        pos = next(((px, py) for px, py in spots if free((px, py, px + tw, py + th))), spots[0])
        px, py = max(0, min(W - tw, pos[0])), max(0, min(H - th, pos[1]))
        placed.append((px, py, px + tw, py + th))
        cv2.rectangle(bgr, (px, py), (px + tw, py + th), (0, 90, 0), -1)
        cv2.putText(bgr, txt, (px + 2, py + th - 3), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1, cv2.LINE_AA)
        if (px, py) != (x, y):                                          # a thin line from a displaced tag to its box
            cv2.line(bgr, (px + tw // 2, py + th // 2), (x + w // 2, y + h // 2), (0, 160, 0), 1, cv2.LINE_AA)
    rows = []
    for ln in lines:
        rows += textwrap.wrap(ln.encode("ascii", "replace").decode(), 112) or [""]
    strip = np.full((14 * len(rows) + 10, GAME_W, 3), 32, np.uint8)
    for i, ln in enumerate(rows):
        cv2.putText(strip, ln, (6, 16 + 14 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (120, 220, 255) if i == 0 else (235, 235, 235), 1, cv2.LINE_AA)
    return np.vstack([strip, bgr])
