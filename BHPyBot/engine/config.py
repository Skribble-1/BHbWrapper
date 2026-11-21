"""engine/config.py - settings: tasks, profile / overrides files, Troubleshooting defaults."""
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


AFK_MARGIN = 80          # buttons inside an activity may be this far from their usual spot
SLOW_WAIT = 15           # default seconds between looks while every task is waiting for tickets/energy (GUI: "Base Check Interval")
GROUP_GIE, GROUP_TG = "gvg/invasion/expedition", "trials/gauntlet"   # these share one cooldown (as in the original)

TASKS = {   # chain = buttons to click, in order of priority; done = 'out of tickets' dialog; block = minutes to wait then
    "quest": dict(label="Quest", place="place.quest", block=120, group="quest", done="dungeons.not-enough-energy",
                  chain=["dungeons.accept", "dungeons.rerun", "dungeons.collect"]),
    "fishing": dict(label="Claim fishing bait", place="place.bait", block=1440, group="fishing", done=None, chain=[]),
    "pvp": dict(label="PVP", place="place.pvp", block=30, group="pvp", done="pvp-arena.not-enough-ticket",
                chain=["pvp-arena.play", "pvp-arena.fight", "pvp-arena.accept", "pvp-arena.town-win", "pvp-arena.town-defeat"]),
    "world-boss": dict(label="World Boss", place="place.world-boss", block=60, group="world-boss", done="world-boss.not-enough-xeals",
                       chain=["world-boss.regroup"]),
    "gvg": dict(label="GVG", place="place.gvg", block=30, group=GROUP_GIE, done="invasion.not-enough-badges",
                chain=["gvg.play", "gvg.fight", "gvg.accept", "gvg.town"]),
    "invasion": dict(label="Invasion", place="place.invasion", block=30, group=GROUP_GIE, done="invasion.not-enough-badges",
                     chain=["invasion.play", "invasion.accept", "invasion.town"]),
    "expedition": dict(label="Expedition", place="place.expedition", block=30, group=GROUP_GIE, done="invasion.not-enough-badges",
                       chain=["expedition.play", "expedition.enter", "expedition.accept", "expedition.town"]),
    "trials": dict(label="Trials", place="place.trials", block=30, group=GROUP_TG, done="trials.not-enough-tokens",
                   chain=["trials.play", "trials.accept", "trials.town"]),
    "gauntlet": dict(label="Gauntlet", place="place.gauntlet", block=30, group=GROUP_TG, done="trials.not-enough-tokens",
                     chain=["gauntlet.play", "gauntlet.accept", "gauntlet.town"]),
    "raid": dict(label="Raid", place="place.raid", block=120, group="raid", done="raid.not-enough-shards",
                 chain=["raid.accept", "dungeons.rerun", "world-boss.regroup", "raid.town"]),
}
ORDER = ["quest", "fishing", "pvp", "world-boss", "gvg", "invasion", "expedition", "trials", "gauntlet", "raid"]
TASK_CODES = {"P": ["pvp"], "B": ["world-boss"], "W": ["world-boss"], "R": ["raid"], "Q": ["quest"], "F": ["fishing"],
              "I": ["invasion"], "E": ["expedition"], "V": ["gvg"], "T": ["trials"], "G": ["gauntlet"],
              "1": ["pvp", "world-boss", "raid"], "2": ["invasion", "gvg", "expedition"], "3": ["trials", "gauntlet"],
              "A": list(ORDER)}


def parse_task_codes(text):
    wanted = []
    for c in (text or "A").upper():
        if c in ",;+ ":
            continue
        if c not in TASK_CODES:
            raise BotExit(EXIT_ERROR, "unknown task code '%s' (P pvp, B world boss, R raid, Q quest, F fishing bait, "
                                      "I invasion, E expedition, V gvg, T trials, G gauntlet, A all)" % c)
        wanted += [t for t in TASK_CODES[c] if t not in wanted]
    return wanted


WB_NAMES = ["orlag", "netherworld", "melvin", "exterminytion", "brimstone", "titans", "ignited", "goodall", "gallus"]
WB_BY_OLD_LEVEL = {1: "orlag", 2: "netherworld", 3: "melvin", 4: "exterminytion", 5: "brimstone", 6: "titans", 7: "ignited"}   # the old bot's numbers


