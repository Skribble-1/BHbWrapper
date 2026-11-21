"""engine/bot.py - the Bot base class: startup, bounties, fishing."""
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
from engine.vision import *  # noqa: F401,F403


class Bot:
    MOTION_PIXELS = 0.02       # this share of the picture must change to count as 'the screen is moving'

    def __init__(self, game, templates, props):
        self.game, self.tpl, self.props = game, templates, props
        self.close_chatbox = props.get("behavior.close-chatbox-direct-message", "").lower() == "true"
        self.next_housekeeping = 0.0
        self.ctx = ""                       # what the bot is doing, written onto saved screenshots
        self._saved_at, self._prev, self._still_there_n = {}, None, 0
        self.last_motion = time.time()
        self._pending = {}                  # screenshots waiting for the screen to freeze (see evidence())

    def save_frame(self, frame, folder, label, why="", cooldown=0):
        """Save a clean screenshot (use it with `template`) AND a *_annotated.png that says why it was taken, what the bot
        was doing and which known templates it could see. cooldown: skip if the same label was saved that recently."""
        now = time.time()
        if cooldown and now - self._saved_at.get(label, 0) < cooldown:
            return None
        self._saved_at[label] = now
        stuck_exit = self.note_unknown(frame) if folder == "unknown" else None
        d = OUT_DIR / folder
        d.mkdir(parents=True, exist_ok=True)
        for old in sorted(d.glob("*.png"))[:-298]:
            old.unlink(missing_ok=True)
            old.with_name(old.name[:-4] + "_matches.txt").unlink(missing_ok=True)
        base = "%s_%s" % (time.strftime("%Y%m%d-%H%M%S"), label)
        path = d / (base + ".png")
        cv2.imwrite(str(path), cv2.cvtColor(frame.img, cv2.COLOR_RGB2BGR))
        try:
            boxes, legend = [], []
            for name in self.tpl.names():
                try:
                    t = self.tpl.get(name)
                    if t.kind == "mask" or t.ink == (0, 0, 0):          # black-ink / mask templates match almost anything
                        continue
                    # the same rule the classifier uses: a template with a usual spot is only believed near it
                    pos = t.find(frame, DEFAULT_MARGIN) if t.at else t.find(frame, None)
                except Exception:
                    continue
                if pos:
                    boxes.append((name, (pos[0], pos[1], t.w, t.h)))
                    legend.append("%s @ x=%d y=%d (%dx%d)%s" % (name, pos[0], pos[1], t.w, t.h,
                                  "" if t.at else "  (any spot)"))
            lines = ["Saved %s: %s" % (time.strftime("%H:%M:%S"), why or label),
                     "Bot was doing: %s" % (self.ctx or "-"),
                     "Screen unchanged for %d s" % self.still_for(),
                     "Templates found (numbers are drawn on the picture): " + ("; ".join("%d %s" % (i, n) for i, (n, _) in enumerate(boxes, 1)) or
                                                                           "NONE - this screen needs a new template"),
                     "Last console lines:"] + [ln for ln in RECENT] + \
                    ["Clean copy for `bhbot.py template`: %s.png   (full list with positions: %s_matches.txt)" % (base, base)]
            cv2.imwrite(str(d / (base + "_annotated.png")), render_annotated(frame.img, lines, boxes))
            (d / (base + "_matches.txt")).write_text("\n".join("%d  %s" % (i, ln) for i, ln in enumerate(legend, 1)) + "\n", encoding="utf-8")
        except Exception as e:                       # annotation must never stop the bot
            log("(could not annotate screenshot: %s)" % e)
        if stuck_exit:
            raise BotExit(*stuck_exit)
        return path

    def note_unknown(self, frame):
        """Counts saved 'unknown' screenshots of the SAME picture in a row. At 3 (and if the GUI asked for it) the bot ends with
        a code the GUI turns into 'restart the game + script' or 'shut the game down'."""
        small = cv2.resize(cv2.cvtColor(frame.img, cv2.COLOR_RGB2GRAY), (100, 65), interpolation=cv2.INTER_AREA).astype(np.int16)
        prev = getattr(self, "_unk_img", None)
        same = prev is not None and float(np.abs(small - prev).mean()) < 6
        self._unk_img = small
        self._unk_n = (getattr(self, "_unk_n", 0) + 1) if same else 1
        action = str(getattr(self, "profile", {}).get("stuck_action", "none") or "none")
        if self._unk_n >= 3 and action in ("restart", "shutdown"):
            log("Stuck: %d screenshots of the same screen saved with nothing changing -> asking the GUI to %s" %
                (self._unk_n, "restart the game and script" if action == "restart" else "shut the game down"))
            return (EXIT_RESTART if action == "restart" else EXIT_SHUTDOWN), "stuck screen (3 identical screenshots)"
        return None

    # ---- is the picture moving? (battles / loading are not 'stuck') ---------- #
    def watch_motion(self, frame):
        small = cv2.resize(cv2.cvtColor(frame.img, cv2.COLOR_RGB2GRAY), (200, 130), interpolation=cv2.INTER_AREA).astype(np.int16)
        if self._prev is None or (np.abs(small - self._prev) > 24).mean() > self.MOTION_PIXELS:
            self.last_motion = time.time()
        self._prev = small
        for label, (since, folder, why, cd, recheck) in list(self._pending.items()):     # screenshots that were waiting for a frozen screen
            if self.still_for() >= self.STILL_SECS:
                del self._pending[label]
                try:
                    still_bad = recheck(frame) if recheck else True
                except Exception:
                    still_bad = True
                if not still_bad:                      # the odd frame that asked for this picture is long gone: the screen now is a normal one
                    log("(no screenshot for '%s': it was a one-frame flicker, the screen is recognised now)" % label)
                    continue
                path = self.save_frame(frame, folder, label, why + " (screen then froze for %d s)" % self.still_for(), cd)
                log("Screenshot saved: %s" % path)
            elif time.time() - since > 180:
                del self._pending[label]
                log("No screenshot for '%s': the screen kept changing, so the game was working normally" % label)

    def still_for(self):
        return time.time() - self.last_motion

    STILL_SECS = 10.0           # a screen must be unchanged this long before a 'stuck' screenshot is worth saving

    def evidence(self, frame, folder, label, why, cooldown=600, recheck=None):
        """A screenshot for YOU to look at - saved only if the game really is stuck (picture unchanged for STILL_SECS).
        While it is still moving (battle, loading, a normal quest run) the request waits; it is dropped after 3 minutes.
        The same kind is saved at most once per `cooldown` seconds. `recheck(frame)` is asked when the frozen picture is finally saved: if it
        says the problem is gone (a one-frame flicker), nothing is saved - a calm lobby must not be filed as 'unknown' or counted as 'stuck'."""
        if label in self._pending or time.time() - self._saved_at.get(label, 0) < cooldown:
            return None
        if self.still_for() >= self.STILL_SECS:
            path = self.save_frame(frame, folder, label, why + " (screen unchanged for %d s)" % self.still_for(), cooldown)
            log("Screenshot saved: %s" % path)
            return path
        self._pending[label] = (time.time(), folder, why, cooldown, recheck)
        log("(no screenshot yet: the screen is still changing - one is saved only if it freezes for %d s)" % self.STILL_SECS)
        return None

    # ---- daily login reward + bounties ---------------------------------------- #
    BOUNTY_SIGN = (100, 430, 46, 28)          # x, y, w, h of the little signboard (bottom left); its number is green when there is loot

    def daily_login(self, frame):
        """The 'Season Rewards' screen that appears on the first login of the day: press CLAIM, close the Items popup and the screen."""
        t = self.tpl.get("daily.claim")
        pos = t.find(frame)                   # near where the CLAIM button really is (anywhere matched battle art)
        if not pos:
            return False
        log("Daily login rewards -> pressing CLAIM")
        self.game.click(*t.center_of(pos))
        time.sleep(1.5)
        self.game.esc(3, gap=1.2)                # the 'Items' popup, then the Season Rewards screen
        return True

    def dialogue_plate(self, frame):
        """The little name plate above an NPC / enemy dialogue box: a light-bordered dark rectangle with coloured
        (or white) name text inside. Returns ((x0, y0, x1, y1), (r, g, b) of the text) or None."""
        img = frame.img.astype(int)
        mn = img.min(axis=2)
        top_ok = (mn >= 195) & (img[..., 2] >= img[..., 0])                # bright border line
        bottom_ok = (mn >= 130) & (img[..., 2] >= img[..., 0])             # the lower border is a little dimmer
        dark = img.max(axis=2) < 90
        text = img.max(axis=2) >= 140
        X0, X1 = 150, 760

        def runs(mask, y_lo, y_hi, need=100):
            out = {}
            for y in range(y_lo, y_hi):
                row = mask[y, X0:X1]
                edges = np.diff(np.concatenate(([0], row.view(np.int8), [0])))
                starts, ends = np.nonzero(edges == 1)[0], np.nonzero(edges == -1)[0]
                if len(starts):
                    i = int(np.argmax(ends - starts))
                    if ends[i] - starts[i] >= need:
                        out[y] = (int(starts[i]) + X0, int(ends[i]) + X0)
            return out

        tops, bottoms = runs(top_ok, 90, 330), runs(bottom_ok, 90, 380)
        for y0, (xa, xb) in sorted(tops.items()):
            for y1 in range(y0 + 30, y0 + 43):                               # a plate is ~40 px tall (popup title banners are 46-51)
                if y1 not in bottoms:
                    continue
                ya, yb = bottoms[y1]
                overlap = min(xb, yb) - max(xa, ya)                          # artwork may hide a corner: compare overlap, not exact ends
                if overlap < 0.7 * min(xb - xa, yb - ya):
                    continue
                xa, xb = max(xa, ya), min(xb, yb)
                inner = (slice(y0 + 4, y1 - 3), slice(xa + 5, xb - 5))
                area = (y1 - y0 - 7) * (xb - xa - 10)
                if area <= 0 or dark[inner].mean() < 0.45:
                    continue
                n_text = int(text[inner].sum())
                if not (40 <= n_text <= 0.4 * area):
                    continue
                box = dark[y1 + 6:min(y1 + 70, 520), max(xa - 250, 0):xb]           # the dialogue box lies right under it
                if box.mean() < 0.4:
                    continue
                px = img[inner][text[inner]]
                return (xa, y0, xb, y1), tuple(int(v) for v in np.median(px, axis=0))
        return None

    LOAD_MIN = 30                             # the game needs at least this long to log in
    LOAD_WAIT = 120                           # seconds to wait for the game to log in and show something
    RESUME_WAIT = 25                          # seconds the game needs to reload into the dungeon

    def resume_dungeon(self, frame):
        """'Uh oh! You were recently disconnected from a dungeon. Continue?' -> YES, then wait for the dungeon to load."""
        text, yes = self.tpl.get("resume.text"), self.tpl.get("resume.yes")
        if not text.find(frame, None):
            return False
        pos = yes.find(frame, None)
        if not pos:
            return False
        now = time.time()
        self._resume_times = [t0 for t0 in getattr(self, "_resume_times", []) if now - t0 < 600] + [now]
        if len(self._resume_times) > 6:                    # the same question six times in 10 min: YES is not getting the game anywhere (a hung tab)
            raise BotExit(EXIT_RESTART, "the 'Recently disconnected from a dungeon' message came back %d times in 10 minutes -> restarting the game" % len(self._resume_times))
        log("'Recently disconnected from a dungeon' message -> pressing YES, waiting %d s for the dungeon to load" % self.RESUME_WAIT)
        self.game.click(*yes.center_of(pos))
        time.sleep(self.RESUME_WAIT)
        return True

    # ---- first startup: ONE routine for every script (fish, afk, ...), run right after the game window was (re)started ---- #
    _start_due = False

    def on_map_now(self, frame):
        """The quiet main map (Afk narrows this: no battle, no navigation block)."""
        return self.on_main_screen(frame)

    def startup(self, ready=None, exit_on_timeout=True):
        """Wait for the game to log in and deal with everything that comes first: 'recently disconnected from a dungeon', the daily
        login rewards, the News popup. When the quiet main map shows, run startup_tasks() (hero check, bounties). `ready(frame)`
        lets a script accept another screen as 'loaded' (fishing: the fishing menu / scene). If the map does not show within
        LOAD_WAIT s: exit_on_timeout -> picture + exit code 3 (restart); otherwise carry on and do the tasks later (_start_due)."""
        log("Startup: waiting for the game to load ...")
        t_start, last_note = time.time(), 0.0
        while True:
            frame = self.game.screenshot()
            if self.resume_dungeon(frame):
                t_start = time.time()
                continue
            if self.daily_login(frame) or self.close_news(frame):
                continue
            waited = time.time() - t_start
            if waited >= self.LOAD_MIN and (self.on_main_screen(frame) or (ready is not None and ready(frame))):
                break
            if waited > self.LOAD_WAIT:
                if exit_on_timeout:
                    self.save_frame(frame, "unknown", "startup_not_loaded", "game did not show the main screen within %d s of startup" % self.LOAD_WAIT)
                    raise BotExit(EXIT_NO_ANCHOR, "the game did not load (no main screen after %d s)" % self.LOAD_WAIT)
                log("Startup: not on the map after %d s (a dungeon may be running) - carrying on, the start tasks follow on the first quiet map" % self.LOAD_WAIT)
                self._start_due = True
                return False
            if time.time() - last_note > 10:
                last_note = time.time()
                log("... still loading (%d s)" % waited)
            time.sleep(1)
        if self.on_map_now(frame):
            self._start_due = False
            self.startup_tasks(frame)
        else:
            self._start_due = True
        return True

    def startup_tasks(self, frame):
        """What is done once per game start on the quiet map. Afk adds the hero check in front."""
        self.collect_bounties("start")

    def always_on(self, frame):
        """Things that can appear at any moment in any script."""
        self.maintenance(frame)
        return self.resume_dungeon(frame) or self.still_there(frame) or self.daily_login(frame)

    def maintenance(self, frame):
        """'Bit Heroes is currently down for maintenance' -> close it and end with a code the GUI uses to wait and start again."""
        if self.tpl.get("globally.maintenance").find(frame, None):
            log("The game says it is down for maintenance -> closing the message and ending (the GUI restarts later)")
            self.game.click(398, 358)
            raise BotExit(EXIT_MAINTENANCE, "server maintenance")

    def news_open(self, frame):
        """The NEWS window (its carousel has yellow left/right arrows that must not be mistaken for a dialogue)."""
        return bool(self.tpl.get("globally.news").find(frame, 60))

    def close_news(self, frame):
        """News popup (login / event announcements): red X, then ESC. True if it was open."""
        if not self.news_open(frame):
            return False
        log("News popup -> closing it (red X, then ESC)")
        self.game.click(621, 98)
        time.sleep(1.0)
        self.game.esc(1)
        time.sleep(1.0)
        return True

    def on_main_screen(self, frame):
        """The map with its menu icons. Needs at least 3 of them: the loading screen's big QUEST logo looks like one."""
        n = sum(1 for name in ("pvp", "quest", "raid", "world-boss", "gvg", "invasion", "expedition", "trials", "gauntlet", "bait")
                if self.tpl.get("place." + name).find(frame, None))
        if n >= 3:
            return True
        if n == 2:      # a low-level hero has few icons, and a label can be hidden by a passing player / icon artwork: the bottom menu row decides
            return sum(1 for name in ("guild", "friends", "chat", "settings") if self.tpl.get("place.menu-" + name).find(frame)) >= 3
        return False

    def bounty_sign_green(self, frame):
        x, y, w, h = self.BOUNTY_SIGN
        reg = frame.img[y:y + h, x:x + w].astype(int)
        return int(((reg[..., 0] < 50) & (reg[..., 1] > 225) & (reg[..., 2] < 50)).sum()) >= 8

    def collect_bounties(self, when):
        """Open the Bounties page from the green number sign and press every LOOT button."""
        frame = self.game.screenshot()
        if not self.on_main_screen(frame):
            self.game.esc(3, gap=0.8)
            time.sleep(1.5)
            frame = self.game.screenshot()
            if not self.on_main_screen(frame):
                log("Bounties (%s): not on the main screen, skipping the check" % when)
                return
        if not self.bounty_sign_green(frame):
            log("Bounties (%s): nothing to collect" % when)
            return
        log("Bounties (%s): the number is green -> opening the bounties page" % when)
        self.game.click(self.BOUNTY_SIGN[0] + self.BOUNTY_SIGN[2] // 2, self.BOUNTY_SIGN[1] + self.BOUNTY_SIGN[3] // 2)
        time.sleep(2)
        looted = 0
        for _ in range(12):
            frame = self.game.screenshot()
            loot = self.tpl.get("bounty.loot")
            pos = loot.find(frame, None)
            if not pos:
                break
            self.game.click(*loot.center_of(pos))
            looted += 1
            log("Bounties: LOOT pressed (%d)" % looted)
            time.sleep(1.5)
            self.game.press("escape")            # close the 'Items' popup
            time.sleep(1.2)
        log("Bounties (%s): %d reward(s) collected -> closing the bounties page (ESC x4, 1 s apart)" % (when, looted))
        self.game.esc(4, gap=1.0)
        time.sleep(1)

    def still_there(self, frame):
        """'Are you still there?' - 20 minutes without input disconnects you; the game wants the space bar."""
        t = self.tpl.get("globally.are-you-still-there")
        pos = t.find(frame, None)
        if not pos:
            self._still_there_n = 0
            return False
        self._still_there_n += 1
        log("The game asked 'are you still there?' -> clicking YES")
        self.game.click(398, 358)                    # the YES button (the template is the text above it)
        if self._still_there_n >= 2:                 # did not go away: the space bar as well
            self.game.press("space")
        time.sleep(2)
        return True

    def dismiss_known(self, frame):
        """Apply user-registered rules (templates with on_seen). Returns True if one fired."""
        for t in self.tpl.handlers():
            pos = t.find(frame, margin=None)
            if pos:
                log("Known popup '%s' -> %s" % (t.name, t.on_seen))
                if t.on_seen == "click":
                    self.game.click(*t.center_of(pos))
                else:
                    self.game.press("escape" if t.on_seen == "esc" else t.on_seen)
                return True
        return False

    def housekeeping(self, frame, every=60):
        """Disconnect + chat-box direct message checks (the original's 'small tasks')."""
        now = time.time()
        if now < self.next_housekeeping:
            return
        self.next_housekeeping = now + every
        rc = self.tpl.get("globally.reconnect")
        pos = rc.find(frame)
        if pos:
            self.game.click(*rc.center_of(pos))
            raise BotExit(EXIT_DISCONNECTED, "Disconnected (clicked Reconnect)")
        sm = self.tpl.get("globally.send-message")
        pos = sm.find(frame, margin=None)
        if pos:
            path = self.save_frame(frame, "chatbox", "direct_message", "someone sent you a direct message")
            log("Someone sent you a direct message; screenshot saved to %s" % path)
            if not self.close_chatbox:
                raise BotExit(EXIT_DM, "direct message open (set behavior.close-chatbox-direct-message=true to auto-close)")
            cb = self.tpl.get("globally.close-chatbox")
            cpos = cb.find(frame, margin=None)
            if cpos:
                self.game.click(*cb.center_of(cpos))


# =========================================================================== #
# Fishing  (port of FishingApp.java)
# =========================================================================== #
class Fishing(Bot):
    STUCK_SECS = 300
    PLAY_WAIT = 6                     # seconds the game needs to walk to the fishing spot after PLAY
    BAR_Y, BAR_X0, BAR_X1 = (368, 376), 165, 628      # the power bar: two sample rows, first/last pixel column
    BAR_59 = 612                      # fill reaching this column = value 59 or 60 (a full bar ends near 626)

    _last_edge = None
    _rise = []
    lag = 0.1
    tau = 0.2                         # the bar swings like exp(-|time from the top| / tau): learned while fishing
    BAR_LO, BAR_HI = 167.0, 627.0     # edge x at value 20 and at value 60
    offset = 0.0                      # seconds: positive = cast later (self-tuning, see learn_from_cast)
    _last_def, _dir = None, 1
    KEY_DELAY = 0.03                  # time between sending the key and the game reacting
    BAR_PEAK = 621                    # x of a bar at 59.5-60

    def learn_from_cast(self):
        """Look at the frozen bar right after the cast, and nudge the timing if it stopped short of the top."""
        time.sleep(0.12)
        e1 = self.bar_edge(self.game.screenshot())
        time.sleep(0.05)
        e2 = self.bar_edge(self.game.screenshot())
        if abs(e1 - e2) <= 3 and e1 > self.BAR_X0 + 20:
            value = 20 + 40.0 * (e1 - self.BAR_LO) / (self.BAR_HI - self.BAR_LO)
            log("Cast landed at about %.0f of 60" % value)
            deficit = -self.tau * math.log(min(max((e1 - self.BAR_LO) / (self.BAR_HI - self.BAR_LO), 0.02), 1.0))          # seconds short of the top (early on the way up OR late on the way down)
            if deficit > 0.004:
                if self._last_def is not None and deficit > self._last_def + 0.002:
                    self._dir = -self._dir                              # last nudge made it worse: go the other way
                self.offset += self._dir * min(0.04, 0.7 * deficit)
            self._last_def = deficit
            self.offset = max(-0.15, min(0.15, self.offset))

    EMPTY = ((27, 87, 135), (32, 98, 152))      # colour of the unfilled part of the bar on the two sample rows

    def bar_edge(self, frame):
        """x of the right end of the filled part of the power bar. The fill changes colour with the value
        (red, orange, yellow, green, dark green, blue), so anything that is not the empty-bar colour counts."""
        img = frame.img[:, self.BAR_X0:self.BAR_X1 + 1].astype(int)
        filled = np.ones(img.shape[1], bool)
        for y, empty in zip(self.BAR_Y, self.EMPTY):
            filled &= np.abs(img[y] - np.array(empty)).sum(axis=1) > 70
        n = 0
        for v in filled:
            if not v:
                break
            n += 1
        return self.BAR_X0 + n

    def run(self, count):
        t = self.tpl
        label, start, cast, catch = (t.get("fishing." + n) for n in ("fishing", "start", "cast", "catch"))
        # the Start / Catch / Cast button is one fixed spot; click left of the text like the original did
        bx, by = start.expected
        button = (bx - 40, by)

        play, icon = t.get("fishing.menu-play"), t.get("place.bait")
        # 1) first startup (shared with AFK): login popups, news, dungeon prompt, bounties - then the fishing scene
        self.startup(ready=lambda f: bool(label.find(f) or play.find(f)))
        frame = self.game.screenshot()
        if not label.find(frame) and not play.find(frame):          # a stray menu (bounties ...) may still be open: ESC does nothing on the map
            log("Closing any menu that may still be open (ESC x4, 1 s apart)")
            self.game.esc(4, gap=1.0)
            time.sleep(1)
        # 2) main screen -> fishing icon (bait) -> menu -> PLAY -> wait -> fishing scene
        for attempt in range(5):
            frame = self.game.screenshot()
            if label.find(frame):
                break
            if self.close_news(frame):
                continue
            pos = play.find(frame)
            if pos:
                log("Fishing menu is open -> pressing PLAY, then waiting %d s for the fishing spot to load" % self.PLAY_WAIT)
                self.game.click(*play.center_of(pos))
                time.sleep(self.PLAY_WAIT)
                continue
            pos = icon.find(frame, None)
            if pos:
                log("Main screen -> opening the fishing menu (bait icon)")
                self.game.click(*icon.center_of(pos))
                time.sleep(1.5)
                continue
            if attempt >= 2 and self.on_main_screen(frame) and not icon.find(frame, None):
                # the plain main screen is showing (no menu in the way) and it has no bait icon: this hero has not unlocked fishing
                self.save_frame(frame, "unknown", "fishing_not_unlocked", "main screen without the fishing (bait) icon", 3600)
                raise BotExit(EXIT_UNAVAILABLE, "fishing is not available on this hero (no fishing icon on the main screen - not unlocked yet)")
            log("FISHING scene not visible yet (%d/5) -> ESC x2 in case a menu is in the way" % (attempt + 1))
            self.game.esc(2, gap=1.0)
            time.sleep(1.5)
        else:
            frame = self.game.screenshot()
            if not label.find(frame):
                if self.on_main_screen(frame) and not icon.find(frame, None):
                    raise BotExit(EXIT_UNAVAILABLE, "fishing is not available on this hero (no fishing icon on the main screen - not unlocked yet)")
                self.save_frame(frame, "unknown", "fishing_label_missing", "could not reach the fishing scene (no FISHING label, Play button or bait icon)")
                raise BotExit(EXIT_NO_ANCHOR, "unable to find the FISHING label of the fishing scene")

        remaining, exit_requested = count, False
        unsure_since = last_space = start_btn_since = None
        esc_tries, last_esc, unknown_shot = 0, 0.0, False
        log("Start fishing: %d to go" % remaining)
        while True:
            t0 = time.time()
            frame = self.game.screenshot()
            t1 = time.time()
            self.lag = 0.8 * self.lag + 0.2 * (t1 - t0)
            t_cap = t0 + 0.4 * (t1 - t0)                    # roughly when the picture was taken
            self.watch_motion(frame)
            self.housekeeping(frame)
            if self.always_on(frame):
                continue
            now = time.time()
            self.ctx = "fishing, %d cast(s) left" % remaining
            if remaining < 1:
                exit_requested = True

            if catch.find(frame):
                state = "catch"
            elif cast.find(frame):
                state = "cast"
            elif start.find(frame):
                state = "start"
            else:
                state = None
            if self.game.watch:
                r, g, b = frame.pixel(697, 389)
                log("[watch] state=%s  bar pixel=(%d,%d,%d)" % (state, r, g, b))
                time.sleep(0.5)

            if state is None:                                   # something else is on screen
                unsure_since = unsure_since or now
                idle = now - unsure_since
                if idle >= self.STUCK_SECS:
                    self.save_frame(frame, "unknown", "fishing_stuck", "no known fishing screen for %d s, giving up" % idle)
                    raise BotExit(EXIT_STUCK, "unable to continue (unsure for %d s)" % idle)
                if idle >= 3 and self.dismiss_known(frame):
                    time.sleep(1)
                elif idle >= 3 and (last_space is None or now - last_space >= 4):
                    last_space = now
                    self.game.press("space")
                if idle >= 20 and not unknown_shot:             # a screen we don't know: keep evidence
                    unknown_shot = True
                    log("Unknown screen for 20 s")
                    self.evidence(frame, "unknown", "fishing_unknown", "fishing: nothing recognised for 20 s", 300)
                if idle >= 20 and esc_tries < 3 and now - last_esc >= 10:
                    esc_tries, last_esc = esc_tries + 1, now          # maybe an unknown popup: try ESC (max 3x)
                    self.game.press("escape")
                continue
            unsure_since, unknown_shot, esc_tries = None, False, 0

            if state == "catch":
                r, g, b = frame.pixel(697, 389)                 # the catch bar must be full (green)
                if g < 230 or r > 100 or b > 100:
                    continue
                log("Catch! (bar %d,%d,%d)" % (r, g, b))
                self.game.press("space")
                if exit_requested:
                    break
                time.sleep(0.2)
                self.game.click(*button)
                self.game.press("space")
                time.sleep(1.5)

            elif state == "cast":
                edge = self.bar_edge(frame)
                prev = self._last_edge
                self._last_edge = (edge, t_cap)
                if self.game.watch:
                    log("[watch] bar edge x=%d" % edge)
                rising = prev is not None and edge > prev[0] + 3 and t_cap - prev[1] < 0.5
                if not rising:
                    self._rise = []                              # resting or falling: wait for the next swing
                    continue
                x = (edge - self.BAR_LO) / (self.BAR_HI - self.BAR_LO)
                if 0.12 <= x <= 0.97:
                    if self._rise:
                        t_a, x_a = self._rise[-1]
                        if 0.12 <= x_a <= 0.85 and x <= 0.9 and math.log(x) - math.log(x_a) > 0.05:
                            tau_obs = (t_cap - t_a) / (math.log(x) - math.log(x_a))
                            self.tau = 0.7 * self.tau + 0.3 * min(max(tau_obs, 0.12), 0.30)
                    self._rise.append((t_cap, x))
                if len(self._rise) < 2:
                    continue
                top = sorted(t - self.tau * math.log(x) for t, x in self._rise)      # each frame's guess for when the top comes
                top = top[len(top) // 2]
                fire_at = top - self.KEY_DELAY + self.offset
                wait = fire_at - time.time()
                if wait > self.lag + 0.04:
                    continue                                     # still a while to go: look again, the estimate gets better
                if wait < -0.06:
                    self._rise = []                              # too late for this swing
                    continue
                if wait > 0:
                    time.sleep(wait)
                self.game.press("space")
                remaining -= 1
                log("Casting (bar was at x=%d, top predicted in %d ms) - %d to go" % (edge, max(0, (top - t_cap) * 1000), remaining))
                self.learn_from_cast()
                self._last_edge, self._rise = None, []
                time.sleep(1.5)

            elif state == "start":
                start_btn_since = start_btn_since or now
                if now - start_btn_since >= self.STUCK_SECS:
                    self.save_frame(frame, "unknown", "fishing_stuck_start", "stuck on the Start button for 5 minutes")
                    raise BotExit(EXIT_STUCK, "unable to continue (stuck at Start button)")
                self.game.click(*button)
                time.sleep(1.5)
                continue
            start_btn_since = None
        log("Fishing has completed")
        self.game.esc(4, gap=0.8)
        time.sleep(1.5)
        self.collect_bounties("end")


# =========================================================================== #
# AFK  (port of AfkApp.java, plus: running commentary, event-proof navigation, give-up rules)
# =========================================================================== #
