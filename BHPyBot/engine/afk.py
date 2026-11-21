"""engine/afk.py - the AFK loop: watches the screen, runs the events, watchdogs and restarts."""
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
from engine.bot import *  # noqa: F401,F403
from engine.config import *  # noqa: F401,F403


class Afk(Bot):
    PVP_FIGHT_ROW = 54            # pixels between the PVP 'Fight' buttons
    GIVE_UP_NAV = 5               # clicked a map icon this many times and nothing opened -> skip the task for a while

    def __init__(self, game, templates, props, wanted, profile, ear=False, tick=1.0, retry=30):
        super().__init__(game, templates, props)
        self.wanted, self.profile, self.ear, self.tick, self.retry = wanted, profile, ear, tick, retry
        self.base_profile, self.disabled, self._ov_mtime = dict(profile), set(), -1
        self.hero_side, self._ov_hero, self._hero_id_tried = None, None, False      # which hero is active (0 left, 1 right): only known once YOUR HEROES was seen
        self._lobby, self._wb_solo = None, False
        self._tr = dict(done=False, tries=0, seen=0.0, stage="", scrolls=0, clicked=0.0, top=False, ups=0, last_first=None, rate=None, hi=None, last_k=0, fallback=False, bad=0, closes=0)
        self._gt = dict(done=False, tries=0, seen=0.0, stage="", scrolls=0, clicked=0.0, top=False, ups=0, last_first=None, rate=None, hi=None, last_k=0, fallback=False, bad=0, closes=0)
        self._ex = dict(seen=0.0, tried=[], idx=None, n=0, any=False, done=False, tries=0, stage="", clicked=0.0, ups=0, top=False, last_first=None, rate=None, hi=None, last_k=0, scrolls=0, fallback=False, bad=0, closes=0)
        self._iv = dict(done=False, tries=0, seen=0.0, stage="", clicked=0.0, ups=0, top=False, last_first=None, rate=None, hi=None, last_k=0, scrolls=0, fallback=False, bad=0, closes=0)
        self.nav_block_until = 0.0         # no map-icon clicks before this time (an activity is loading)
        self.lobby_seen = self.battle_seen = 0.0     # when a World Boss lobby / a battle (AUTO button) was last on screen
        self.activity = None              # task whose button was clicked last ('pvp' = no yellow-arrow checks while it runs)
        self._qz = dict(stage="left", n=0, steps=0, seen=0.0)
        self._was_waiting = set()
        self._map_lost_since = 0.0
        self._pop_n = {}
        self._icon_miss = {}               # (hero, task) -> how many times its map icon was missing in a row
        self.tasks = [t for t in ORDER if t in wanted and TASKS[t].get("supported", True)]
        self.blocked = {}                 # group -> (until, why)
        self.cur_char, self.char_blocks = 0, {}          # alternate characters: which hero (0 = the one we started on) and the other's saved pauses
        self._no_second, self._no_switch_until, self._switch_req_done = False, 0.0, 0.0
        self.idle = self.stuck = self.map_miss = 0
        self.nav_tries, self.quest_fail, self.quest_bad = {}, 0, []
        self.zone_steps, self.zone_trail, self.zone_seen = 0, [], 0.0
        self.next_small, self.auto_red, self.ladder = {}, 0, 0
        self._said, self._said_at = None, 0.0
        self._talk_box, self._talk_since, self._talk_clicks = None, 0.0, 0

    # ---- commentary --------------------------------------------------------- #
    WAIT_MSGS = ("In a battle - waiting", "Screen is changing", "Just entered an activity", "Waiting - ")   # the repetitive 'nothing to do' lines
    wait_every = 60                    # seconds between two prints of the same waiting message (changeable from the GUI)

    def say(self, msg, every=60):
        """Print what the bot is doing; same message again only after `every` seconds.
        Waiting messages are throttled per message (they used to alternate and so print on every look)."""
        now = time.time()
        if msg.startswith(self.WAIT_MSGS):
            seen = self.__dict__.setdefault("_wait_seen", {})
            if msg not in seen:
                log(msg)
            elif now - seen[msg] >= self.wait_every:
                log(msg + "   (still)")
            else:
                return
            seen[msg] = now
            if len(seen) > 200:
                seen.clear()
            return
        if msg != self._said:
            log(msg)
            self._said, self._said_at = msg, now
        elif now - self._said_at >= every:
            log(msg + "   (still)")
            self._said_at = now

    def block(self, task, minutes, why):
        spec = TASKS[task]
        if "tickets" in why or "energy" in why or "no " in why:
            self.nav_block_until = 0.0              # nothing was entered: the map is free to use right away
        if spec["group"] != "fishing":
            minutes = min(minutes, self.retry)      # re-check soon: regeneration speed differs per account
        until = time.time() + minutes * 60
        self.blocked[spec["group"]] = (until, why)
        if task == "quest":
            self.quest_bad, self.quest_fail = [], 0
        log("%s: pausing %d min (%s) - back at %s" % (spec["label"], minutes, why, time.strftime("%H:%M", time.localtime(until))))

    def refresh_overrides(self):
        """Re-read overrides.json when it changes (the GUI can change it while the bot runs)."""
        try:
            m = OVERRIDES_FILE.stat().st_mtime
        except OSError:
            m = 0
        if m == self._ov_mtime and self._ov_hero == self.hero_side:
            return
        self.ts_cfg = tshoot_all(load_overrides())
        first = self._ov_mtime == -1
        self._ov_mtime, self._ov_hero = m, self.hero_side
        base = dict(self.base_profile)
        own = (base.get("hero_profiles") or {}).get(self.hero_side) if self.hero_side in (0, 1) else None
        if own:
            base.update(own)                                          # the profile file's ig.left.* / ig.right.* lines
        self.profile, self.disabled, notes = apply_overrides(base, load_overrides(), self.hero_side)
        self.disabled |= set(base.get("profile_off") or ()) | set((base.get("hero_off") or {}).get(self.hero_side, ()))
        if own or (base.get("hero_off") or {}).get(self.hero_side):
            notes.insert(0, "profile file: the %s hero's own lines are used" % HERO_SIDES[self.hero_side])
        elif self.disabled and base.get("profile_off"):
            notes.append("profile file switches off: " + ", ".join(sorted(base["profile_off"])))
        if self.hero_side in (0, 1) and isinstance(load_overrides().get("heroes"), dict):
            notes.insert(0, "settings of the %s hero" % HERO_SIDES[self.hero_side])
        if self.profile.get("tick"):
            self.tick = float(self.profile["tick"])
        if self.profile.get("wait_every"):
            self.wait_every = int(self.profile["wait_every"])
        self.slow_wait = float(self.profile.get("base_tick") or SLOW_WAIT)
        if first:
            self._switch_req_done = float(self.profile.get("switch_request") or 0)      # an old request is not a new one
        if notes or not first:
            log("Settings overrides %s: %s" % ("in use" if first else "changed", "; ".join(notes) or "none (profile settings)"))

    def is_ready(self, task):
        if task in self.disabled:
            return False
        if task == "world-boss" and self.profile.get("wb_boss") not in WB_NAMES:       # nothing to summon: do not even open the window
            if not getattr(self, "_wb_warned", False):
                self._wb_warned = True
                log("World Boss: skipped - no boss is chosen. Pick one with the 'World Boss' override button (or ig.user.world-boss.name in your profile).")
            return False
        self._wb_warned = False if task == "world-boss" else getattr(self, "_wb_warned", False)
        return self.blocked.get(TASKS[task]["group"], (0, ""))[0] <= time.time()

    # ---- helpers ------------------------------------------------------------ #
    def see(self, name, frame, margin=AFK_MARGIN):
        t = self.tpl.get(name)
        return t, t.find(frame, margin)

    def tap(self, t, pos, dy=0):
        cx, cy = t.center_of(pos)
        self.game.click(cx, cy + dy)

    def acted(self):
        self.idle = self.stuck = self.map_miss = self.ladder = 0
        self._map_lost_since = 0.0

    def waiting_summary(self):
        """One line for every task: when it is checked again, 'ready', 'not available (skipping)' or 'switched off'.
        e.g. 'PVP until 12:14 (out of tickets/energy); Quest ready; Expedition not available on this hero (skipping)'."""
        now, parts = time.time(), []
        for t in self.tasks:
            label, g = TASKS[t]["label"], TASKS[t]["group"]
            until, why = self.blocked.get(g, (0, ""))
            if t in self.disabled:
                parts.append("%s switched off" % label)
            elif t == "world-boss" and self.profile.get("wb_boss") not in WB_NAMES:
                parts.append("World Boss: no boss chosen")
            elif (self.cur_char, t) in self._icon_miss and until > now:
                parts.append("%s not available on this hero (skipping, next look %s)" % (label, time.strftime("%H:%M", time.localtime(until))))
            elif until > now:
                parts.append("%s until %s (%s)" % (label, time.strftime("%H:%M", time.localtime(until)), why))
            else:
                parts.append("%s ready" % label)
        return "; ".join(parts)

    def talk_arrow(self, frame, margin=60, min_x=0):
        """The yellow 'next' arrow of a dialogue, or None. Its template is a black-text mask, which also 'matches' dark scenes (boss rooms),
        so a hit only counts when the pixel in its middle really is the arrow's pale yellow. The hit nearest the usual spot wins."""
        t = self.tpl.get("globally.talkRightArrow")
        hits = [p for p in t.find_all(frame, margin) if p[0] >= min_x]
        if t.at:
            hits.sort(key=lambda p: (p[0] - t.at[0]) ** 2 + (p[1] - t.at[1]) ** 2)
        for p in hits[:400]:
            cx, cy = t.center_of(p)
            if not (0 <= cx < GAME_W and 0 <= cy < GAME_H):
                continue
            r, g, b = frame.pixel(cx, cy)
            if r >= 200 and g >= 170 and b <= 190 and r - b >= 50:
                return p
        return None

    # ---- V2.0 screen classifier (screens.py) ------------------------------------------------------------------------ #
    def _screen_call(self, name, frame, afk_margin):
        """The checks screens.RULES can name with ("call", ...): the ones this class already had, in one place."""
        if name == "dialogue_open":                 # name plate + the yellow arrow, and not one of the look-alike screens
            if self.tpl.get("dungeons.zones").find(frame, None) or self.difficulty_screen(frame) or self.news_open(frame):
                return False
            return bool((self.talk_arrow(frame, 60) or self.talk_arrow(frame, None)) and self.dialogue_plate(frame))
        if name == "black_frame":
            return self.black_frame(frame)
        if name == "battle_open":                   # the AUTO tab (green = on, red = off: same text, different colour)
            return bool(self.tpl.get("globally.auto-green").find(frame, afk_margin))
        return bool(getattr(self, name)(frame))

    def ts_due(self, key):
        """Troubleshooting function `key`: False while it is switched Off (GUI) or its interval has not passed yet; otherwise True and the timer restarts."""
        cfg = (getattr(self, "ts_cfg", None) or tshoot_all({}))[key]
        now = time.time()
        if cfg["off"] or now < getattr(self, "_ts_next", {}).get(key, 0):
            return False
        self.__dict__.setdefault("_ts_next", {})[key] = now + cfg["every"]
        return True

    @staticmethod
    def black_frame(frame):
        """The whole picture is (nearly) black: the game is between two scenes, or has gone dark. Normal for a few seconds."""
        g = frame.img.max(axis=2)
        return bool((g < 24).mean() > 0.995)

    def battle_shadow(self, frame, busy):
        """Compare the old battle check (AUTO tab seen = 'battle') with the classifier's answer and log every
        DISAGREEMENT once per change ([battle-check] lines). Diagnostic only: nothing acts on it."""
        if not self.ts_due("battle"):
            return
        try:
            name = screens.classify(self, frame, AFK_MARGIN).name
        except Exception:
            return
        old = busy == "battle"
        new = name in ("battle", "results", "dialogue")
        key = (old, name if old != new else "")
        if key == getattr(self, "_bshadow", None):
            return
        self._bshadow = key
        if old != new:
            log("[battle-check] DISAGREE: old check says %s, classifier says '%s'" % ("battle" if old else "no battle", name))
            self.evidence(frame, "unknown", "battle_check_disagree", "old battle check: %s, classifier: %s" % ("battle" if old else "no battle", name), 600,
                          recheck=lambda f: bool(self.tpl.get("globally.auto-green").find(f, AFK_MARGIN)) != (screens.classify(self, f, AFK_MARGIN).name in ("battle", "results", "dialogue")))

    def health_tick(self, frame):
        """Chrome crash / hang watchdog. Every Troubleshooting interval (default 3 s): (1) ask the tab a trivial question (Game.health), (2) look at the picture: a flat
        light-grey (or white) page is Chrome's error / 'Aw, Snap' page, not the game. Two bad checks in a row (JS) or four (grey page)
        -> end with the restart code, the GUI then closes the game window and starts everything again."""
        now = time.time()
        if not self.ts_due("health"):
            return
        ok, why = self.game.health()
        if not ok and why.startswith("a browser dialog"):
            # e.g. "An error occurred running the Unity content ... function signature mismatch": the game is dead behind it. Press OK, restart.
            log("[health] " + why)
            self.game.close_dialog()
            try:
                self.save_frame(frame, "unknown", "chrome_dialog", why, 0)
            except Exception:
                pass
            raise BotExit(EXIT_RESTART, "the game crashed (%s) -> restarting the game" % why)
        self.frozen_check(frame, now)
        if not self.black_frame(frame):
            self._black_since = None
        else:
            if getattr(self, "_black_since", None) is None:
                self._black_since = now
            if now - self._black_since >= TSHOOT_BLACK_SECS:
                try:
                    self.save_frame(frame, "unknown", "black_screen", "the picture has been completely black for %d s" % TSHOOT_BLACK_SECS, 0)
                except Exception:
                    pass
                raise BotExit(EXIT_RESTART, "the screen has been completely black for %d s (the game died or hung) -> restarting the game" % TSHOOT_BLACK_SECS)
        if ok:
            self._bad_since = None
        elif getattr(self, "_bad_since", None) is None:
            self._bad_since = now
        g = cv2.cvtColor(frame.img, cv2.COLOR_RGB2GRAY)
        med = int(np.median(g))
        flat = bool((np.abs(g.astype(np.int16) - med) <= 6).mean() > 0.97) and med >= 190      # black loading screens are normal; grey / white is not
        if not flat:
            self._flat_since = None
        elif getattr(self, "_flat_since", None) is None:
            self._flat_since = now
        bad_for = now - self._bad_since if getattr(self, "_bad_since", None) is not None else 0
        flat_for = now - self._flat_since if getattr(self, "_flat_since", None) is not None else 0
        if not ok and now >= getattr(self, "_health_log", 0):
            self._health_log = now + 10
            log("[health] Chrome check failing for %d s (restart at %d s): %s" % (bad_for, TSHOOT_BAD_SECS, why))
        if flat and now >= getattr(self, "_health_log2", 0):
            self._health_log2 = now + 10
            log("[health] the picture has been a flat grey / white page for %d s (restart at %d s) - not the game" % (flat_for, TSHOOT_FLAT_SECS))
        if bad_for >= TSHOOT_BAD_SECS or flat_for >= TSHOOT_FLAT_SECS:
            try:
                self.save_frame(frame, "unknown", "chrome_unhealthy", "Chrome looks crashed or hung: " + (why or "flat grey page"), 0)
            except Exception:                      # the picture is only a courtesy: the restart must happen anyway
                pass
            raise BotExit(EXIT_RESTART, "Chrome crashed or is not responding (%s) -> restarting the game" % (why or "the page is a flat grey / white screen"))

    def frozen_check(self, frame, now):
        """A frozen battle: the AUTO tab is on screen but the picture has not changed AT ALL for 4 minutes (a live battle always moves)."""
        if not self.ts_due("frozen"):
            return
        small = cv2.resize(cv2.cvtColor(frame.img, cv2.COLOR_RGB2GRAY), (100, 65), interpolation=cv2.INTER_AREA).astype(np.int16)
        prev = getattr(self, "_fz_prev", None)
        same = prev is not None and float(np.abs(small - prev).mean()) < 0.15
        self._fz_prev = small
        in_battle = bool(self.tpl.get("globally.auto-green").find(frame, AFK_MARGIN))
        if not (same and in_battle):
            self._fz_since = None
            return
        if getattr(self, "_fz_since", None) is None:
            self._fz_since = now
        for_s = now - self._fz_since
        if for_s >= 60 and now >= getattr(self, "_fz_log", 0):
            self._fz_log = now + 60
            log("[frozen] the battle picture has not changed for %d s (restart at %d s)" % (for_s, TSHOOT_FROZEN_SECS))
        if for_s >= TSHOOT_FROZEN_SECS:
            self.game.close_dialog()
            try:
                self.save_frame(frame, "unknown", "battle_frozen", "the battle picture has not changed for 4 minutes", 0)
            except Exception:
                pass
            raise BotExit(EXIT_RESTART, "the battle screen has been frozen for 4 minutes (the game crashed or hung) -> restarting the game")

    def shadow_screen(self, frame):
        """Ask the classifier which screen this is and log it when it changes (the Troubleshooting menu's Screen check)."""
        if not self.ts_due("screen"):
            return
        try:
            sc = screens.classify(self, frame, AFK_MARGIN)
        except Exception as e:                      # never let the experiment disturb the bot
            if not getattr(self, "_shadow_err", False):
                self._shadow_err = True
                log("[screen] classifier error (ignored): %r" % (e,))
            return
        if sc.name == getattr(self, "_shadow_name", None):
            return
        self._shadow_name = sc.name
        log("[screen] %s%s" % (sc.name, "   (matched: %s)" % ", ".join(sc.why[:3]) if sc.why else "   - no rule matches this screen"))
        if sc.name == "unknown":
            self.evidence(frame, "unknown", "screen_unknown", "the screen classifier does not know this screen", 300,
                          recheck=lambda f: screens.classify(self, f, AFK_MARGIN).name == "unknown")

    def small_tasks(self, frame):
        """What the original ran in the background: Auto in battles, NPC talk, news, daily rewards, 'leave dungeon?'."""
        now = time.time()

        def due(key, every):
            if now < self.next_small.get(key, 0):
                return False
            self.next_small[key] = now + every
            return True

        if due("auto", 5):
            t = self.tpl.get("globally.auto-green")           # same 'AUTO' text for green and red: the button colour tells
            pos = t.find(frame, AFK_MARGIN)
            if pos and pos[0] >= 5:
                r, g, b = frame.pixel(pos[0] - 5, pos[1])
                if r <= 150 and g >= 205 and b <= 150:
                    self.auto_red = 0
                elif r >= 205 and g <= 150 and b <= 150:
                    self.auto_red += 1
                    if self.auto_red < 4:
                        if self.auto_red >= 2:
                            self.say("Auto is OFF in this battle - turning it on in %d s" % ((4 - self.auto_red) * 5), every=0)
                    else:
                        self.say("Auto is OFF in this battle -> turning it on", every=0)
                        self.game.click(*t.center_of(pos))          # click only: no key presses in a battle
                        time.sleep(1)
                        return True
        if due("buy", 2):                                       # 'not enough ...' for a task that is already paused: just close it
            for task in self.tasks:
                done = TASKS[task].get("done")
                if done and not self.is_ready(task) and self.tpl.get(done).find(frame, None):
                    self.say("%s: 'not enough ... buy more?' dialog -> ESC x4, 1 s apart" % TASKS[task]["label"], every=10)
                    self.game.esc(4, gap=1.0)
                    return True
        if due("fishranks", 2):                                 # 'FISHING RANKS' window: opens when the hero's click lands on the weekly player statue
            if self.tpl.get("globally.fishing-ranks").find(frame):
                if now - getattr(self, "_ranks_last", 0) > 8:   # a NEW occurrence (not the same window that is still closing)
                    self._ranks_times = [x for x in getattr(self, "_ranks_times", []) if now - x < 1800] + [now]
                self._ranks_last = now
                if len(self._ranks_times) >= 4:
                    raise BotExit(EXIT_RESTART, "the 'Fishing Ranks' window opened %d times in 30 min (the hero stands at the statue) -> restarting the game" % len(self._ranks_times))
                self._ranks_n = getattr(self, "_ranks_n", 0) + 1
                if self._ranks_n % 2:
                    self.say("'Fishing Ranks' window -> clicking its red X", every=10)
                    self.game.click(668, 75)
                    if len([x for x in self._ranks_times if now - x < 600]) >= 2 and now - getattr(self, "_leave_at", 0) > 600:
                        self._leave_at = now
                        time.sleep(1.5)
                        self.leave_statue()
                else:
                    self.say("'Fishing Ranks' window is still open -> ESC", every=10)
                    self.game.press("escape")
                time.sleep(1)
                return True
        if due("fishscene", 3):
            if self.tpl.get("fishing.fishing").find(frame):
                self.say("The fishing scene is open (not fishing now) -> ESC", every=10)
                self.game.press("escape")
                return True
        return False

    # ---- things that can show up at any time -------------------------------- #
    def npc_dialogue(self, frame, seen=True):
        """NPC / boss dialogue (name plate + the yellow arrow): click the arrow, every third time SPACE; gives up after 24 clicks on an
        unchanging screen. called when the classifier says 'dialogue' (seen=True); small_tasks
        calls it with the old direct check."""
        now = time.time()
        if self.activity == "pvp":
            return False
        t = self.tpl.get("globally.talkRightArrow")
        pos = self.talk_arrow(frame, 60) or self.talk_arrow(frame, None)
        plate = self.dialogue_plate(frame) if pos else None
        if not (pos and plate):
            self._talk_box = None
            return False
        box, rgb = plate
        if self._talk_box is None or max(abs(a - c) for a, c in zip(box, self._talk_box)) > 6:
            self._talk_box, self._talk_since, self._talk_clicks = box, now, 0
            return False
        if now - self._talk_since < 1.5:                        # name + arrow have stood still for a moment
            return False
        self._talk_clicks += 1
        if self._talk_clicks > 24:                              # a real dialogue ends long before this
            log("NPC dialogue: still the same screen after %d clicks - it is not a dialogue -> ESC x2" % (self._talk_clicks - 1))
            self.save_frame(frame, "unknown", "dialogue_endless", "clicked a 'dialogue' 24 times and the screen never changed", 600)
            self.game.esc(2, gap=1.0)
            self._talk_box, self._talk_clicks = None, 0
            self._talk_since = now + 20
            return True
        if self._talk_clicks % 3 == 0:
            self.say("NPC dialogue (name text %s) -> SPACE" % (rgb,), every=10)
            self.game.press("space")
        else:
            self.say("NPC dialogue (name text %s) -> clicking the yellow arrow" % (rgb,), every=10)
            self.game.click(*t.center_of(pos))
        self._talk_since = now
        return True

    def popup_table(self, frame):
        """popups that only need to be closed / answered are handled from ONE table (screens.POPUP_ACTIONS: screen name -> action).
        The classifier says which popup is open; this runs its action. (Popups with their own state - fishing ranks, 'quit this battle?',
        reconnect, resume - keep their own handlers.)"""
        now = time.time()
        if now < getattr(self, "_pop_next", 0):
            return False
        self._pop_next = now + 1.0
        try:
            sc = screens.classify(self, frame, AFK_MARGIN)
        except Exception:
            return False
        if sc.name != "dialogue":
            self._talk_box = None                                   # the dialogue is over: forget its name plate
        act = screens.POPUP_ACTIONS.get(sc.name)
        if not act:
            return False
        if act["do"] == "dialogue":                                 # NPC / boss dialogue (own timing + give-up rule)
            self._pop_next = now + 0.3
            return self.npc_dialogue(frame)
        key = "popup:" + sc.name
        if now < self.next_small.get(key, 0):
            return False
        self.next_small[key] = now + act.get("every", 2) + act.get("cooldown", 0)
        self.say(act["say"], every=10)
        kind = act["do"]
        if kind == "esc":
            if act.get("times", 1) > 1:
                self.game.esc(act["times"], gap=act.get("gap", 1.0))
            else:
                self.game.press("escape")
        elif kind == "click_template":
            t, pos = self.see(act["template"], frame, None)
            if not pos:
                return False
            self.game.click(*t.center_of(pos))
        elif kind == "x_then_esc":                                  # ESC alone is often ignored by some windows: the red X first, ESC the next time
            n = self._pop_n[sc.name] = self._pop_n.get(sc.name, 0) + 1
            if n % 2:
                self.game.click(*act["x"])
            else:
                self.game.press("escape")
        elif kind == "claim_then_esc":                              # daily rewards: press the claim button, then ESC x6
            t, pos = self.see(act["template"], frame, None)
            if not pos:                                             # the other daily screen (daily.claim) belongs to daily_login()
                return False
            self.tap(t, pos)
            self.game.esc(6, gap=1.0)
        elif kind == "confirm_team":                                # 'start with a non-full team?': its YES button (or SPACE)
            t, pos = self.see("globally.confirm-start-not-full-team", frame, None)
            if pos:
                self.tap(t, pos)
            yes = self.tpl.get("resume.yes").find(frame, None)
            if yes:
                self.game.click(*self.tpl.get("resume.yes").center_of(yes))
            else:
                self.game.press("space")
            self.acted()
        time.sleep(1)
        return True

    def keepalive(self, frame):
        t_q, pos_q = self.see("globally.confirm-quit-battle", frame, None)
        if pos_q:                                           # 'Exit - are you sure you want to quit this battle?' (ESC / the red X open it)
            # In a party lobby (World Boss START / READY / INVITE visible behind it) ESC asks the same question: there YES
            # just leaves the party. In a real battle it must be NO.
            # The dialog dims everything behind it, so the lobby's buttons are no longer recognised: go by what was seen just before.
            now = time.time()
            lobby = any(self.tpl.get(n).find(frame, None) for n in ("world-boss.start-boss", "world-boss.lobby-ready",
                                                                    "world-boss.lobby-unready", "world-boss.invite")) \
                or (self.lobby_seen > self.battle_seen and now - self.lobby_seen < 120)
            battle = bool(self.tpl.get("globally.auto-green").find(frame, AFK_MARGIN))
            if lobby and not battle:
                self.say("Exit question in the World Boss lobby -> YES (leaving the party)", every=0)
                self.game.click(329, 357)                   # the YES button
                self._lobby = None
            else:
                self.say("'Quit this battle?' dialog -> NO", every=0)
                self.game.click(464, 357)                   # the NO button
            time.sleep(1)
            self.acted()
            return True
        return False

    # ---- one task's screens ------------------------------------------------- #
    # ---- second character: main map -> YOUR HEROES -> the other hero -> CONFIRM -------------------------------- #
    HERO_SKULL_MAP = (92, 415)          # small grey skull above the player badge (only there once a 2nd hero is unlocked)
    HERO_BADGE, HERO_SKULL_PAGE = (47, 455), (172, 55)       # 2nd way: the badge opens the character page, the skull sits beside the name
    HERO_BOXES = ((147, 162, 305, 321), (327, 162, 485, 321))    # the left / right portrait on the YOUR HEROES page
    HERO_LOAD_WAIT = 60                 # seconds for the game to reload and the server to take the new hero

    def hero_active(self, frame):
        """0 = the left portrait is the active hero, 1 = the right one (the active one has a light glow around its frame)."""
        rings = []
        for x0, y0, x1, y1 in self.HERO_BOXES:
            o, w = 3, 6
            part = frame.img[y0 - o - w:y1 + o + w, x0 - o - w:x1 + o + w].astype(int).min(axis=2)
            inner = np.zeros(part.shape, bool)
            inner[w:-w, w:-w] = True
            rings.append(part[~inner].mean())
        if abs(rings[0] - rings[1]) < 12:
            return None
        return 0 if rings[0] > rings[1] else 1

    def open_heroes(self):
        title = self.tpl.get("heroes.title")
        for route in ("skull", "badge"):
            if route == "skull":
                self.game.click(*self.HERO_SKULL_MAP)
            else:
                self.game.click(*self.HERO_BADGE)
                time.sleep(1.5)
                self.game.click(*self.HERO_SKULL_PAGE)
            time.sleep(1.5)
            if title.find(self.game.screenshot()):
                return True
            self.game.esc(2, gap=0.8)
        return False

    def on_map_now(self, frame):
        return (self.on_main_screen(frame) and time.time() >= self.nav_block_until
                and not self.tpl.get("globally.auto-green").find(frame, AFK_MARGIN))

    def startup_tasks(self, frame):
        if self.needs_hero_id() and not self._hero_id_tried:
            self.identify_hero()                            # which hero (left / right) is playing: decides the override settings
        super().startup_tasks(frame)

    def needs_hero_id(self):
        """Do any settings depend on WHICH hero (left / right) is playing? Profile-file hero lines, per-hero overrides, or alternating."""
        bp, ov = self.base_profile, load_overrides()
        return bool(bp.get("hero_profiles") or bp.get("hero_off") or self.profile.get("alternate")
                    or (isinstance(ov.get("heroes"), dict) and ov["heroes"]))

    def identify_hero(self):
        """Which hero is active (left / right on the YOUR HEROES page)? Needed only when the overrides differ per hero. Opens the page,
        looks, closes it with ESC. Tried once per run; unknown = the shared override sections are used."""
        self._hero_id_tried = True
        log("Which hero is playing? Opening YOUR HEROES (the profile / overrides have settings per hero)")
        if not self.open_heroes():
            log("Per-hero overrides: the YOUR HEROES page did not open (no second hero?) -> the shared override settings are used")
            return False
        act = self.hero_active(self.game.screenshot())
        self.game.esc(2, gap=0.8)
        if act is None:
            log("Per-hero overrides: could not tell which hero is active -> the shared override settings are used")
            return False
        self.hero_side = act
        log("Per-hero overrides: the %s hero is active" % HERO_SIDES[act])
        self.refresh_overrides()
        return True

    def switch_character(self, why):
        """Open YOUR HEROES, pick the hero that is NOT active, CONFIRM, wait for the reload. True when the other hero is loaded."""
        log("Alternate characters (%s): opening YOUR HEROES" % why)
        if not self.open_heroes():
            log("Alternate characters: the YOUR HEROES page did not open - is a second character unlocked? Not trying again in this run.")
            self._no_second = True
            return False
        f2 = None
        for attempt in range(2):
            f = self.game.screenshot()
            act = self.hero_active(f)
            if act is None:
                break
            x0, y0, x1, y1 = self.HERO_BOXES[1 - act]
            log("Alternate characters: the %s hero is active -> choosing the %s one" % (("left", "right")[act], ("left", "right")[1 - act]))
            self.game.click((x0 + x1) // 2, (y0 + y1) // 2)
            time.sleep(1.2)
            f2 = self.game.screenshot()
            if self.hero_active(f2) == 1 - act:
                break
            f2 = None
        if f2 is None:
            log("Alternate characters: could not select the other hero -> ESC (will try again in 30 min)")
            self.save_frame(self.game.screenshot(), "unknown", "heroes_select_failed", "YOUR HEROES: could not tell / select the other hero", 600)
            self.game.esc(3, gap=0.8)
            self._no_switch_until = time.time() + 1800
            return False
        tc = self.tpl.get("heroes.confirm")
        pos = tc.find(f2)
        self.tap(tc, pos) if pos else self.game.click(390, 449)
        log("Alternate characters: CONFIRM pressed -> waiting %d s for the game to reload with the other hero" % self.HERO_LOAD_WAIT)
        t0 = time.time()
        time.sleep(8)
        while time.time() - t0 < 180:
            try:
                fr = self.game.screenshot()
                if self.resume_dungeon(fr) or self.daily_login(fr) or self.close_news(fr):
                    time.sleep(1)
                    continue
                if time.time() - t0 >= self.HERO_LOAD_WAIT and self.on_main_screen(fr):
                    break
            except (ConnectionError, OSError, ValueError) as e:        # the page may blink while it reloads
                log("(waiting for the game to reload: %s)" % e)
            time.sleep(1)
        self.cur_char = 1 - self.cur_char
        if act is not None:
            self.hero_side = 1 - act                    # the hero we just chose: its own override settings apply from now on
            self.refresh_overrides()
        self.activity, self.nav_block_until = None, 0.0
        self.nav_tries, self.quest_fail, self.quest_bad = {}, 0, []
        self.zone_steps, self.zone_trail, self.next_small = 0, [], {}
        self.acted()
        log("Alternate characters: the other hero is loaded -> starting the AFK chain over")
        self.collect_bounties("start")
        return True

    def switch_if_exhausted(self, frame):
        """Every task is waiting: if the other hero still has something to do, switch to it."""
        if not self.profile.get("alternate") or self.ear or self._no_second or time.time() < self._no_switch_until:
            return False
        if not self.on_map_now(frame):
            return False
        other, now = self.char_blocks.get(1 - self.cur_char, {}), time.time()
        if not any(t not in self.disabled and other.get(TASKS[t]["group"], (0, ""))[0] <= now for t in self.tasks):
            self.say("Alternate characters: the other hero is waiting too - staying here", every=600)
            return False
        mine = dict(self.blocked)
        self.char_blocks[self.cur_char] = mine
        self.blocked = dict(other)
        if self.switch_character("every task of this hero is waiting"):
            return True
        self.blocked = mine
        return False

    def leave_statue(self):
        """The statue (it copies a different player every week) sits where the hero stands. The fishing PLAY button moves the hero to
        a fishing spot far from it: fishing icon -> PLAY -> wait -> ONE ESC (back to the map, hero now somewhere else)."""
        log("The 'Fishing Ranks' window keeps opening -> moving the hero away from the statue: fishing icon -> PLAY -> wait -> ESC")
        icon, play = self.tpl.get("place.bait"), self.tpl.get("fishing.menu-play")
        pos = icon.find(self.game.screenshot(), None)
        if not pos:
            log("(the fishing icon was not found - skipping)")
            return False
        self.game.click(*icon.center_of(pos))
        time.sleep(2)
        pos = play.find(self.game.screenshot())
        if not pos:
            log("(the fishing menu did not open - ESC)")
            self.game.press("escape")
            return False
        self.game.click(*play.center_of(pos))
        time.sleep(6)                                           # the game walks the hero to the fishing spot and loads it
        self.game.press("escape")
        time.sleep(1.5)
        return True

    MENU_TASK = {"world_boss_menu": "world-boss", "world_boss_lobby": "world-boss", "pvp_menu": "pvp", "trials_menu": "trials", "gauntlet_menu": "gauntlet",
                 "gvg_menu": "gvg", "invasion_menu": "invasion", "expedition_menu": "expedition", "raid_menu": "raid"}

    def close_paused_menu(self, frame, ready):
        """an event's menu is open but that event is paused / switched off / not wanted (e.g. World Boss ran out of Xeals): close it
        with ESC right away (one ESC per 2.5 s: lobby -> 'leave the party?' -> list -> map) instead of waiting for the unknown-screen timer."""
        if time.time() - getattr(self, "_menu_esc_at", 0.0) < 2.5:
            return False
        try:
            sc = screens.classify(self, frame, AFK_MARGIN)
        except Exception:
            return False
        if sc.name == "difficulty_popup" and not ({"trials", "gauntlet", "expedition"} & set(ready)):
            # the DIFFICULTY list was opened while its event was still allowed, then the event paused (tickets gone): nobody wants it now
            self._menu_esc_at = time.time()
            self.say("[menu] a difficulty list is open but Trials / Gauntlet / Expedition are not running now -> ESC", every=30)
            self.game.esc(1, 0)
            return True
        task = self.MENU_TASK.get(sc.name)
        if not task or task in ready:
            return False
        self._menu_esc_at = time.time()
        self.say("[menu] the %s screen is open but %s is not running now -> ESC" % (sc.name.replace("_", " "), TASKS[task]["label"]), every=30)
        self.game.esc(1, 0)
        return True

    def difficulty_screen(self, frame):
        """The dungeon's NORMAL / HARD / HEROIC picker (all three buttons visible)."""
        return all(self.tpl.get(n).find(frame, AFK_MARGIN) for n in ("dungeons.normal", "dungeons.hard", "dungeons.heroic"))

    def retry_difficulty(self, frame):
        """A click on NORMAL / HARD / HEROIC can get lost. The 25 s 'loading' wait that follows it would then hide the still-open picker
        for ages, so: still open 4 s after our click -> choose again (twice at most)."""
        since = time.time() - getattr(self, "_mode_click_at", 0.0)
        if not (4.0 < since < 25.0) or getattr(self, "_mode_retries", 0) >= 2 or not self.difficulty_screen(frame):
            return False
        self._mode_retries = getattr(self, "_mode_retries", 0) + 1
        self._mode_click_at = time.time()
        name = {1: "dungeons.normal", 2: "dungeons.hard", 3: "dungeons.heroic"}.get(self.profile["quest_mode"], "dungeons.heroic")
        t, pos = self.see(name, frame)
        if not pos:
            return False
        log("Quest: the difficulty picker is still open %d s after choosing -> choosing %s again (%d of 2)" % (since, name.split(".", 1)[1], self._mode_retries))
        self.tap(t, pos)
        return True

    def quest_difficulty(self, frame):
        if not self.difficulty_screen(frame):
            return False
        name = {1: "dungeons.normal", 2: "dungeons.hard", 3: "dungeons.heroic"}.get(self.profile["quest_mode"], "dungeons.heroic")
        t, pos = self.see(name, frame)
        self.say("Quest: difficulty screen -> choosing %s" % name.split(".", 1)[1], every=10)
        self.tap(t, pos)
        self._mode_click_at, self._mode_retries = time.time(), 0
        self.activity = "quest"
        self.acted()
        time.sleep(1.5)
        return True

    RESULT_WORDS = ("town", "rerun", "collect", "regroup")      # buttons of the screen AFTER a battle

    def try_chain(self, task, frame, results_only=False):
        """results_only: a battle is running / loading, so only the buttons that can appear after it are looked for."""
        spec = TASKS[task]
        if spec.get("done"):
            t, pos = self.see(spec["done"], frame, None)
            if pos:
                self.say("%s: out of tickets/energy (asks to buy more) -> ESC x4, 1 s apart" % spec["label"])
                self.game.esc(4, gap=1.0)
                self.block(task, spec["block"], "out of tickets/energy")
                self.acted()
                return True
        for name in spec["chain"]:
            # in a battle only result buttons; while a battle is just loading the Team screen's ACCEPT is still allowed;
            # before a hero switch nothing new is started (no rerun / accept), the run just ends at 'town'
            allowed = ("town", "collect") if results_only == "switch" else self.RESULT_WORDS + (("accept",) if results_only == "loading" else ())
            if results_only and not any(w in name for w in allowed):
                continue
            t, pos = self.see(name, frame)
            if not pos:
                continue
            dy, target = 0, 0
            if name in ("pvp-arena.fight", "gvg.fight"):       # PVP and GVG: the same 'Select opponent' list, rows PVP_FIGHT_ROW apart
                target = self.profile["pvp_target" if name.startswith("pvp") else "gvg_target"]
                if target > 1:
                    dy = (target - 1) * self.PVP_FIGHT_ROW
            self.say("%s: clicking '%s'%s" % (spec["label"], name.split(".", 1)[1], " (opponent row %d)" % target if dy else ""), every=20)
            self.tap(t, pos, dy)
            if any(k in name for k in ("accept", "fight", "enter", "start", "rerun")):      # these start a battle / dungeon: it needs time to load
                self.nav_block_until = time.time() + self.NAV_GRACE
            elif any(k in name for k in self.RESULT_WORDS):
                self.nav_block_until = 0.0                    # the run is over (town / win / defeat / collect): the menu behind it is next - no waiting
            self.activity = task
            self.nav_tries[task] = 0
            self.acted()
            return True
        return False

    def claim_bait(self, frame):
        t, pos = self.see("fishing.bait", frame)
        if not pos:
            return False
        log("Fishing: claiming bait")
        self.tap(t, pos)
        self.game.esc(6, gap=1.0)
        time.sleep(3)
        t, pos = self.see("fishing.bait", self.game.screenshot())
        if pos:
            self.tap(t, pos)
            self.game.esc(1, gap=1.0)
        self.block("fishing", TASKS["fishing"]["block"], "claimed")
        self.acted()
        return True

    # ---- quest: choose a level on the zone screen, with a give-up rule ------- #
    ZONE_STEPS_MAX = 25               # how many zones to walk back looking for one that has open levels

    def close_zone_map(self):
        """Close the Quest zone map. ESC alone was ignored once (map stayed open for 40 min), so: the red X first, then ESC."""
        self.game.click(728, 72)
        time.sleep(0.8)
        self.game.press("escape")

    def zone_arrow(self, frame, side):
        """Centre of the yellow zone arrow ('L' or 'R'), or None (the arrow is hidden on the first/last zone)."""
        x0, x1 = (40, 75) if side == "L" else (728, 765)
        reg = frame.img[240:310, x0 - 3:x1 - 3].astype(int)          # game coordinates (window origin is 3,31)
        r, g, b = reg[..., 0], reg[..., 1], reg[..., 2]
        m = (r > 200) & (g > 190) & (r - b > 90) & (abs(r - g) < 40)
        if m.sum() < 200:
            return None
        ys, xs = np.nonzero(m)
        return int(xs.mean()) + x0 - 3, int(ys.mean()) + 240

    def zone_fp(self, frame):
        """Small fingerprint of the zone title banner (differs from zone to zone)."""
        g = cv2.cvtColor(frame.img[44:90, 250:565], cv2.COLOR_RGB2GRAY)
        return cv2.resize(g, (64, 10), interpolation=cv2.INTER_AREA).astype(np.float32)

    @staticmethod
    def same_zone(a, b):
        return float(np.abs(a - b).mean()) < 6.0

    def star_levels(self, frame):
        """Levels with gold stars under them -> [(click_x, click_y, star_count)]. Empty/dark stars do not count.
        A star is a gold blob of a fixed size (~22x22 px, ~165 px of gold); lava, sand and fire are other shapes."""
        img = frame.img.astype(int)
        r, g, b = img[..., 0], img[..., 1], img[..., 2]
        m = ((r >= 225) & (g >= 140) & (g <= 225) & (b <= 90)).astype(np.uint8)
        m[:100] = 0; m[:, :60] = 0; m[:, 740:] = 0       # header, zone arrows
        m[420:, :160] = 0                                # player badge, bottom left
        n, _, st, _ = cv2.connectedComponentsWithStats(m, connectivity=8)
        stars = sorted((int(st[i][0] + st[i][2] // 2), int(st[i][1]), int(st[i][1] + st[i][3])) for i in range(1, n)
                       if 19 <= st[i][2] <= 26 and 19 <= st[i][3] <= 26 and 140 <= st[i][4] <= 200)
        groups = []                                      # one dungeon = up to 3 stars side by side
        for x, y0, y1 in stars:
            for gr in groups:
                if abs(x - gr[-1][0]) <= 40 and abs(y0 - gr[-1][1]) <= 14:
                    gr.append((x, y0, y1)); break
            else:
                groups.append([(x, y0, y1)])
        return [(int(sum(s_[0] for s_ in gr) / len(gr)), max(min(s_[1] for s_ in gr) - 22, 100), len(gr)) for gr in groups]

    def special_nodes(self, frame):
        """Special dungeons you have BEATEN on the zone map: [(x, y)]. Their picture is a red skull pile: two glowing red eyes side by side
        (two blobs ~24 px apart); underneath is a star - gold = beaten, dark = not beaten yet (those are not returned)."""
        img = frame.img.astype(int)
        r, g, b = img[..., 0], img[..., 1], img[..., 2]
        red = ((r > 150) & (g < 70) & (b < 80) & (r - g > 100)).astype(np.uint8)
        red[:100] = 0; red[:, :60] = 0; red[:, 740:] = 0
        gold = (r >= 225) & (g >= 140) & (g <= 225) & (b <= 90)
        n, _, st, _ = cv2.connectedComponentsWithStats(red, connectivity=8)
        eyes = [(st[i][0] + st[i][2] / 2.0, st[i][1] + st[i][3] / 2.0) for i in range(1, n)
                if 150 <= st[i][4] <= 420 and 10 <= st[i][2] <= 28 and 12 <= st[i][3] <= 28]
        found = []
        for i, (x1, y1) in enumerate(eyes):
            for x2, y2 in eyes[i + 1:]:
                if 16 <= abs(x1 - x2) <= 44 and abs(y1 - y2) <= 8:
                    cx, cy = int((x1 + x2) / 2), int((y1 + y2) / 2)
                    under = gold[cy + 14:cy + 62, max(0, cx - 34):cx + 34]
                    if int(under.sum()) >= 100 and all(abs(cx - f[0]) > 30 or abs(cy - f[1]) > 30 for f in found):
                        found.append((cx, cy))
        return found

    def level_nodes(self, frame):
        """Every level on the zone map, left to right: [(click_x, click_y, template or None, template position)]."""
        nodes = [(x, y, None, None) for (x, y, n) in self.star_levels(frame)]
        for name in ("dungeons.boss", "dungeons.star", "dungeons.bStar", "dungeons.level"):
            t = self.tpl.get(name)
            for p in t.find_all(frame, margin=None):
                if p[0] < 40 or p[1] < 90:
                    continue
                cx, cy = t.center_of(p)
                if not any(abs(cx - n[0]) < 35 and abs(cy - n[1]) < 70 for n in nodes):
                    nodes.append((cx, cy, t, p))
        return sorted(nodes, key=lambda n: (n[0], n[1]))

    def quest_goto_zone(self, frame, zone):
        """Quest override: walk to zone number `zone` (1 = the first). Left to the first zone, then right. True = clicked an arrow."""
        q, now = self._qz, time.time()
        if now - q["seen"] > 20:                                 # a fresh visit to the zone screen
            q.update(stage="left", n=0, steps=0)
        q["seen"] = now
        if q["stage"] == "done":
            return False
        q["steps"] += 1
        if q["steps"] > 80:
            log("Quest override: could not reach zone %d (gave up after 80 arrow clicks) -> using the normal rule" % zone)
            q["stage"] = "done"
            return False
        left, right = self.zone_arrow(frame, "L"), self.zone_arrow(frame, "R")
        arrow = None
        if q["stage"] == "left":
            if left:
                arrow = left
            else:
                q["stage"], q["n"] = "right", 0
        if q["stage"] == "right":
            if q["n"] < zone - 1 and right:
                q["n"] += 1
                arrow = right
            else:
                q["stage"] = "done"
                log("Quest override: zone %d reached%s" % (q["n"] + 1, "" if q["n"] == zone - 1 else " (the last zone that is unlocked)"))
                return False
        fp = self.zone_fp(frame)
        self.say("Quest override: going to zone %d (%s)" % (zone, "first walking back to zone 1" if q["stage"] == "left" else "zone %d of %d" % (q["n"] + 1, zone)), every=3)
        self.game.click(*arrow)
        for _ in range(10):                                      # wait until the title banner changes
            time.sleep(0.4)
            if not self.same_zone(fp, self.zone_fp(self.game.screenshot())):
                break
        time.sleep(0.4)
        return True

    def quest_screen(self, frame, active):
        spec = TASKS["quest"]
        if self.tpl.get("globally.auto-green").find(frame, AFK_MARGIN):       # a battle, not the zone map: never press ESC here
            return False
        if not active:
            self.say("Quest zone screen is open but Quest is paused -> closing it (red X, then ESC)", every=10)
            self.close_zone_map()
            return True
        qz, qd = int(self.profile.get("quest_zone", 0) or 0), int(self.profile.get("quest_dungeon", 0) or 0)
        override_cands = None
        if qz and self.quest_goto_zone(frame, qz):
            return True                                          # clicked a zone arrow: look again
        if qd:
            nodes = self.level_nodes(frame)
            if 1 <= qd <= len(nodes):
                x, y, t_, p_ = nodes[qd - 1]
                override_cands = [((x, y) if t_ is None else p_, "override: dungeon %d of %d in this zone (left to right)" % (qd, len(nodes)), t_)]
            else:
                self.say("Quest override: dungeon %d wanted but only %d level(s) found here -> using the normal rule" % (qd, len(nodes)), every=30)
        # Farm the most recent dungeon you have already beaten: a level with gold stars under it.
        # Walk back through the zones until one has such a level. Levels without stars (e.g. the bright
        # flag at the edge of your progress) are never entered while stars can be found.
        cands = []
        for (x, y, n) in self.star_levels(frame):
            if not any(abs(x - q[0]) < 15 and abs(y - q[1]) < 15 for q in self.quest_bad):
                cands.append(((x, y), "beaten level (%d gold star%s)" % (n, "" if n == 1 else "s"), None))
        cands.sort(key=lambda c: -int(c[1].split("(")[1][0]))
        if int(self.profile.get("quest_special", 0) or 0) and not qd:
            # A SPECIAL dungeon (a skull pile: Demiroth, Oblitaran, Grimlyn ...) is unlocked once every regular dungeon of the zone has 3 stars.
            # Beaten = one GOLD star under it (it has no heroic); not beaten yet = a dark star: those are never entered (a waste of energy).
            def far(p, q, d=15):
                return not (abs(p[0] - q[0]) < d and abs(p[1] - q[1]) < d)
            sp = [p for p in self.special_nodes(frame) if all(far(p, q) for q in self.quest_bad)]
            counts = [int(c[1].split("(")[1][0]) for c in cands]
            how = "skull pile with a gold star"
            if not sp:                                     # second way: the one 1-star level among 3-star ones
                ones = [c for c in cands if c[1].startswith("beaten level (1 ")]
                if len(ones) == 1 and len(cands) >= 2 and all(c is ones[0] or c[1].startswith("beaten level (3 ") for c in cands):
                    sp, how = [ones[0][0]], "the only 1-star level among 3-star ones"
            log("Quest special-dungeon check: levels with gold stars %s -> %s" % (counts or "none", ("special dungeon at %d,%d (%s)" % (sp[0] + (how,))) if sp else "no beaten special dungeon here"))
            if sp:
                cands = [(sp[0], "special dungeon (%s)" % how, None)] + [c for c in cands if not (abs(c[0][0] - sp[0][0]) < 40 and abs(c[0][1] - sp[0][1]) < 60)]
        self.zone_seen = time.time()
        if override_cands:
            cands = override_cands
        elif qz and not cands and not qd:
            pass                                                 # zone override without a dungeon: normal rule, inside that zone
        if not cands and not qz and (self.quest_bad or self.zone_trail) and (self.star_levels(frame) or self.special_nodes(frame)):
            # Levels with gold stars ARE here, but every one of them already failed this round (e.g. not enough energy to open it):
            # walking back through the zones would find nothing new - pause Quest and close the map instead.
            log("Quest: every beaten level in this zone was already tried and did not start (%d tried) -> pausing Quest, closing the map" % len(self.quest_bad))
            self.block("quest", 20, "no level could be started (energy?)")
            self.close_zone_map()
            return True
        if not cands and self.zone_steps < self.ZONE_STEPS_MAX and not qz:
            arrow = self.zone_arrow(frame, "L")
            if arrow:
                fp = self.zone_fp(frame)
                diffs = [float(np.abs(fp - q).mean()) for q in self.zone_trail]
                if diffs and min(diffs) < 6.0 and getattr(self, "_zone_retry", 0) < 2:
                    self._zone_retry = getattr(self, "_zone_retry", 0) + 1       # the click may simply have been missed: try the arrow again first
                    log("Quest: the zone banner still looks like one I saw (difference %.1f) -> clicking the left arrow again (%d of 2)" % (min(diffs), self._zone_retry))
                    self.game.click(*arrow)
                    time.sleep(2)
                    return True
                self._zone_retry = 0
                if any(self.same_zone(fp, q) for q in self.zone_trail):
                    log("Quest: walking back brought me to a zone I already looked at - the arrow is not moving backwards. Giving up on Quest for now.")
                    self.save_frame(frame, "unknown", "quest_zone_loop", "zone arrow walk went round in a circle (zone already visited at step %d)" % self.zone_steps, 300)
                    self.zone_trail, self.zone_steps = [], 0
                    self.block("quest", 20, "zone arrows loop")
                    self.close_zone_map()
                    return True
                self.zone_trail.append(fp)
                self.zone_steps += 1
                log("Quest: no beaten level (gold stars) in this zone -> going back one zone (step %d)" % self.zone_steps)
                self.game.click(*arrow)
                for _ in range(10):                          # wait until the title banner changes (zone slide takes a moment)
                    time.sleep(0.4)
                    if not self.same_zone(fp, self.zone_fp(self.game.screenshot())):
                        break
                else:
                    log("Quest: the zone did not change after clicking the left arrow")
                time.sleep(0.4)
                return True
        if not cands and not self.zone_arrow(frame, "L"):         # first zone and no stars at all: fall back to templates
            for name, what in (("dungeons.boss", "boss level"), ("dungeons.star", "level with stars"),
                               ("dungeons.bStar", "level without stars"), ("dungeons.level", "any level")):
                t = self.tpl.get(name)
                for p in t.find_all(frame, margin=None):
                    if p[0] >= 40 and p[1] >= 90 and not any(abs(p[0] - q[0]) < 15 and abs(p[1] - q[1]) < 15 for q in self.quest_bad + [c[0] for c in cands]):
                        cands.append((p, what, t))
        log("Quest zone screen: %d level(s) to try%s" % (len(cands), ", skipping %d that did not work" % len(self.quest_bad) if self.quest_bad else ""))
        entered = special_done = False
        for p, what, t in cands[:6]:
            log("Quest: clicking %s at %d,%d" % (what, p[0], p[1]))
            self.game.click(*p) if t is None else self.tap(t, p)
            time.sleep(2)
            f2 = self.game.screenshot()
            if what.startswith("special"):
                if self.difficulty_screen(f2):                       # NORMAL / HARD / HEROIC: a regular level, not the special one
                    log("Quest: that level has the difficulty picker, so it is not a special dungeon -> closing it and using the normal rule")
                    self.game.press("escape")
                    time.sleep(1)
                    self.quest_bad.append(p)
                    return True
                te, pe = self.see("dungeons.enter", f2, None)
                if pe:                                              # the special dungeon's page: bonuses + ENTER only (then the TEAM page: ACCEPT)
                    log("Quest: special dungeon opened -> pressing 'enter'")
                    self.tap(te, pe)
                    entered = special_done = True
                    break
            f2 = self.game.screenshot()
            for name in ("dungeons.energy", "dungeons.heroic-energy"):
                te, pe = self.see(name, f2)
                if pe:
                    log("Quest: level opened -> pressing '%s'" % name.split(".", 1)[1])
                    self.tap(te, pe)
                    entered = True
                    break
            if entered:
                break
            self.quest_bad.append(p)
            log("Quest: that level did not open (locked?) - will not try it again this round")
            self.save_frame(f2, "unknown", "quest_level_no_open", "clicked a level at %d,%d: neither the energy nor the heroic button was recognised" % p, 600)
        if entered:
            time.sleep(1)
            f3 = self.game.screenshot()
            te, pe = self.see("dungeons.enter", f3, None)
            if special_done:
                pass                                                # ENTER was already pressed on the special dungeon's page
            elif pe:
                self.tap(te, pe)
            else:
                mode = {1: "dungeons.normal", 2: "dungeons.hard", 3: "dungeons.heroic"}.get(self.profile["quest_mode"], "dungeons.heroic")
                tm, pm = self.see(mode, f3)
                if pm:
                    log("Quest: choosing %s mode" % mode.split(".", 1)[1])
                    self.tap(tm, pm)
                    self._mode_click_at, self._mode_retries = time.time(), 0
            self.nav_block_until = time.time() + self.NAV_GRACE
            self.zone_steps, self.zone_trail = 0, []
            self.entered_log = [e for e in getattr(self, "entered_log", []) if time.time() - e[0] < 60] + [(time.time(), p)]
            if len(self.entered_log) >= 4 and all(abs(e[1][0] - p[0]) < 15 and abs(e[1][1] - p[1]) < 15 for e in self.entered_log[-4:]):
                log("Quest: this level keeps opening and returning to the zone map without starting - skipping it")
                self.quest_bad.append(p)
                self.save_frame(frame, "unknown", "quest_level_loops", "level at %s was clicked 4 times in a minute and the zone map kept coming back" % (p,), 300)
            else:
                self.quest_fail = 0
            self.acted()
            return True
        self.zone_steps, self.zone_trail = 0, []
        self.quest_fail += 1
        log("Quest: could not enter any level (%d of 3 attempts)" % self.quest_fail)
        self.game.press("escape")
        if self.quest_fail >= 3:
            log("Quest: giving up for now")
            self.save_frame(frame, "unknown", "quest_zone_stuck", "Quest zone screen: no level could be entered in 3 rounds (tried and failed: %s)" % self.quest_bad, 300)
            self.block("quest", 20, "no level could be entered")
        return True

    # ---- Raid and World Boss ---------------------------------------------------- #
    ARROW_L, ARROW_R = (152, 297), (645, 297)          # the yellow arrows beside the Raid / World Boss pictures
    DOTS_Y = 442                                       # row of little page dots under the picture

    def carousel_dots(self, frame):
        """(number of dots, 1-based index of the green one) from the page dots under a Raid / World Boss picture, or None.
        New raids / bosses only add dots, so nothing here depends on how many there are."""
        row = frame.img[self.DOTS_Y, 150:660].astype(int)
        green = (row[:, 1] > 120) & (row[:, 0] < 90) & (row[:, 1] - row[:, 0] > 60)
        grey = (np.abs(row[:, 0] - row[:, 1]) < 12) & (np.abs(row[:, 1] - row[:, 2]) < 12) & (row[:, 0] > 120) & (row[:, 0] < 175)
        dots = []
        for mask, is_green in ((green, True), (grey, False)):
            edges = np.diff(np.concatenate(([0], mask.astype(np.int8), [0])))
            for a, b in zip(np.nonzero(edges == 1)[0], np.nonzero(edges == -1)[0]):
                if 7 <= b - a <= 12:
                    dots.append((int((a + b) // 2), is_green))
        dots.sort()
        if len(dots) < 2 or sum(1 for _, g in dots if g) != 1:
            return None
        return len(dots), 1 + [g for _, g in dots].index(True)

    def flip_to(self, frame, want, label):
        """Click the arrow once towards page `want`. Returns True if it clicked, False if already there / unknown."""
        dots = self.carousel_dots(frame)
        if not dots:
            return False
        n, cur = dots
        if want > n:
            self.say("%s: page %d wanted but only %d are unlocked -> using the last one" % (label, want, n), every=60)
            want = n
        if cur == want:
            return False
        self.say("%s: page %d of %d is showing, going to page %d" % (label, cur, n, want), every=5)
        self.game.click(*(self.ARROW_R if want > cur else self.ARROW_L))
        time.sleep(1.0)
        return True

    def raid_screens(self, frame):
        t = self.tpl
        if t.get("raid.raid").find(frame) and t.get("raid.summon").find(frame, None):          # raid picture + SUMMON
            want = self.profile["raid_level"]
            if want <= 0:
                log("Raid: no raid level in your profile (ig.user.raid.level, or --raid-level) -> skipping Raid for now")
                self.block("raid", 1440, "no raid level set")
                self.game.esc(2)
                return True
            if self.flip_to(frame, want, "Raid"):
                return True
            sm = t.get("raid.summon")
            self.say("Raid: pressing SUMMON", every=10)
            self.game.click(*sm.center_of(sm.find(frame, None)))
            time.sleep(1.5)
            return True
        names = {1: "raid.normal", 2: "raid.hard", 3: "raid.heroic"}
        found = {k: t.get(n).find(frame, AFK_MARGIN) for k, n in names.items()}               # difficulty buttons, each at its own spot
        if all(found.values()) and abs(found[1][1] - found[2][1]) <= 4 and abs(found[2][1] - found[3][1]) <= 4 \
                and found[1][0] < found[2][0] < found[3][0]:                                    # ... in one row, left to right
            mode = self.profile["raid_mode"] if self.profile["raid_mode"] in names else 3
            tm = t.get(names[mode])
            self.say("Raid: choosing %s" % names[mode].split(".")[1], every=10)
            self.game.click(*tm.center_of(found[mode]))
            time.sleep(1.5)
            return True
        return False

    def wb_private_now(self):
        """Private (solo) party? Yes if the profile says so, or after a public party waited too long for other players."""
        return bool(self.profile.get("wb_private", True)) or self._wb_solo

    @staticmethod
    def ready_slots(frame):
        """How many party slots have the green 'ready' border? (a tall green line on the slots' left edge)"""
        img = frame.img.astype(int)
        reg = img[150:450, 101:106]
        g = ((reg[..., 1] >= 130) & (reg[..., 1] - reg[..., 0] >= 40) & (reg[..., 1] - reg[..., 2] >= 35)).any(axis=1)
        count, run, gap = 0, 0, 0
        for v in list(g) + [False] * 4:
            if v:
                run, gap = run + 1, 0
            else:
                gap += 1
                if gap > 3 and run:
                    count += 1 if run >= 30 else 0
                    run = 0
        return count

    def boss_lobby(self, frame):
        """The party page (START / READY / UNREADY). Lead: wait for the wanted number of ready players, then START.
        Not lead: press READY, then wait for the lead to start. Waits are long; when they run out the bot leaves and
        (as lead) tries again as a private solo party."""
        t, now = self.tpl, time.time()
        start = t.get("world-boss.start-boss").find(frame, AFK_MARGIN)
        ready = t.get("world-boss.lobby-ready").find(frame, AFK_MARGIN)
        unready = t.get("world-boss.lobby-unready").find(frame, AFK_MARGIN)
        if not (start or ready or unready):
            if self._lobby and now - self._lobby["seen"] > 25:
                self._lobby = None
            return False
        L = self._lobby = self._lobby if self._lobby and now - self._lobby["seen"] < 25 else dict(since=now, said=0.0, readied=0.0)
        L["seen"] = now
        minutes = (now - L["since"]) / 60.0
        boss = self.profile.get("wb_boss", "")
        others = self.ready_slots(frame)
        if start:
            if self.wb_private_now():
                need = 0
            else:
                need = max(1, min(int(self.profile.get("wb_players", 1)), WB_MAX_PLAYERS.get(boss, 3) - 1))
            if others >= need:
                self.say("World Boss: %s -> START" % ("solo (private)" if need == 0 else "%d other player(s) ready" % others), every=0)
                self.game.click(*t.get("world-boss.start-boss").center_of(start))
                self._lobby, self._wb_solo = None, False
                self.nav_block_until = time.time() + self.NAV_GRACE
                time.sleep(2)
                return True
            limit = float(self.profile.get("wb_wait", 15))
            if minutes >= limit:
                log("World Boss: only %d of %d other player(s) ready after %d min -> leaving, then trying again as a private solo party" % (others, need, limit))
                self._wb_solo, self._lobby = True, None
                self.game.esc(4, gap=1.0)
                return True
            if now - L["said"] > 30:
                L["said"] = now
                log("World Boss: waiting for other players - %d of %d ready (%.0f of %d min)" % (others, need, minutes, limit))
            return True
        if ready:
            self.say("World Boss: not the party lead -> pressing READY", every=0)
            self.game.click(*t.get("world-boss.lobby-ready").center_of(ready))
            L["readied"] = now
            time.sleep(1.5)
            return True
        limit = max(30.0, float(self.profile.get("wb_wait", 15)) * 2)           # the lead can take a long time to press START
        if minutes >= limit:
            log("World Boss: the party lead has not started for %d min -> leaving" % limit)
            self._lobby = None
            self.game.esc(4, gap=1.0)
            self.block("world-boss", 10, "the lead never started")
            return True
        if now - L["said"] > 60:
            L["said"] = now
            log("World Boss: READY - waiting for the party lead to press START (%.0f of %d min)" % (minutes, limit))
        return True

    def boss_screens(self, frame):
        t = self.tpl
        if self.boss_lobby(frame):
            return True
        title = t.get("world-boss.world-boss").find(frame)
        summon_pic = t.get("world-boss.summon-boss").find(frame, None)
        if title and summon_pic:                                                                # boss picture + SUMMON
            want = self.profile["wb_boss"]
            if want not in WB_NAMES:
                log("World Boss: no boss chosen (profile ig.user.world-boss.name=%s, or --boss) -> skipping World Boss for now" % "|".join(WB_NAMES))
                self.block("world-boss", 1440, "no boss chosen")
                self.game.esc(3, gap=1.0)
                return True
            if t.get("boss." + want).find(frame, None):
                self.say("World Boss: %s is showing -> SUMMON" % want, every=10)
                self._boss_flips = 0
                self._wb = dict(tier=False, mode=False, tier_tries=0, mode_tries=0, private_tries=0, mode_open_at=0)
                self.game.click(*t.get("world-boss.summon-boss").center_of(summon_pic))
                time.sleep(1.5)
                return True
            self._boss_flips = getattr(self, "_boss_flips", 0) + 1
            dots = self.carousel_dots(frame)
            if self._boss_flips > (dots[0] + 1 if dots else 14):
                log("World Boss: went through every page and never saw '%s' (not unlocked yet?) -> skipping World Boss" % want)
                self.evidence(frame, "unknown", "boss_not_found", "boss '%s' not found in the World Boss pages" % want, 300)
                self._boss_flips = 0
                self.block("world-boss", 60, "boss not unlocked")
                self.game.esc(3, gap=1.0)
                return True
            self.say("World Boss: looking for '%s' (page %s)" % (want, dots[1] if dots else "?"), every=5)
            self.game.click(*self.ARROW_R)
            time.sleep(0.9)
            return True
        sp = t.get("world-boss.summon-party")
        if sp.find(frame):                                                                      # list of parties + SUMMON (its title is a smaller font than the picture screen's)
            self.say("World Boss: no party wanted -> SUMMON your own", every=10)
            self.game.click(*sp.center_of(sp.find(frame)))
            time.sleep(1.2)
            return True
        sd = t.get("world-boss.summon-tier-diff")
        pos = sd.find(frame, AFK_MARGIN)
        tier, mode = self.profile["wb_tier"], self.profile["wb_mode"]
        w = self._wb                                                                            # progress through the Summon dialog
        if pos:                                                                                 # the Summon dialog (tier / difficulty / private)
            cx, cy = sd.center_of(pos)                                                          # centre of its SUMMON button
            if tier and not w["tier"]:
                cur = self.dialog_tier_is(frame, cx, cy, tier)
                if cur:
                    self.say("World Boss: tier %d is already set -> leaving it" % tier, every=30)
                    w["tier"] = True
            if mode and not w["mode"]:
                cur_mode = self.dialog_mode(frame, cx, cy)
                if cur_mode == mode:
                    self.say("World Boss: %s is already set -> leaving it" % self.MODE_NAMES[mode], every=30)
                    w["mode"] = True
            if tier and not w["tier"] and w["tier_tries"] < 3:
                w["tier_tries"] += 1
                self.say("World Boss: opening the tier list (want tier %d)" % tier, every=5)
                self.game.click(cx, cy - 185)                                                   # the little arrow beside 'TIER n'
                time.sleep(1.0)
                return True
            if mode and not w["mode"] and w["mode_tries"] < 3:
                w["mode_tries"] += 1
                self.say("World Boss: opening the difficulty list (want %s)" % self.MODE_NAMES[mode], every=5)
                self.game.click(cx, cy - 107)                                                   # the arrow beside the difficulty
                w["mode_open_at"] = time.time()
                time.sleep(1.0)
                return True
            want_private = self.wb_private_now()
            ticked = self.private_ticked(frame, cx, cy)
            if ticked != want_private and w["private_tries"] < 2:
                w["private_tries"] += 1
                self.say("World Boss: %s PRIVATE" % ("ticking" if want_private else "un-ticking"), every=5)
                self.game.click(cx - 137, cy - 54)
                time.sleep(0.8)
                return True
            self.say("World Boss: SUMMON (%s%s%s)" % ("private, solo" if want_private else "public, others can join",
                                                    ", tier %d" % tier if tier else "", ", " + self.MODE_NAMES[mode] if mode else ""), every=10)
            self._lobby = None
            self._wb = dict(tier=False, mode=False, tier_tries=0, mode_tries=0, private_tries=0, mode_open_at=0)
            self.game.click(cx, cy)
            time.sleep(2)
            return True
        if tier and not w["tier"]:                                                              # the tier list
            tt = t.get("tier.%d" % tier) if (self.tpl.folder / ("tier.%d.png" % tier)).exists() else None
            if not tt:
                self.say("World Boss: no template for tier %d (make one with `template ... tier.%d`) - leaving the tier as it is" % (tier, tier), every=60)
                w["tier"] = True
            else:
                tp_ = tt.find(frame, None)
                if tp_ and not t.get("world-boss.world-boss").find(frame) and self.tier_row_selected(frame, tp_):
                    self.say("World Boss: tier %d is already selected -> closing the list" % tier, every=5)
                    w["tier"], w["tier_at"], w["tier_clicks"] = True, time.time(), 2
                    self._wb_tier_cache = tier
                    self.game.press("escape")
                    time.sleep(1.0)
                    return True
                if tp_ and not t.get("world-boss.world-boss").find(frame):
                    self.say("World Boss: choosing tier %d" % tier, every=5)
                    self.game.click(*tt.center_of(tp_))
                    w["tier"], w["tier_at"] = True, time.time()
                    self._wb_tier_cache = tier
                    time.sleep(1.0)
                    return True
        if w["tier"] and w.get("tier_at") and time.time() - w["tier_at"] > 2 and w.get("tier_esc", 0) < 3 and self.tier_list_open(frame):
            if w.get("tier_clicks", 1) < 2:                           # maybe the click was too early: one more try, on the row itself
                tt = self.tpl.get("tier.%d" % tier) if (self.tpl.folder / ("tier.%d.png" % tier)).exists() else None
                tp2 = tt.find(frame, None) if tt else None
                if tp2:
                    w["tier_clicks"], w["tier_at"] = 2, time.time()
                    self.say("World Boss: the tier list is still open -> clicking tier %d once more" % tier, every=5)
                    self.game.click(*tt.center_of(tp2))
                    time.sleep(1.0)
                    return True
            w["tier_esc"] = w.get("tier_esc", 0) + 1                  # the wanted tier was already selected: clicking it leaves the list open
            self.say("World Boss: the tier list is still open (tier %d was probably already selected) -> ESC" % tier, every=5)
            self.game.press("escape")
            time.sleep(1.0)
            return True
        if w["mode"] and w["mode_open_at"] and self.difficulty_list_open(frame) and time.time() - w.get("mode_clicked_at", 0) > 2 and w.get("mode_esc", 0) < 3:
            w["mode_esc"] = w.get("mode_esc", 0) + 1                                              # the chosen row was already selected: the list stays open
            self.say("World Boss: difficulty list is still open -> ESC", every=5)
            self.game.press("escape")
            time.sleep(1.0)
            return True
        if mode and not w["mode"] and w["mode_open_at"] and time.time() - w["mode_open_at"] < 15:
            rows = {1: 156, 2: 216, 3: 276}                                                     # NORMAL / HARD / HEROIC rows of the difficulty list
            if self.difficulty_list_open(frame):
                self.say("World Boss: choosing %s" % self.MODE_NAMES[mode], every=5)
                self.game.click(385, rows[mode])
                w["mode"] = True
                w["mode_clicked_at"] = time.time()
                time.sleep(1.0)
                return True
        return False

    _wb_tier_cache = 0                      # the tier this bot last chose (used when there is no picture of the tier label)

    def dialog_tier_is(self, frame, cx, cy, tier):
        """Is `tier` the tier shown in the Summon dialog? Uses wbsum.tier.<n> (a picture of that dialog's label) when it exists;
        without one it trusts what the bot chose last time. Make one: python bhbot.py template <shot.png> wbsum.tier.NN --rect=... --ink FFFFFF"""
        if (self.tpl.folder / ("wbsum.tier.%d.png" % tier)).exists():
            p = self.tpl.get("wbsum.tier.%d" % tier).find(frame, None)
            return bool(p and abs(p[0] - (cx - 137)) < 40 and abs(p[1] - (cy - 192)) < 40)
        others = [n for n in range(1, 60) if n != tier and (self.tpl.folder / ("wbsum.tier.%d.png" % n)).exists()]
        if any(self.tpl.get("wbsum.tier.%d" % n).find(frame, None) for n in others):
            return False                                                   # a different, known tier is showing
        return self._wb_tier_cache == tier

    @staticmethod
    def dialog_mode(frame, cx, cy):
        """Difficulty written in the Summon dialog's box: 1 Normal (lavender), 2 Hard (pink), 3 Heroic (yellow), or None."""
        reg = frame.img[cy - 107 - 18:cy - 107 + 18, cx - 130:cx + 25].astype(int)
        def count(rgb):
            return int((np.abs(reg - np.array(rgb)).max(axis=2) <= 14).sum())
        counts = {1: count((147, 158, 244)), 2: count((255, 128, 125)), 3: count((255, 255, 0))}
        best = max(counts, key=counts.get)
        return best if counts[best] >= 40 else None

    MODE_NAMES = {1: "Normal", 2: "Hard", 3: "Heroic"}
    _wb = dict(tier=False, mode=False, tier_tries=0, mode_tries=0, private_tries=0, mode_open_at=0)

    def private_ticked(self, frame, cx, cy):
        """Is the green tick in the PRIVATE box of the Summon dialog?"""
        x, y = cx - 137, cy - 54
        reg = frame.img[y - 14:y + 14, x - 14:x + 14].astype(int)
        return int(((reg[..., 1] > 150) & (reg[..., 0] < 130) & (reg[..., 2] < 130)).sum()) >= 12

    @staticmethod
    def tier_row_selected(frame, tpos):
        """The row of the tier that is currently selected has a lighter background (58,68,80) than the others (40,47,55)."""
        r, g, b = (int(v) for v in frame.img[tpos[1] + 5, 285])
        return 52 <= r <= 66 and 62 <= g <= 76 and 74 <= b <= 90

    def tier_list_open(self, frame):
        """The TIER list: at least two of the 'TIER nn' rows are on screen."""
        names = [n for n in range(1, 60) if (self.tpl.folder / ("tier.%d.png" % n)).exists()]
        return sum(1 for n in names if self.tpl.get("tier.%d" % n).find(frame, None)) >= 2

    def difficulty_list_open(self, frame):
        """The DIFFICULTY list: three rows of coloured words (lavender NORMAL, pink HARD, yellow HEROIC)."""
        img = frame.img.astype(int)
        yellow = img[266:288, 330:445]
        pink = img[206:228, 340:430]
        yes_y = int(((yellow[..., 0] > 230) & (yellow[..., 1] > 230) & (yellow[..., 2] < 90)).sum()) >= 20
        yes_p = int(((pink[..., 0] > 220) & (pink[..., 1] < 150) & (pink[..., 2] < 150)).sum()) >= 15
        return yes_y and yes_p

    # ---- Trials difficulty override ------------------------------------------ #
    TR_LIST_Y0, TR_LIST_Y1 = 140, 440          # the visible part of the DIFFICULTY list
    TR_ROW_PITCH = 60

    def tr_row_kind(self, frame, tier_y):
        """'cyan' (set + mythic drops) or 'yellow' (legendary drops): the colour of the number range above a TIER label."""
        reg = frame.img[max(tier_y - 22, 0):tier_y - 3, 290:480].astype(int)
        cyan = int(((reg[..., 0] < 60) & (reg[..., 1] > 200) & (reg[..., 2] > 200)).sum())
        yellow = int(((reg[..., 0] > 230) & (reg[..., 1] > 220) & (reg[..., 2] < 90)).sum())
        return "cyan" if cyan > yellow else "yellow" if yellow > 20 else None

    def tr_rows(self, frame):
        """Rows of the tier list on screen: [(tier_label_y, kind, tier number or None)]."""
        known = {}
        for n in range(1, 60):
            if (self.tpl.folder / ("trials.tier.%d.png" % n)).exists():
                for p in self.tpl.get("trials.tier.%d" % n).find_all(frame, margin=None):
                    known[p[1]] = n
        ys = [p[1] for p in self.tpl.get("trials.tier-word").find_all(frame, margin=None)]
        ys += [y for y in known if not any(abs(y - q) <= 3 for q in ys)]       # one-digit labels are centred differently: their own templates find them
        rows = []
        for y in sorted(ys):
            tier = next((n for ky, n in known.items() if abs(ky - y) <= 3), None)
            rows.append((y, self.tr_row_kind(frame, y), tier))
        return rows

    def tr_number_rows(self, frame):
        """Rows of the exact-number list: y of each yellow/cyan number, top to bottom."""
        reg = frame.img[145:445, 330:460].astype(int)
        mx, mn = reg.max(axis=2), reg.min(axis=2)
        m = (((reg[..., 0] > 230) & (reg[..., 1] > 220) & (reg[..., 2] < 90)) | ((reg[..., 0] < 60) & (reg[..., 1] > 200) & (reg[..., 2] > 200))
             | ((mx > 200) & (mx - mn > 90))).any(axis=1)             # yellow, light blue, or any other bright coloured number (tiers 1-4 are red / blue / green)
        bands, start = [], None
        for i, v in enumerate(list(m) + [False]):
            if v and start is None:
                start = i
            elif not v and start is not None:
                if i - start >= 8:
                    bands.append(145 + (start + i) // 2)
                start = None
        return bands

    def trials_difficulty(self, frame, which="trials"):
        """Trials / Gauntlet page -> arrow beside DIFFICULTY -> tier list (light blue = set+mythic, yellow = legendary) -> exact number list."""
        tier = int(self.profile.get(which + "_tier", 0) or 0)
        if not tier:
            return False
        label = which.capitalize()
        S, now, t = (self._tr if which == "trials" else self._gt), time.time(), self.tpl
        title = t.get("trials.difficulty-title").find(frame, None)
        play = t.get(which + ".play").find(frame, None)
        if not title and not play:
            if now - S["seen"] > 30:
                S.update(done=False, tries=0, stage="", scrolls=0, top=False, ups=0, last_ys=None, last_first=None, rate=None, hi=None, last_k=0, fallback=False, bad=0, closes=0)     # left the Trials page: next visit starts over
            return False
        S["seen"] = now
        if S["done"]:
            if title and now - S["clicked"] > 4:
                self.say(label + ": the difficulty list is still open -> ESC", every=10)
                self.game.press("escape")
                return True
            return False
        if not title:
            if S["stage"] == "numbers":                                   # the list closed by itself: the choice was taken
                S["done"], S["clicked"] = True, now
                log(label + ": difficulty chosen")
                return False
            S["tries"] += 1
            if S["tries"] > 3:
                log(label + ": could not open the difficulty list -> playing with the current difficulty")
                S["done"] = True
                return False
            cx, cy = t.get(which + ".play").center_of(play)
            self.say("%s: opening the difficulty list (want tier %d, %s drops)" % (label, tier, "legendary" if self.profile[which + "_kind"] == "yellow" else "set+mythic"), every=0)
            self.game.click(cx + 63, cy + 117)                           # the little arrow beside the difficulty number
            S["stage"], S["scrolls"], S["top"], S["ups"] = "tiers", 0, False, 0
            time.sleep(1.2)
            return True
        kind = self.profile[which + "_kind"]
        rows = self.tr_rows(frame)
        if rows:                                                          # --- the tier list
            S["stage"] = "tiers"
            for y, k, n in rows:
                if n == tier and (k == kind or tier <= 4):
                    self.say(label + ": choosing tier %d, %s drops" % (tier, "legendary" if kind == "yellow" else "set+mythic"), every=0)
                    self.game.click(387, y - 6)
                    S["stage"], S["top"], S["ups"], S["clicked"] = "numbers", False, 0, now
                    time.sleep(1.2)
                    return True
            # list order: highest tier first, light blue row above yellow; tiers 1-4 have a single row each
            pos = lambda tr, kd: -(tr + 4) if tr <= 4 else -tr * 2 + (0 if kd == "cyan" else 1)
            known = [(y, k, n) for y, k, n in rows if n and (k or n <= 4)]
            ref = min(known, key=lambda r: abs(pos(r[2], r[1]) - pos(tier, kind)), default=None)
            S["scrolls"] += 1
            if S["scrolls"] > 30:
                log(label + ": could not find tier %d in the list -> playing with the current difficulty" % tier)
                S["done"] = True
                self.game.press("escape")
                return True
            if ref:
                y_t = ref[0] + self.TR_ROW_PITCH * (pos(tier, kind) - pos(ref[2], ref[1]))
                if self.TR_LIST_Y0 + 20 <= y_t <= self.TR_LIST_Y1:
                    self.say(label + ": choosing tier %d, %s drops (counted from tier %d)" % (tier, "legendary" if kind == "yellow" else "set+mythic", ref[2]), every=0)
                    self.game.click(387, y_t - 6)
                    S["stage"], S["top"], S["ups"], S["clicked"] = "numbers", False, 0, now
                    time.sleep(1.2)
                    return True
                direction = 1 if y_t > self.TR_LIST_Y1 else -1
            else:                                                         # no tier I know is visible: look from the top, then downwards
                if S["scrolls"] <= 3:
                    direction = -1
                else:
                    direction = 1
                    if S["scrolls"] == 4:
                        log(label + ": no tier label I know is on screen. Make a template for one (python bhbot.py template <screenshot.png> trials.tier.NN --rect=x,y,w,h --ink CCCCCC)")
            ys_now = tuple(r[0] for r in rows)
            at_top = direction < 0 and ys_now == S.get("last_ys")      # scrolling up changed nothing: this is the top of the list
            S["last_ys"] = ys_now
            seen_tiers = [r for r in rows if r[2]]
            if at_top and seen_tiers and max(r[2] for r in seen_tiers) < tier:
                # A hero only sees the tiers up to its own (a tier-2 hero has just tiers 1-2): take the highest tier the list offers
                best = max(r[2] for r in seen_tiers)
                cand = [r for r in seen_tiers if r[2] == best]
                row = next((r for r in cand if r[1] == kind), cand[0])
                log("%s: tier %d is not in this hero's list (highest is tier %d) -> choosing tier %d" % (label, tier, best, best))
                self.game.click(387, row[0] - 6)
                S["stage"], S["top"], S["ups"], S["clicked"] = "numbers", False, 0, now
                time.sleep(1.2)
                return True
            self.say(label + ": scrolling the list %s (looking for tier %d)" % ("down" if direction > 0 else "up", tier), every=0)
            self.game.scroll(397, 300, 120 * direction)
            time.sleep(0.6)
            return True
        # --- the exact-number list (highest / lowest value inside the chosen range)
        S["stage"] = "numbers"
        want_n = int(self.profile.get(which + "_number", 0) or 0)
        if want_n:                                                        # a number was asked for: the same list picker as Expedition
            self._bright_numbers = True
            try:
                return self.list_pick(S, frame, 0 if S["fallback"] else want_n, label + " difficulty", now)
            finally:
                self._bright_numbers = False
        want_top = self.profile.get(which + "_pick", "highest") != "lowest"
        if S["ups"] < 14:                                                 # wheel the list to its top (or bottom) first
            S["ups"] += 1
            self.game.scroll(397, 300, -400 if want_top else 400)
            time.sleep(0.4)
            return True
        bands = self.tr_number_rows(frame)
        if not bands:
            self.say(label + ": the number list shows no rows I can read -> ESC", every=10)
            S["done"] = True
            self.game.esc(2)
            return True
        y = bands[0] if want_top else bands[-1]
        self.say(label + ": choosing the %s difficulty value in that range" % ("highest" if want_top else "lowest"), every=0)
        self.game.click(387, y)
        S["done"], S["clicked"] = True, now
        time.sleep(1.2)
        return True

    # ---- invasion: play the wave you chose (default: the top one) ------------- #
    _DIGITS = None

    def digit_samples(self):
        if Afk._DIGITS is None:
            f = self.tpl.folder / "digits.json"
            raw = json.loads(f.read_text()) if f.is_file() else {}
            Afk._DIGITS = [(ch, np.array([c == "1" for c in bits]).reshape(14, 10)) for ch, lst in raw.items() for bits in lst]
        return Afk._DIGITS

    def read_rows(self, frame, y0=140, y1=445, x0=330, x1=450, min_h=13):
        """Numbers in a list box: [(y, value or None)] top to bottom (the reading itself lives in picker.py)."""
        return picker.read_rows(frame.img, self.digit_samples(), y0, y1, x0, x1, min_h, bright=getattr(self, "_bright_numbers", False))

    def wave_rows(self, frame, default_step=1):
        """Numbers of a number list rebuilt from their step: [(y, value)] (picker.py); the step ends up in self.list_step."""
        rows, self.list_step = picker.infer_rows(self.read_rows(frame), default_step)
        return rows

    def list_pick(self, S, frame, want, label, now):
        """One look at an open number list (Invasion WAVE, Expedition DIFFICULTY): wheel to the top, read the numbers, wheel toward `want`,
        click it. `want` 0 = the top row. The wheel step is measured, not assumed, so the same code serves lists that count by 1, 5 or
        1,000 and have 3 to 6 digits. S is the caller's state dict. Returns True when it did something."""
        S["seen"] = now
        rows = self.wave_rows(frame)

        def choose(y, why):
            self.say("%s: choosing %s" % (label, why), every=0)
            self.game.click(387, y)
            S.update(stage="", done=True, clicked=now, closes=0)
            time.sleep(1.2)
            return True

        if not S["top"]:                                              # 1) wheel to the very top (the list stops changing)
            first = rows[0][1] if rows else None
            if (first is not None and first == S["last_first"]) or S["ups"] >= 40:
                S["top"], S["rate"], S["hi"] = True, None, None
            else:
                S["last_first"] = first
                S["ups"] += 1
                self.game.scroll(397, 300, -600)
                time.sleep(0.4)
                return True
        if not rows:
            if S["bad"] < 3:
                S["bad"] += 1
                time.sleep(0.4)
                return True
            return choose(157, "the top row (the numbers could not be read)")
        hi, lo = rows[0][1], rows[-1][1]
        if not want:
            return choose(rows[0][0], "the top row (%d)" % hi)
        hit = next((y for y, v in rows if v == want), None)
        if not hit and lo < want < hi:                                    # between two rows (not on the list's step): the nearest row
            hit = min(rows, key=lambda r: abs(r[1] - want))[0]
        if hit:
            return choose(hit, "%d" % want)
        if S["hi"] is not None and S["last_k"]:                       # how far one wheel step moves the list
            moved = abs(S["hi"] - hi)
            if moved == 0:                                            # the list did not move: that was its end
                S["fallback"], S["top"], S["ups"], S["last_first"] = True, False, 0, None
                log("%s: %d is not in the list (it runs %d down to %d) -> taking the top one" % (label, want, hi, lo))
                return True
            S["rate"] = moved / S["last_k"]
        if want > hi:
            if S["top"] and S["scrolls"] == 0:                        # asked for more than the top wave
                log("%s: %d is higher than the top of the list (%d) -> taking the top one" % (label, want, hi))
                return choose(rows[0][0], "the top row (%d)" % hi)
            dist, sign = want - hi, -1
        else:
            dist, sign = lo - want, 1
        k = 1 if not S["rate"] else max(1, min(30, int(0.8 * dist / S["rate"])))
        S["scrolls"] += 1
        if S["scrolls"] > 60:
            S["fallback"], S["top"], S["ups"], S["last_first"] = True, False, 0, None
            log("%s: could not reach %d -> taking the top one" % (label, want))
            return True
        S["hi"], S["last_k"] = hi, k
        self.say("%s: scrolling to %d (list shows %d..%d)" % (label, want, hi, lo), every=0)
        self.game.scroll(397, 300, 120 * k * sign)
        time.sleep(0.5)
        return True


    # ---- expedition: portal map -> portal page -> difficulty ---------------------------- #
    EXP_PORTALS = (("googarum", "Googarum's portal"), ("twimbo", "Twimbo's portal"), ("svord", "Svord's portal"), ("x5", "X5-T34M's portal"))
    # left to right on the Hallowed Dimension map; other dimensions (or heroes that have not unlocked every portal) are told apart by the title
    # on the portal page, so the order is only the first guess

    def expedition_bars(self, frame):
        """The little green progress bars under each portal on the dimension map: [(x, y)] left to right (empty = not the map)."""
        img = frame.img.astype(int)
        x = img[55:100, 705:760]                                          # the red close button at the top right of the map
        if int(((x[..., 0] > 190) & (x[..., 1] < 90) & (x[..., 2] < 90)).sum()) < 150:
            return []
        reg = img[110:480, 90:700]
        m = ((reg[..., 1] > 170) & (reg[..., 0] < 130) & (reg[..., 2] < 130)).astype(np.uint8)
        n, _, st, _ = cv2.connectedComponentsWithStats(m)
        bars = [(int(st[i][0]) + 90 + int(st[i][2]) // 2, int(st[i][1]) + 110) for i in range(1, n)
                if 8 <= st[i][3] <= 10 and 10 <= st[i][2] <= 72 and st[i][4] >= 0.7 * st[i][2] * st[i][3]]
        return sorted(bars) if 1 <= len(bars) <= 8 else []

    def expedition_map_open(self, frame):
        return bool(self.expedition_bars(frame)) and not self.tpl.get("expedition.enter").find(frame, None)

    def expedition_portal_open(self, frame):
        """Which portal page is open: 'googarum' | 'twimbo' | 'svord' | 'x5' | '' (an ENTER page we cannot name) | None (not a portal page)."""
        for key, _ in self.EXP_PORTALS:
            try:
                if self.tpl.get("expedition.portal-" + key).find(frame):
                    return key
            except KeyError:
                pass
        return "" if self.tpl.get("expedition.enter").find(frame) else None

    def expedition_pages(self, frame):
        """Dimension map -> the portal you chose (or any portal found) -> its difficulty -> the chain presses ENTER."""
        S, now, t = self._ex, time.time(), self.tpl
        want_p = str(self.profile.get("expedition_portal", "") or "")
        want_d = int(self.profile.get("expedition_difficulty", 0) or 0)
        title = t.get("trials.difficulty-title").find(frame, None)
        if title and S["stage"] == "list":
            want = 0 if S["fallback"] else want_d
            return self.list_pick(S, frame, want, "Expedition difficulty", now)
        if title and S["done"] and 1.5 < now - S["clicked"] < 30 and S["closes"] < 4:
            # the list is still open: choosing the difficulty that was already selected does not close it -> ESC once
            S["closes"] += 1
            self.say("Expedition: the difficulty list is still open -> closing it with ESC", every=0)
            self.game.esc(1, 0)
            S["clicked"] = now - 0.5
            time.sleep(1.0)
            return True
        page = self.expedition_portal_open(frame)
        bars = [] if page is not None else self.expedition_bars(frame)
        if page is None and not bars:
            return False
        if now - S["seen"] > 30:                                          # a new visit: start over
            S.update(tried=[], idx=None, n=0, any=False, done=False, tries=0, stage="", closes=0, ups=0)
        S["seen"] = now
        names = dict(self.EXP_PORTALS)
        if bars:                                                          # ---- the dimension map ----
            n = S["n"] = len(bars)
            order = [k for k, _ in self.EXP_PORTALS]
            untried = [i for i in range(n) if i not in S["tried"]]
            if not untried:                                               # nothing matched: take any portal
                S["any"], S["tried"], untried = True, [], list(range(n))
            guess = order.index(want_p) if want_p in order and n == len(order) else None
            i = guess if guess in untried else untried[0]
            S["tried"].append(i)
            S["idx"] = i
            x, y = bars[i]
            self.say("Expedition: opening portal %d of %d%s" % (i + 1, n, " (looking for %s)" % names[want_p] if want_p in names else ""), every=0)
            self.game.click(x, y - 40)
            time.sleep(1.2)
            return True
        # ---- a portal page ----
        if want_p and page != want_p and not S["any"] and S["idx"] is not None and len(S["tried"]) < S["n"]:
            log("Expedition: this is %s, not %s -> back to the map to try the next portal" % (names.get(page, "a portal"), names.get(want_p, want_p)))
            self.game.esc(1, 0)
            time.sleep(1.0)
            return True
        if want_p and want_p != page and S["idx"] is not None and not S["any"]:
            log("Expedition: %s is not available -> using %s" % (names.get(want_p, want_p), names.get(page, "this portal")))
            S["any"] = True
        if not want_d or S["done"]:
            return False
        cur = next((v for _, v in self.read_rows(frame, 388, 424, 405, 522) if v), None)
        if cur == want_d:
            S["done"] = True
            return False
        S["tries"] += 1
        if S["tries"] > 4:
            log("Expedition: could not open the difficulty list -> playing the current difficulty (%s)" % (cur or "?"))
            S["done"] = True
            return False
        self.say("Expedition: difficulty is %s, opening the list to choose %d" % (cur or "?", want_d), every=0)
        self.game.click(542, 404)                                         # the little arrow beside the difficulty
        S.update(stage="list", ups=0, top=False, last_first=None, rate=None, hi=None, last_k=0, scrolls=0, fallback=False, bad=0)
        time.sleep(1.2)
        return True

    def invasion_wave(self, frame):
        """Invasion menu -> arrow under WAVE -> list -> wheel to the top -> (top wave | the wave you chose) -> PLAY is the chain's job."""
        S, now, t = self._iv, time.time(), self.tpl
        title = t.get("trials.difficulty-title").find(frame, None)
        menu = t.get("invasion.title").find(frame, None)
        play = t.get("invasion.play").find(frame, None)
        if title and S["stage"] == "list":
            want = 0 if S["fallback"] else int(self.profile.get("invasion_wave", 0) or 0)
            return self.list_pick(S, frame, want, "Invasion", now)
        if title and S["done"] and 1.5 < now - S["clicked"] < 30 and S["closes"] < 4:
            # we clicked a wave but the list is still open (clicking the wave that is already selected does not close it): ESC closes it
            S["closes"] += 1
            self.say("Invasion: the wave list is still open -> closing it with ESC", every=0)
            self.game.esc(1, 0)
            S["clicked"] = now - 0.5
            time.sleep(1.0)
            return True
        if not menu and not play:
            if now - S["seen"] > 30:
                S.update(done=False, tries=0, stage="", ups=0, closes=0)  # left the Invasion screen: next visit starts over
            return False
        S["seen"] = now
        if S["done"]:
            return False
        if S["stage"] == "list":                                          # the list closed without us choosing (or never opened)
            S["stage"] = ""
        if play:                                                          # right after a battle the screen is still sliding in: the first click is lost
            if now - S.get("play_seen", 0) > 30:                          # a NEW visit (not just the next look: 'check every' may be 4 s or more)
                S["play_since"] = now
            S["play_seen"] = now
            if now - S["play_since"] < 2.0:
                time.sleep(0.5)
                return True
        S["tries"] += 1
        if S["tries"] > 4:
            log("Invasion: could not open the wave list -> playing with the current wave")
            S["done"] = True
            return False
        if not play:
            return False
        cx, cy = t.get("invasion.play").center_of(play)
        self.say("Invasion: opening the wave list", every=0)
        self.game.click(cx + 62, cy + 118)                                # the little arrow under WAVE
        S.update(stage="list", ups=0, top=False, last_first=None, rate=None, hi=None, last_k=0, scrolls=0, fallback=False, bad=0)
        time.sleep(1.2)
        return True

    # ---- getting from the map into an activity ------------------------------ #
    NAV_GRACE = 25                      # seconds without map clicks after entering an activity (the game is loading it)

    def navigate(self, frame, ready):
        now = time.time()
        if now < self.nav_block_until:
            self.say("Just entered an activity - giving the game time to load it", every=3600)
            return False
        # Map icons only exist on the real map. Loading screens (big QUEST logo) and battles contain look-alikes: never click those.
        if not self.on_main_screen(frame) or self.tpl.get("globally.auto-green").find(frame, AFK_MARGIN):
            return False
        for task in ready:
            spec = TASKS[task]
            t, pos = self.see(spec["place"], frame, None)       # icon anywhere on screen: new event icons can't push it out
            if not pos:
                continue
            self._icon_miss.pop((self.cur_char, task), None)      # the icon exists on this hero after all
            n = self.nav_tries[task] = self.nav_tries.get(task, 0) + 1
            if n > self.GIVE_UP_NAV:
                log("%s: opened its map icon %d times but nothing happened" % (spec["label"], n - 1))
                self.evidence(frame, "unknown", "cannot_open_" + task, "clicked the %s map icon %d times but its screen never opened" % (spec["label"], n - 1), 300)
                self.nav_tries[task] = 0
                self.block(task, 10, "map icon does nothing")
                return True
            self.say("%s: opening it from the map (try %d)" % (spec["label"], n), every=20)
            # the right-hand event icons sit ABOVE their text label: odd tries click the icon, even tries the label
            self.tap(t, pos, -30 if task in ("invasion", "gvg", "expedition", "trials", "gauntlet") and n % 2 == 1 else 0)
            self.idle = self.stuck = 0
            time.sleep(1)
            return True
        return False

    def map_visible(self, frame):
        return any(self.tpl.get("place." + n).find(frame, None) for n in
                   ("pvp", "quest", "raid", "world-boss", "gvg", "invasion", "expedition", "trials", "gauntlet", "bait"))

    def lost(self, frame, ready):
        """Nothing recognised and no icon to click. On the map = the icon is missing. A moving picture (battle, loading,
        animation) is never 'stuck': ESC and screenshots only start after 10 s of an UNCHANGED screen."""
        still = self.still_for()
        if self.tpl.get("loading.title").find(frame, None):          # the game's own LOADING screen (a slow load can sit at 100% for a while)
            self.say("The game is loading - waiting", every=120)
            self.ladder = 0
            return
        if not self.map_visible(frame):
            self._map_lost_since = 0.0
        if self.map_visible(frame):
            now = time.time()
            self._map_lost_since = self._map_lost_since or now
            if still < 3 and now - self._map_lost_since < 30:       # a crowd walking around may hide an icon for a moment - but only for 30 s
                self.say("Waiting - on the map, looking for an icon (%s)" % self.waiting_summary(), every=120)
                return
            self.say("Waiting - on the map: " + self.waiting_summary(), every=120)
            fresh = self.game.screenshot()                          # last check with a brand-new picture: is an icon of a ready task there after all?
            back = [t for t in ready if self.tpl.get(TASKS[t]["place"]).find(fresh, None)]
            if back:
                log("Map icon check: %s is on screen in a fresh picture after all -> not counting this as missing" % ", ".join(TASKS[t]["label"] for t in back))
                self.map_miss = 0
                return
            self.map_miss += 1
            names = ", ".join(TASKS[t]["label"] for t in ready)
            seen = [n for n in ("pvp", "quest", "raid", "world-boss", "gvg", "invasion", "expedition", "trials", "gauntlet", "bait")
                    if self.tpl.get("place." + n).find(frame, None)]
            self.say("On the map, but no icon for: %s (look %d of 3; icons seen: %s)" % (names, self.map_miss, ", ".join(seen) or "none"), every=20)
            if self.map_miss >= 3:
                # Other icons are there but these are not: usually an event this hero has not unlocked yet (a low-level second hero).
                # First time 10 min (an overlay may hide it), then the longest pause allowed (--retry); one screenshot per task and hero.
                new = [t for t in ready if (self.cur_char, t) not in self._icon_miss]
                if new:
                    self.evidence(frame, "unknown", "icons_missing", "on the map but no icon for: %s" % ", ".join(TASKS[t]["label"] for t in new), 300)
                for t in ready:
                    n = self._icon_miss[(self.cur_char, t)] = self._icon_miss.get((self.cur_char, t), 0) + 1
                    self.block(t, 10 if n == 1 else 60, "its map icon is not on screen - not unlocked on this hero yet?")
                log("No map icon for %s - probably not unlocked on this hero yet; checking again later (switch such an event off for this hero "
                    "with its override button if you like)" % names)
                self.map_miss = 0
            return
        ta = self.tpl.get("globally.talkRightArrow")
        # Only the right-hand arrow of a popup counts (gear / pet pages: x ~650). Never in PVP, and never the yellow turn
        # marker over a hero in a battle (it sits on the left side of the screen).
        apos = None if self.activity == "pvp" else self.talk_arrow(frame, None, 600)
        if apos and self.news_open(frame):          # the News carousel's arrow: handled by the News popup check, not a dialogue
            apos = None
        if apos:                                    # a yellow 'next' arrow that stays for 8 s is a dialogue we did not recognise
            self._arrow_since = getattr(self, "_arrow_since", None) or time.time()
            if time.time() - self._arrow_since >= 8:
                # No name plate, so this is NOT a known dialogue (e.g. the gear / pet pages have yellow arrows too, and
                # clicking those just cycles through the slots). Never click it: close whatever it is with ESC x4.
                self._arrow_rounds = getattr(self, "_arrow_rounds", 0) + 1
                self.evidence(frame, "unknown", "dialogue_unrecognised", "yellow arrow on screen for 8 s without the name plate being recognised", 300)
                self.say("Yellow arrow on screen for 8 s without a dialogue name plate (gear/pet page?) -> ESC x4, 1 s apart", every=0)
                self.game.esc(4, gap=1.0)
                self._arrow_since = time.time()
                return
        else:
            self._arrow_since = None
        if self.tpl.get("globally.auto-green").find(frame, AFK_MARGIN):
            self.say("In a battle - waiting", every=300)
            self.ladder = 0
            return
        if self.black_frame(frame):
            self.say("Black screen (scene change / loading) - waiting", every=60)
            self.ladder = 0
            return
        if still < 10:
            self.say("Screen is changing (battle / loading / animation) - waiting", every=300)
            self.ladder = 0
            return
        stage = int(still // 10)                    # 10 s, 20 s, 30 s ... of a frozen, unrecognised screen
        if stage <= self.ladder:
            return
        self.ladder = stage
        if stage == 1:
            path = self.save_frame(frame, "unknown", "afk_unknown", "unrecognised screen, unchanged for 10 s", cooldown=300)
            self.say("Unrecognised screen, unchanged for 10 s%s -> ESC" % (", saved to %s" % path if path else ""), every=0)
            self.game.press("escape")
        elif stage in (2, 3):
            self.say("Still unrecognised after %d s -> ESC again" % (stage * 10), every=0)
            self.game.press("escape")
        elif stage == 5:
            self.say("Still nothing after 50 s -> ESC x4 and starting over", every=0)
            self.game.esc(4)
            self.last_motion = time.time()
            self.ladder = 0

    # ---- main loop ---------------------------------------------------------- #
    def run(self):
        self.refresh_overrides()
        skipped = [TASKS[t]["label"] for t in self.wanted if not TASKS[t].get("supported", True)]
        log("AFK tasks: %s" % ", ".join(TASKS[t]["label"] for t in self.tasks))
        if skipped:
            log("Not in this version yet (skipped): %s" % ", ".join(skipped))
        log("Profile: %s%s | quest mode %d | pvp target %s | gvg target %s | raid %s (mode %d) | world boss %s tier %s" % (
            self.profile["name"] or "-", "" if self.profile["found"] else " (file not found, using defaults)",
            self.profile["quest_mode"], self.profile["pvp_target"] or "default", self.profile["gvg_target"] or "default", self.profile["raid_level"] or "-", self.profile["raid_mode"],
            self.profile["wb_boss"] or "-", "%s%s" % (self.profile["wb_tier"] or "default", ", " + self.MODE_NAMES.get(self.profile["wb_mode"], "") if self.profile["wb_mode"] else "")))
        self.startup(exit_on_timeout=False)                 # shared first startup (bot.py): login popups, hero check, bounties
        while True:
            time.sleep(self.tick)
            self.refresh_overrides()
            frame = self.game.screenshot()
            if self.activity and self.on_main_screen(frame):
                self.activity = None
            if self.tpl.get("world-boss.start-boss").find(frame, None) or self.tpl.get("world-boss.invite").find(frame, None) \
                    or self.tpl.get("world-boss.lobby-ready").find(frame, None) or self.tpl.get("world-boss.lobby-unready").find(frame, None):
                self.lobby_seen = time.time()
            elif self.tpl.get("globally.auto-green").find(frame, AFK_MARGIN):
                self.battle_seen = time.time()
            self.watch_motion(frame)
            self.health_tick(frame)
            self.shadow_screen(frame)
            self.housekeeping(frame)
            if self.always_on(frame) or self.keepalive(frame) or self.popup_table(frame) or self.small_tasks(frame) or self.dismiss_known(frame):
                self.acted()
                continue
            if self._start_due and self.on_map_now(frame):
                # the map was not quiet at startup (a resumed dungeon was running): the start tasks follow now that it is over
                self._start_due = False
                self.startup_tasks(frame)
                self.acted()
                continue
            req = float(self.profile.get("switch_request") or 0)           # the GUI asks for a hero switch just before a full restart
            wanting = bool(self.profile.get("alternate")) and req > self._switch_req_done
            if wanting:
                if self._no_second or time.time() < self._no_switch_until:
                    self._switch_req_done = req
                    raise BotExit(EXIT_SWITCHED, "Alternate characters: no second hero available right now - restarting without switching")
                if self.on_map_now(frame):
                    self._switch_req_done = req
                    self.switch_character("before the full restart")
                    raise BotExit(EXIT_SWITCHED, "Alternate characters: finished - the GUI restarts the game now")
            ready = [t for t in self.tasks if self.is_ready(t)]
            for t in self.tasks:                    # announce when a pause has run out
                g = TASKS[t]["group"]
                if t in self.disabled:              # a switched-off task is never 'ready': it must not re-arm its group's announcement
                    continue                        # (GVG / Invasion / Expedition share one group: a skipped GVG made Invasion announce every look)
                if t in ready and g in self._was_waiting:
                    self._was_waiting.discard(g)
                    log("%s: wait is over - checking again" % TASKS[t]["label"])
                elif t not in ready:
                    self._was_waiting.add(g)
            self.ctx = "AFK, ready: %s | idle %d, map-miss %d" % (", ".join(TASKS[t]["label"] for t in ready) or "none (all waiting)", self.idle, self.map_miss)
            if not ready:
                def left(t):
                    if t in self.disabled:
                        return "%s: switched off in the overrides" % TASKS[t]["label"]
                    if t == "world-boss" and self.profile.get("wb_boss") not in WB_NAMES:
                        return "World Boss: no boss chosen (World Boss override button)"
                    until, why = self.blocked.get(TASKS[t]["group"], (0, ""))
                    return "%s: %s, retry in %d min" % (TASKS[t]["label"], why, max(1, round((until - time.time()) / 60)))
                if self.ear:
                    self.game.esc(4, gap=0.8)
                    time.sleep(1.5)
                    self.collect_bounties("end")
                    raise BotExit(EXIT_OK, "Every task is waiting for tickets/energy -> exiting (--ear)")
                if self.switch_if_exhausted(frame):
                    continue
                self.say("Waiting - " + self.waiting_summary(), every=300)
                time.sleep(self.tick if self._talk_box is not None else getattr(self, "slow_wait", SLOW_WAIT))      # a dialogue is open: keep clicking it at normal speed
                continue
            # A battle (the AUTO tab is on screen) or one that is still loading (just clicked accept / fight): nothing to choose
            # here. Pale enemies and the snowy background can look like menu text, so the menu screens are not even looked at.
            busy = "battle" if self.tpl.get("globally.auto-green").find(frame, AFK_MARGIN) else ("loading" if time.time() < self.nav_block_until else ("switch" if wanting else False))
            self.battle_shadow(frame, busy)
            if busy == "loading":
                # the 25 s 'just entered something' wait is only a guess. If the classifier SEES a menu of an event, the game has
                # finished loading and the wait is over (e.g. the PVP menu after a result screen).
                try:
                    sc = screens.classify(self, frame, AFK_MARGIN)
                except Exception:
                    sc = None
                if sc and sc.name in screens.MENU_SCREENS and not any("fight" in w for w in sc.why):
                    self.say("[gate] the classifier sees '%s' -> the loading wait is over" % sc.name, every=3600)
                    self.nav_block_until = 0.0
                    busy = False
            if busy == "switch":
                self.say("Alternate characters: finishing the current run, then switching hero before the restart", every=120)
                if self.tpl.get("dungeons.zones").find(frame, AFK_MARGIN):          # leave the zone map: the hero page opens from the main map
                    self.game.press("escape")
                    continue
            elif busy:
                self.say("A battle is running or loading - not touching the menus", every=300)
            if handlers.dispatch(self, frame, ready, busy, lambda afk, f: screens.classify(afk, f, AFK_MARGIN)):
                continue
            if not busy and self.tpl.get("dungeons.zones").find(frame, AFK_MARGIN):
                self.quest_screen(frame, "quest" in ready)
                continue
            if busy == "loading" and "quest" in ready and self.retry_difficulty(frame):
                continue
            if not busy and "quest" in ready and self.quest_difficulty(frame):
                continue
            if not busy and "fishing" in ready and self.claim_bait(frame):
                continue
            if not busy and (("raid" in ready and self.raid_screens(frame)) or ("world-boss" in ready and self.boss_screens(frame))):
                self.acted()
                continue
            if not busy and "invasion" in ready and self.invasion_wave(frame):
                self.acted()
                continue
            if not busy and "expedition" in ready and self.expedition_pages(frame):
                self.acted()
                continue
            if not busy and "trials" in ready and self.trials_difficulty(frame):
                self.acted()
                continue
            if not busy and "gauntlet" in ready and self.trials_difficulty(frame, "gauntlet"):
                self.acted()
                continue
            if any(self.try_chain(t, frame, busy) for t in sorted(ready, key=lambda t: t != self.activity) if TASKS[t]["chain"] or TASKS[t].get("done")):   # the activity we are in goes first (the Team screen's ACCEPT is shared)
                continue
            if not busy and self.close_paused_menu(frame, ready):
                continue
            self.idle += 1
            if self.idle < 3:                       # screens change over a second or two; give them time
                continue
            if not wanting and self.navigate(frame, [t for t in ready if t != "fishing"]):
                continue
            self.lost(frame, [t for t in ready if t != "fishing"] or ready)


# =========================================================================== #
# Commands
# =========================================================================== #