# Most players a world boss party can hold (this bot waits for at most max-1 OTHER players).
WB_MAX_PLAYERS = {"orlag": 5, "melvin": 4, "goodall": 4, "gallus": 4,
                  "netherworld": 3, "exterminytion": 3, "brimstone": 3, "titans": 3, "ignited": 3}


# ---- Troubleshooting functions (GUI: "Troubleshooting" button; stored in overrides.json -> "troubleshooting") ---------------------- #
# key: (title, log prefix the Hide switch filters, optional (has an Off switch), longest interval, what it does)
TSHOOT = {
    "screen":  ("Screen check",         "[screen]",        True,  60, "Names the screen the bot sees and logs it when it changes. Saves 'unknown screen' pictures."),
    "battle":  ("Battle check",         "[battle-check]",  True,  60, "Compares the old battle check with the screen classifier; logs and saves a picture when they disagree."),
    "handler": ("Event handler lines",  "[handler]",       True,  60, "Logs which event handled which screen (at most one line per interval)."),
    "health":  ("Chrome health",        "[health]",        False, 30, "Detects a crashed / hung Chrome tab, a Unity error dialog, a flat grey page -> restarts the game."),
    "frozen":  ("Frozen battle",        "[frozen]",        False, 30, "Detects a battle picture that has not moved for 4 minutes -> restarts the game."),
}
TSHOOT_DEFAULT_EVERY = 3
TSHOOT_FROZEN_SECS, TSHOOT_BAD_SECS, TSHOOT_FLAT_SECS, TSHOOT_BLACK_SECS = 240, 40, 80, 180     # how long a condition must last before the restart (independent of the interval)


def tshoot_all(ov):
    """overrides.json -> {key: dict(every=seconds, off=bool, hide=bool)}. Bad numbers fall back to 3 s; required items can never be off."""
    raw = ov.get("troubleshooting") if isinstance(ov, dict) else None
    raw = raw if isinstance(raw, dict) else {}
    out = {}
    for key, (_title, _prefix, optional, longest, _what) in TSHOOT.items():
        r = raw.get(key) if isinstance(raw.get(key), dict) else {}
        try:
            every = float(r.get("every", TSHOOT_DEFAULT_EVERY))
        except (TypeError, ValueError):
            every = TSHOOT_DEFAULT_EVERY
        out[key] = dict(every=min(max(every, 1.0), float(longest)), off=bool(r.get("off")) and optional, hide=bool(r.get("hide")))
    return out


def load_overrides():
    """overrides.json is written by the GUI's override buttons. A section only counts while its "on" is true."""
    try:
        data = json.loads(OVERRIDES_FILE.read_text(encoding="utf-8")) if OVERRIDES_FILE.is_file() else {}
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


# Gauntlet difficulty ranges: (lowest, highest, tier, "yellow" legendary / "cyan" set+mythic / None = single row)
GAUNTLET_RANGES = [(1, 9, 1, None), (10, 19, 2, None), (20, 29, 3, None), (30, 69, 4, None), (70, 99, 5, "yellow"), (100, 109, 5, "cyan"),
                   (110, 139, 6, "yellow"), (140, 149, 6, "cyan"), (150, 199, 7, "yellow"), (200, 209, 7, "cyan"),
                   (210, 259, 8, "yellow"), (260, 289, 8, "cyan"), (290, 439, 9, "yellow"), (440, 499, 9, "cyan"),
                   (500, 639, 10, "yellow"), (640, 674, 10, "cyan"), (675, 1079, 11, "yellow"), (1080, 1239, 11, "cyan"),
                   (1240, 1579, 12, "yellow"), (1580, 1799, 12, "cyan"), (1800, 2299, 13, "yellow"), (2300, 2599, 13, "cyan"),
                   (2600, 2899, 14, "yellow"), (2900, 3169, 14, "cyan"), (3170, 4283, 15, "yellow"), (4284, 5256, 15, "cyan"),
                   (4415, 6014, 16, "yellow"), (6015, 6424, 16, "cyan"), (6425, 7424, 17, "yellow"), (7425, 8314, 17, "cyan"),
                   (8315, 9464, 18, "yellow"), (9465, 9754, 18, "cyan"), (9755, 11734, 19, "yellow"), (11735, 12487, 19, "cyan"),
                   (12488, 13494, 20, "yellow"), (13495, 14360, 20, "cyan"), (14361, 15518, 21, "yellow"), (15519, 25001, 21, "cyan")]


def gauntlet_tier_of(number):
    """Difficulty number -> (tier, 'yellow'|'cyan'), or None when it is outside the table."""
    for lo, hi, tier, kind in GAUNTLET_RANGES:
        if lo <= number <= hi:
            return tier, kind or "cyan"
    return None


HERO_SIDES = ("left", "right")
PER_HERO_SECTIONS = ("quest", "pvp", "boss", "raid", "trials", "gauntlet", "gvg", "expedition", "invasion")      # not 'settings': speed etc. are global


def hero_view(ov, hero):
    """overrides.json -> what applies to one hero (0 = left, 1 = right, None = not known). The top-level sections are shared by both
    heroes; a section under "heroes" -> "left"/"right" REPLACES the shared one for that hero only (even when it is switched off:
    that hero then uses its profile)."""
    own = (ov.get("heroes") or {}).get(HERO_SIDES[hero]) if hero in (0, 1) and isinstance(ov.get("heroes"), dict) else None
    if not isinstance(own, dict):
        return ov
    merged = dict(ov)
    for k in PER_HERO_SECTIONS:
        if isinstance(own.get(k), dict):
            merged[k] = own[k]
    if isinstance(own.get("events"), dict):
        merged["events"] = own["events"]
    return merged


def apply_overrides(base, ov, hero=None):
    """Profile (from readonly.<name>.user-config.properties and the command line) + the GUI overrides -> (profile, disabled events, notes).
    hero: 0 / 1 = the left / right hero is active (its own override sections win), None = not known (shared sections only)."""
    ov = hero_view(ov, hero)
    p, notes, disabled = dict(base), [], set()
    def sec(name):
        d = ov.get(name)
        return d if isinstance(d, dict) and d.get("on") else None
    def num(d, key, default=0):
        try:
            return int(d.get(key, default) or default)
        except (TypeError, ValueError):
            return default
    d = sec("quest")
    if d:
        p["quest_zone"], p["quest_dungeon"] = num(d, "zone"), num(d, "dungeon")
        if num(d, "mode") in (1, 2, 3):
            p["quest_mode"] = num(d, "mode")
        p["quest_special"] = 1 if d.get("special") else 0
        notes.append("Quest: zone %s dungeon %s mode %s%s" % (p["quest_zone"] or "auto", p["quest_dungeon"] or "auto", p["quest_mode"],
                                                                ", special dungeon first" if p["quest_special"] else ""))
    d = sec("pvp")
    if d:
        p["pvp_target"] = num(d, "target")
        notes.append("PVP: target %s" % (p["pvp_target"] or "default"))
    d = sec("gvg")
    if d:
        p["gvg_target"] = num(d, "target")
        notes.append("GVG: target %s" % (p["gvg_target"] or "default"))
    d = sec("boss")
    if d:
        name = str(d.get("name", "")).strip().lower()
        if name in WB_NAMES:
            p["wb_boss"] = name
        if num(d, "tier"):
            p["wb_tier"] = num(d, "tier")
        if num(d, "mode") in (1, 2, 3):
            p["wb_mode"] = num(d, "mode")
        p["wb_private"] = bool(d.get("private", True))
        p["wb_players"] = max(1, num(d, "players", 1))
        p["wb_wait"] = max(1, num(d, "wait_min", 15))
        notes.append("World Boss: %s tier %s mode %s, %s" % (p["wb_boss"] or "-", p["wb_tier"] or "default", p["wb_mode"],
                     "private" if p["wb_private"] else "public, wait %d min for %d other player(s)" % (p["wb_wait"], p["wb_players"])))
    d = sec("raid")
    if d:
        if num(d, "level"):
            p["raid_level"] = num(d, "level")
        if num(d, "mode") in (1, 2, 3):
            p["raid_mode"] = num(d, "mode")
        notes.append("Raid: level %s mode %s" % (p["raid_level"], p["raid_mode"]))
    st = ov.get("settings")
    if isinstance(st, dict):
        try:
            p["tick"] = min(max(float(st.get("tick", 0) or 0), 0.2), 10.0) or None
            p["wait_every"] = min(max(int(st.get("wait_every", 0) or 0), 5), 3600) or None
            p["base_tick"] = min(max(float(st.get("base_tick", 0) or 0), 1.0), 300.0) or None
            p["stuck_action"] = st.get("stuck_action") if st.get("stuck_action") in ("restart", "shutdown") else "none"
            p["alternate"] = bool(st.get("alternate_chars", False))
            p["switch_request"] = float(st.get("switch_request", 0) or 0)
        except (TypeError, ValueError):
            pass
        notes.append("check every %s s (%s s while every task is waiting), repeat waiting messages every %s s" % (
            p.get("tick") or "default", p.get("base_tick") or SLOW_WAIT, p.get("wait_every") or "default"))
    d = sec("trials")
    if d:
        p["trials_tier"] = num(d, "tier")
        p["trials_kind"] = "yellow" if str(d.get("drops", "set")).lower().startswith("leg") else "cyan"
        p["trials_pick"] = "lowest" if str(d.get("pick", "highest")).lower() == "lowest" else "highest"
        number = num(d, "number")
        p["trials_number"] = number
        if number:                                    # an exact difficulty number: its tier and drop type come from the table (as for the Gauntlet)
            hit = gauntlet_tier_of(number)
            if hit:
                p["trials_tier"], p["trials_kind"] = hit
                notes.append("Trials: difficulty %d is tier %d (%s)" % (number, hit[0], "legendary" if hit[1] == "yellow" else "set+mythic"))
            else:
                notes.append("Trials: difficulty %d is not in the table -> using the tier field" % number)
        notes.append("Trials: tier %s, %s drops, %s value" % (p["trials_tier"] or "(unchanged)",
                     "legendary (yellow)" if p["trials_kind"] == "yellow" else "set+mythic (light blue)", p["trials_pick"]))
    d = sec("gauntlet")
    if d:
        tier, kind = num(d, "tier"), "yellow" if str(d.get("drops", "set")).lower().startswith("leg") else "cyan"
        number = num(d, "number")
        if number:                                    # an exact difficulty number picks its tier and drop type from the table
            hit = gauntlet_tier_of(number)
            if hit:
                tier, kind = hit
                notes.append("Gauntlet: difficulty %d is tier %d (%s)" % (number, tier, "legendary" if kind == "yellow" else "set+mythic"))
            else:
                notes.append("Gauntlet: difficulty %d is not in the table -> using the tier field" % number)
        p["gauntlet_tier"], p["gauntlet_kind"] = tier, kind
        p["gauntlet_number"] = number
        p["gauntlet_pick"] = "lowest" if str(d.get("pick", "highest")).lower() == "lowest" else "highest"
        notes.append("Gauntlet: tier %s, %s drops, %s value" % (tier or "(unchanged)", "legendary (yellow)" if kind == "yellow" else "set+mythic (light blue)", p["gauntlet_pick"]))
    d = sec("invasion")
    if d:
        p["invasion_wave"] = num(d, "wave")
        notes.append("Invasion: wave %s" % (p["invasion_wave"] or "top of the list"))
    d = sec("expedition")
    if d:
        pk = str(d.get("portal", "") or "").strip().lower()
        p["expedition_portal"] = pk if pk in ("googarum", "twimbo", "svord", "x5") else ""
        p["expedition_difficulty"] = num(d, "difficulty")
        notes.append("Expedition: %s, difficulty %s" % (({"googarum": "Googarum's", "twimbo": "Twimbo's", "svord": "Svord's", "x5": "X5-T34M's"}).get(p["expedition_portal"], "any portal")
                                                         + (" portal" if p["expedition_portal"] else ""), p["expedition_difficulty"] or "unchanged"))
    ev = ov.get("events") if isinstance(ov.get("events"), dict) else {}      # older overrides.json: one 'events' section
    ev = dict(ev, **{t: ov[t] for t in ("gvg", "expedition", "invasion", "trials", "gauntlet") if isinstance(ov.get(t), dict)})
    if True:
        for task in ("gvg", "invasion", "expedition", "trials", "gauntlet"):
            e = ev.get(task)
            if isinstance(e, dict) and e.get("on") and not e.get("enabled", True):
                disabled.add(task)
        for key, task in (("quest", "quest"), ("pvp", "pvp"), ("boss", "world-boss"), ("raid", "raid")):      # any event can be skipped (e.g. not unlocked on a low-level hero)
            e = ov.get(key)
            if isinstance(e, dict) and e.get("on") and not e.get("enabled", True):
                disabled.add(task)
        if disabled:
            notes.append("switched off: " + ", ".join(sorted(disabled)))
    return p, disabled, notes


# Every setting the profile file can hold. suffix = the key after "ig.user." (both heroes) / "ig.left." / "ig.right." (one hero only);
# (suffix, name in the bot's profile, kind, default, comment). kinds: int, bool, str, drops (set|legendary), pick (highest|lowest)
PROFILE_KEYS = [
    ("quest.mode", "quest_mode", "int", 3, "Quest difficulty: 1 = Normal, 2 = Hard, 3 = Heroic"),
    ("quest.zone", "quest_zone", "int", 0, "Quest zone number; 0 = automatic (the most recent beaten level)"),
    ("quest.dungeon", "quest_dungeon", "int", 0, "Dungeon in that zone, counted left to right; 0 = automatic"),
    ("quest.special", "quest_special", "int", 0, "1 = play the zone's SPECIAL dungeon first when it is there (unlocked once every regular dungeon has 3 stars); 0 = off"),
    ("pvp.target", "pvp_target", "int", 0, "PVP opponent: 1 = top Fight button ... 5; 0 = default"),
    ("gvg.target", "gvg_target", "int", 0, "GVG opponent: 1 = top Fight button ... 4; 0 = default (works exactly like the PVP opponent)"),
    ("raid.level", "raid_level", "int", 0, "Raid number (1 = first raid); 0 = leave the game's choice"),
    ("raid.mode", "raid_mode", "int", 3, "Raid difficulty: 1 = Normal, 2 = Hard, 3 = Heroic"),
    ("world-boss.name", "wb_boss", "str", "", "orlag | netherworld | melvin | exterminytion | brimstone | titans | ignited | goodall | gallus ; empty = no World Boss"),
    ("world-boss.tier", "wb_tier", "int", 0, "World Boss tier; 0 = leave as the game has it"),
    ("world-boss.mode", "wb_mode", "int", 3, "World Boss difficulty: 1 = Normal, 2 = Hard, 3 = Heroic"),
    ("world-boss.private", "wb_private", "bool", True, "true = solo (private); false = let other players join and wait for them"),
    ("world-boss.players", "wb_players", "int", 1, "Other players to wait for when not private"),
    ("world-boss.wait", "wb_wait", "int", 15, "Minutes to wait for them, then go solo"),
    ("trials.tier", "trials_tier", "int", 0, "Trials tier (13-15 known); 0 = leave the difficulty as the game has it"),
    ("trials.drops", "trials_kind", "drops", "set", "set = Set + Mythic (light blue) | legendary = Legendary (yellow)"),
    ("trials.pick", "trials_pick", "pick", "highest", "highest | lowest number inside that tier"),
    ("gauntlet.tier", "gauntlet_tier", "int", 0, "Gauntlet tier (1-21); 0 = leave the difficulty as the game has it"),
    ("gauntlet.drops", "gauntlet_kind", "drops", "set", "set | legendary"),
    ("gauntlet.pick", "gauntlet_pick", "pick", "highest", "highest | lowest number inside that tier"),
    ("invasion.wave", "invasion_wave", "int", 0, "Invasion wave to play; 0 = the top wave in the list"),
    ("expedition.portal", "expedition_portal", "str", "", "googarum | twimbo | svord | x5 ; empty = any portal found (also used if the chosen one is not unlocked)"),
    ("expedition.difficulty", "expedition_difficulty", "int", 0, "Expedition difficulty number (any length, e.g. 7445 or 125000); 0 = leave as the game has it"),
]
PROFILE_EVENTS = ("quest", "pvp", "world-boss", "raid", "gvg", "invasion", "expedition", "trials", "gauntlet")


def parse_profile_props(props, prefix):
    """The settings written under one prefix ('ig.user.', 'ig.left.', 'ig.right.'): ({bot setting: value}, {events switched off}).
    Only keys that are really in the file are returned, so a hero section changes just what it names."""
    out, off = {}, set()
    for suffix, name, kind, default, _ in PROFILE_KEYS:
        raw = props.get(prefix + suffix)
        if raw is None or str(raw).strip() == "":
            continue
        raw = str(raw).strip()
        try:
            if kind == "int":
                out[name] = int(float(raw.replace(",", "")))
            elif kind == "bool":
                out[name] = raw.lower() in ("1", "true", "yes", "on")
            elif kind == "drops":
                out[name] = "yellow" if raw.lower().startswith("leg") else "cyan"
            elif kind == "pick":
                out[name] = "lowest" if raw.lower() == "lowest" else "highest"
            else:
                out[name] = raw.lower()
        except ValueError:
            log("Profile: %s%s = %r is not a valid value -> ignored" % (prefix, suffix, raw))
    for ev in PROFILE_EVENTS:
        raw = props.get("%s%s.enabled" % (prefix, ev))
        if raw is not None and str(raw).strip().lower() in ("0", "false", "no", "off"):
            off.add(ev)
    return out, off


def load_profile(name):
    p = load_props(BOT_DIR / ("readonly.%s.user-config.properties" % name)) if name else {}
    prof = {"name": name, "found": bool(p), "invasion_wave": 0}
    for _, key, _, default, _ in PROFILE_KEYS:
        prof[key] = default
    shared, off = parse_profile_props(p, "ig.user.")
    if "wb_boss" not in shared:                                   # the old key: a boss LEVEL
        try:
            old = int(p.get("ig.user.world-boss.level", 0) or 0)
        except ValueError:
            old = 0
        shared["wb_boss"] = WB_BY_OLD_LEVEL.get(old, "")
    prof.update(shared)
    prof["profile_off"] = off
    prof["hero_profiles"], prof["hero_off"] = {}, {}
    for i, side in enumerate(HERO_SIDES):                         # settings that belong to ONE hero of this account
        vals, hoff = parse_profile_props(p, "ig.%s." % side)
        if vals or hoff:
            prof["hero_profiles"][i], prof["hero_off"][i] = vals, hoff
    return prof


def profile_template(name):
    """The text of a complete readonly.<name>.user-config.properties with every setting the bot reads."""
    L = ["# Bit Heroes bot - settings profile for the account '%s'" % name,
         "# File name: readonly.%s.user-config.properties (in the main folder, next to user-config.properties)." % name,
         "#",
         "# How settings are applied (later wins):",
         "#   1. built-in defaults",
         "#   2. ig.user.*   - this file, BOTH heroes of the account",
         "#   3. ig.left.*   / ig.right.*  - this file, only while that hero is playing (only the keys you write; a key you leave out",
         "#                    uses the ig.user value). The bot reads which hero is active on the YOUR HEROES page once after login.",
         "#   4. the override windows of the controller (overrides.json) - shared, then per hero.",
         "# A line starting with # is ignored. An empty value means 'not set'. Changes are read when the bot starts.",
         "#",
         "# <event>.enabled=false switches an event off (quest, pvp, world-boss, raid, gvg, invasion, expedition, trials, gauntlet),",
         "# e.g. for a hero that has not unlocked it yet:  ig.right.expedition.enabled=false",
         ""]
    L.append("# ---------------- both heroes ----------------")
    for suffix, _, kind, default, comment in PROFILE_KEYS:
        v = str(default).lower() if kind == "bool" else default
        L += ["# " + comment, "ig.user.%s=%s" % (suffix, v)]
    L += ["", "# Switch events off for both heroes (remove the # to use):"]
    L += ["#ig.user.%s.enabled=false" % ev for ev in PROFILE_EVENTS]
    for side in HERO_SIDES:
        L += ["", "# ---------------- %s hero only (remove the # of the lines you want; they win over ig.user.*) ----------------" % side]
        for suffix, _, kind, default, comment in PROFILE_KEYS:
            v = str(default).lower() if kind == "bool" else default
            L.append("#ig.%s.%s=%s" % (side, suffix, v))
        L += ["#ig.%s.%s.enabled=false" % (side, ev) for ev in PROFILE_EVENTS]
    return "\n".join(L) + "\n"
