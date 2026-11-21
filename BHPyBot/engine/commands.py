"""engine/commands.py - command line: afk, fish, screen, selftest, template, profile ...."""
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
from engine.afk import *  # noqa: F401,F403


def clear_startup_popups(game, max_seconds=120):
    """A Chrome that was left open can show 'Are you still there?' and, behind it, 'You have been disconnected'. Click YES / RECONNECT
    until neither is on screen (in whatever order they come) before the script starts."""
    tpls = Templates()
    still, rec, maint = tpls.get("globally.are-you-still-there"), tpls.get("globally.reconnect"), tpls.get("globally.maintenance")
    end, clean, acted = time.time() + max_seconds, 0, 0
    while time.time() < end and clean < 2:
        frame = game.screenshot()
        if maint.find(frame, None):
            log("Start-up check: the game says it is down for maintenance")
            game.click(398, 358)
            raise BotExit(EXIT_MAINTENANCE, "server maintenance")
        pos = still.find(frame, None)
        if pos:
            log("Start-up check: the game is asking 'are you still there?' -> clicking YES")
            game.click(398, 358)                    # the YES button (the template is the text above it)
            game.press("space")
            clean, acted = 0, acted + 1
            time.sleep(2.5)
            continue
        pos = rec.find(frame, None)
        if pos:
            log("Start-up check: the game says it was disconnected -> clicking RECONNECT, waiting for it to load")
            game.click(*rec.center_of(pos))
            clean, acted = 0, acted + 1
            time.sleep(15)
            continue
        clean += 1
        time.sleep(1.2)
    if acted:
        log("Start-up check: the game is ready")


def connect(args, watch=False):
    props = load_props()
    # an account is only needed when Chrome has to be started; an already running game is just attached to
    account = None if port_open(args.port) else pick_account(load_accounts(props), getattr(args, "account", None))
    game = Game(port=args.port, watch=watch)
    try:
        game.open(props, account, headless=getattr(args, "headless", False))
    except (OSError, ConnectionError) as e:
        raise BotExit(EXIT_NO_GAME, "cannot reach the game: %s" % e)
    if not watch:                                   # --watch must never click
        clear_startup_popups(game)
    return game, props


def cmd_fish(args):
    game, props = connect(args, args.watch)
    fb = Fishing(game, Templates(), props)
    fb.offset = args.cast_offset / 1000.0
    fb.run(args.count)


def cmd_afk(args):
    log("bhbot %s" % VERSION)
    props = load_props()
    accounts = load_accounts(props)
    if not args.account and args.profile and any(a["name"] == args.profile for a in accounts):
        args.account = args.profile            # like the old bot: profile name = the account it belongs to
    wanted = parse_task_codes(args.tasks)
    game, props = connect(args, args.watch)
    profile = load_profile(args.profile or args.account or (accounts[0]["name"] if accounts else ""))
    if args.boss:
        profile["wb_boss"] = args.boss.lower()
    if args.boss_tier:
        profile["wb_tier"] = args.boss_tier
    if args.boss_mode:
        profile["wb_mode"] = args.boss_mode
    if args.raid_level:
        profile["raid_level"] = args.raid_level
    Afk(game, Templates(), props, wanted, profile, ear=args.ear, tick=args.tick, retry=args.retry).run()


def cmd_profile(args):
    """Write a complete profile file (every setting the bot reads, with its default) for an account."""
    accounts = load_accounts(load_props())
    name = args.name or (accounts[0]["name"] if accounts else "")
    if not name:
        raise BotExit(EXIT_ERROR, "no account name found in user-config.properties; pass --name")
    path = BOT_DIR / ("readonly.%s.user-config.properties" % name)
    if path.exists() and not args.force:
        raise BotExit(EXIT_ERROR, "%s already exists (use --force to replace it; the old one is kept as .bak)" % path.name)
    if path.exists():
        shutil.copyfile(path, str(path) + ".bak")
    path.write_text(profile_template(name), encoding="utf-8")
    log("Wrote %s" % path)


def cmd_esc(args):
    game, _ = connect(args)
    log("Pressing ESC x%d" % args.times)
    game.esc(args.times)


def cmd_launch(args):
    connect(args)
    log("Game is running (Chrome keeps running after this command ends)")


def cmd_snap(args):
    game, props = connect(args)
    path = Bot(game, Templates(), props).save_frame(game.screenshot(), "snap", args.label)
    log("Saved %s" % path)


def cmd_check(args):
    tpls = Templates()
    if args.png:
        frame = Frame(cv2.cvtColor(cv2.imread(args.png, cv2.IMREAD_COLOR), cv2.COLOR_BGR2RGB))
        if frame.img.shape[:2] != (GAME_H, GAME_W):
            raise BotExit(EXIT_ERROR, "screenshot must be %dx%d (it is %dx%d)" % ((GAME_W, GAME_H) + frame.img.shape[1::-1]))
    else:
        game, _ = connect(args)
        frame = game.screenshot()
    found = 0
    for name in tpls.names():
        try:
            t = tpls.get(name)
        except ValueError:
            continue
        hits = t.find_all(frame, margin=None if args.anywhere else DEFAULT_MARGIN)
        if hits:
            found += 1
            log("%-40s at %s%s%s" % (name, hits[0], " (+%d more)" % (len(hits) - 1) if len(hits) > 1 else "",
                                     "   -> %s" % t.on_seen if t.on_seen else ""))
    log("%d known template(s) visible" % found)


def suggest_ink(rgb):
    """Pick the text colour of a crop: white / black if present, else the most common bright colour."""
    cands = [(255, 255, 255), (0, 0, 0)]
    cols, counts = np.unique(rgb.reshape(-1, 3), axis=0, return_counts=True)
    cands += [tuple(int(v) for v in cols[i]) for i in np.argsort(-counts)[:4]]
    best = None
    for c in cands:
        n = int(ink_map(rgb, c).sum())
        if 12 <= n <= rgb.shape[0] * rgb.shape[1] * 0.6:
            score = n if c in cands[:2] else n * 0.5
            if best is None or score > best[0]:
                best = (score, c, n)
    return (best[1], best[2]) if best else (None, 0)


def cmd_template(args):
    src = cv2.imread(args.png, cv2.IMREAD_COLOR)
    if src is not None and args.png.lower().endswith("_annotated.png"):
        clean = args.png[:-len("_annotated.png")] + ".png"
        raise BotExit(EXIT_ERROR, "that is the annotated copy (for reading). Use the clean one next to it: %s" % clean)
    if src is None or src.shape[:2] != (GAME_H, GAME_W):
        raise BotExit(EXIT_ERROR, "need an %dx%d screenshot made by the bot (see `snap` / out/unknown)" % (GAME_W, GAME_H))
    if args.rect:
        x, y, w, h = (int(v) for v in args.rect.split(","))
    else:
        log("Drag a box tightly around the button's TEXT, then press ENTER (ESC to cancel)")
        try:
            x, y, w, h = (int(v) for v in cv2.selectROI("template - drag a box around the text, ENTER to accept", src, False))
            cv2.destroyAllWindows()
        except cv2.error:
            raise BotExit(EXIT_ERROR, "no window support in this OpenCV build; pass --rect x,y,w,h instead "
                                      "(or `pip uninstall opencv-python-headless` and `pip install opencv-python`)")
    if w < 4 or h < 4:
        raise BotExit(EXIT_ERROR, "nothing selected")
    crop = src[y:y + h, x:x + w]
    rgb = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
    if args.ink:
        ink = tuple(int(args.ink[i:i + 2], 16) for i in (0, 2, 4))
    else:
        ink, n = suggest_ink(rgb)
        if ink is None:
            raise BotExit(EXIT_ERROR, "could not find a text colour in that box; select the text of the button or pass --ink RRGGBB")
    tpl = Template(args.name, rgb, ink, at=(x, y))
    full = Frame(cv2.cvtColor(src, cv2.COLOR_BGR2RGB))
    hits = tpl.find_all(full, margin=None)
    log("text colour %02x%02x%02x, %d text pixels, box at %d,%d (%dx%d)" % (ink + (tpl.n_ink, x, y, w, h)))
    if tpl.n_ink < 12:
        log("WARNING: very few text pixels - this template will match almost anything. Select a bigger label.")
    if (tpl.expected) not in hits:
        raise BotExit(EXIT_ERROR, "the template does not find itself in the screenshot; try --ink RRGGBB")
    if len(hits) > 1:
        log("WARNING: matches %d places on this screen (%s). Select something more unique, or it will only work with a position hint (default)." % (len(hits), hits[:4]))
    # how often does it show up in the other screenshots we have? (a template that shows up everywhere is too generic)
    others = [p for p in OUT_DIR.rglob("*.png") if p.resolve() != Path(args.png).resolve()][:60]
    seen = 0
    for p in others:
        im = cv2.imread(str(p), cv2.IMREAD_COLOR)
        if im is not None and im.shape[:2] == (GAME_H, GAME_W) and tpl.find(Frame(cv2.cvtColor(im, cv2.COLOR_BGR2RGB)), margin=None):
            seen += 1
    log("also visible in %d of %d other saved screenshots" % (seen, len(others)))
    t = Templates()
    t.save(args.name, crop, ink, None if args.anywhere else (x, y), args.on_seen)
    log("Saved templates/%s.png%s%s" % (args.name, "" if args.anywhere else "  (position hint %d,%d +-%d px)" % (x, y, DEFAULT_MARGIN),
                                        "  [when seen: %s]" % args.on_seen if args.on_seen else ""))
    if not args.on_seen:
        log("It is saved but nothing uses it yet. Add --on-seen click|esc|space to make the bot react to it by itself.")


def _offline_afk():
    """An Afk with only the templates: enough for the screen classifier to judge saved screenshots."""
    a = Afk.__new__(Afk)
    a.tpl = Templates()
    return a


def _load_frame(path):
    im = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if im is None:
        return None
    if im.shape[:2] in ((558, 805), (558, 806)):                  # a window screenshot: cut the game out of it like the bot does
        im = im[31:551, 3:803]
    if im.shape[:2] != (GAME_H, GAME_W):
        return None
    return Frame(cv2.cvtColor(im, cv2.COLOR_BGR2RGB))


CORPUS_DIR = ROOT / "tests" / "screens"


def corpus_check(verbose=True):
    """Every tests/screens/<expected name>/*.png must be classified as <expected name>. -> (checked, wrong)"""
    afk, checked, wrong = _offline_afk(), 0, []
    for folder in sorted(p for p in CORPUS_DIR.glob("*") if p.is_dir()):
        if folder.name not in screens.NAMES:
            wrong.append((folder.name, "-", "folder name is not a screen name (see screens.NAMES)"))
            continue
        for png in sorted(folder.glob("*.png")):
            frame = _load_frame(png)
            if frame is None:
                wrong.append((folder.name, png.name, "not an 800x520 / window screenshot"))
                continue
            checked += 1
            sc = screens.classify(afk, frame, AFK_MARGIN, full=True)
            if sc.name != folder.name:
                wrong.append((folder.name, png.name, "classified as %s (matched %s; also %s)" % (sc.name, ", ".join(sc.why) or "-", ", ".join(sc.also) or "-")))
    if verbose:
        log("screen corpus: %d screenshot(s) checked, %d wrong" % (checked, len(wrong)))
        for exp, name, why in wrong:
            log("  WRONG  tests/screens/%s/%s -> %s" % (exp, name, why))
    return checked, wrong


def cmd_screen(args):
    if args.live:
        # Watch the running game from a SEPARATE process: prints the screen name whenever it changes, never clicks. Works while the
        # controller's Pause is on (Pause only freezes the bot, not the game), so you can steer the game by hand and read what the bot sees.
        game, _ = connect(args, watch=True)               # watch = attach only, never click
        afk = _offline_afk()
        last = None
        log("Watching the game (Ctrl+C to stop). Nothing is clicked.")
        while True:
            try:
                sc = screens.classify(afk, game.screenshot(), AFK_MARGIN, full=True)
            except KeyboardInterrupt:
                return
            except Exception as e:
                sc = None
                if last != "error":
                    log("(cannot read the game right now: %s)" % e)
                last = "error"
            if sc and sc.name != last:
                last = sc.name
                log("[screen] %s%s%s" % (sc.name, "   (matched: %s)" % ", ".join(sc.why[:3]) if sc.why else "   - no rule matches this screen",
                                         "   also: " + ", ".join(sc.also) if sc.also else ""))
            try:
                time.sleep(args.every)
            except KeyboardInterrupt:
                return
    if args.corpus or not args.png:
        checked, wrong = corpus_check()
        if wrong:
            raise BotExit(EXIT_ERROR, "%d screenshot(s) classified wrongly" % len(wrong))
        return
    afk = _offline_afk()
    for path in args.png:
        frame = _load_frame(path)
        if frame is None:
            log("%s: not an 800x520 bot screenshot (or a window screenshot)" % path)
            continue
        sc = screens.classify(afk, frame, AFK_MARGIN, full=True)
        log("%s -> %s   matched: %s%s" % (Path(path).name, sc.name, ", ".join(sc.why) or "-", "   also: " + ", ".join(sc.also) if sc.also else ""))


def cmd_selftest(args):
    """Paste every template onto a fake screen and check the matcher finds it - and only it - where it belongs."""
    tpls, bad, n = Templates(), [], 0
    afk0 = Afk.__new__(Afk)
    afk0.tpl = tpls_for_picker = Templates()
    for p in picker.selftest(afk0.digit_samples, BOT_DIR):
        log("selftest FAIL (number lists): " + p)
        bad.append(p)
    rng = np.random.default_rng(1)
    for name in tpls.names():
        m = tpls.manifest.get(name, {})
        if m.get("kind") == "mask" or "ink" not in m:
            continue
        png = cv2.cvtColor(cv2.imread(str(TEMPLATE_DIR / (name + ".png"))), cv2.COLOR_BGR2RGB)
        t = tpls.get(name)
        at = t.at or ((t.region[0] + 2, t.region[1] + 2) if t.region else (200, 200))
        want = (at[0] + t.origin[0], at[1] + t.origin[1])
        bg = rng.integers(40, 120, (GAME_H, GAME_W, 3), dtype=np.uint8)
        h, w = png.shape[:2]
        bg[at[1]:at[1] + h, at[0]:at[0] + w] = png[:GAME_H - at[1], :GAME_W - at[0]]
        hits = t.find_all(Frame(bg), margin=None)
        n += 1
        if hits != [want]:
            bad.append((name, want, hits[:3]))
    log("selftest: %d templates checked, %d problem(s)" % (n, len(bad)))
    for b in bad:
        log("  PROBLEM %s expected %s got %s" % b)
    if bad:
        raise BotExit(EXIT_ERROR, "selftest failed")
    if CORPUS_DIR.is_dir():
        _, wrong = corpus_check()                       # the screenshots in tests/screens must still be recognised
        if wrong:
            raise BotExit(EXIT_ERROR, "selftest failed: screen corpus")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="bhbot", description=__doc__.split("Commands")[0].strip())
    ap.add_argument("--port", type=int, default=9222, help="Chrome DevTools port (default 9222)")
    ap.add_argument("--account", help="account name or number from user-config.properties (default: first)")
    ap.add_argument("--headless", action="store_true", help="run Chrome without a visible window")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("fish", help="fish N times"); p.add_argument("count", type=int)
    p.add_argument("--watch", action="store_true", help="only report what the bot sees, never click")
    p.add_argument("--cast-offset", type=int, default=0, help="start timing correction in ms (+ = cast later); it also tunes itself")
    p.set_defaults(fn=cmd_fish)
    p = sub.add_parser("afk", help="chain PVP/Quest/GVG/... until out of tickets (like the old `afk a`)")
    p.add_argument("tasks", nargs="?", default="a", help="letters: P pvp, Q quest, F fishing bait, I invasion, E expedition, V gvg, T trials, G gauntlet, A all (default)")
    p.add_argument("--profile", help="settings file readonly.<name>.user-config.properties (default: the account name)")
    p.add_argument("--ear", action="store_true", help="exit when every task is waiting for tickets/energy")
    p.add_argument("--boss", help="world boss to fight: %s (overrides the profile)" % ", ".join(WB_NAMES))
    p.add_argument("--boss-tier", type=int, default=0, help="world boss tier, e.g. 12 (overrides the profile)")
    p.add_argument("--boss-mode", type=int, default=0, choices=(0, 1, 2, 3), help="world boss difficulty: 1 normal, 2 hard, 3 heroic (overrides the profile)")
    p.add_argument("--raid-level", type=int, default=0, help="raid number: 1 = first raid (Tier 4) ... 12 = Tier 15 (overrides the profile)")
    p.add_argument("--retry", type=int, default=30, help="longest pause (minutes) after running out of tickets/energy before looking again (default 30)")
    p.add_argument("--tick", type=float, default=1.0, help="seconds between looks at the screen (default 1)")
    p.add_argument("--watch", action="store_true", help="only say what the bot sees and would do, never click"); p.set_defaults(fn=cmd_afk)
    p = sub.add_parser("esc", help="press ESC N times"); p.add_argument("times", type=int, nargs="?", default=4); p.set_defaults(fn=cmd_esc)
    sub.add_parser("launch", help="open the game").set_defaults(fn=cmd_launch)
    p = sub.add_parser("snap", help="save a screenshot of the running game"); p.add_argument("label", nargs="?", default="snap"); p.set_defaults(fn=cmd_snap)
    p = sub.add_parser("check", help="which known templates are visible?"); p.add_argument("png", nargs="?")
    p.add_argument("--anywhere", action="store_true", help="ignore position hints"); p.set_defaults(fn=cmd_check)
    p = sub.add_parser("template", help="make a template from a screenshot"); p.add_argument("png"); p.add_argument("name")
    p.add_argument("--rect", help="x,y,w,h instead of dragging a box"); p.add_argument("--ink", help="text colour RRGGBB (default: auto)")
    p.add_argument("--on-seen", choices=["click", "esc", "space"], help="what the bot does by itself when it sees this")
    p.add_argument("--anywhere", action="store_true", help="no position hint: match anywhere on screen"); p.set_defaults(fn=cmd_template)
    p = sub.add_parser("screen", help="V2.0: which screen does the classifier see in a saved screenshot? (--corpus: check tests/screens)")
    p.add_argument("png", nargs="*"); p.add_argument("--corpus", action="store_true")
    p.add_argument("--live", action="store_true", help="watch the running game and print the screen name when it changes (no clicks)")
    p.add_argument("--every", type=float, default=1.0); p.set_defaults(fn=cmd_screen)
    p = sub.add_parser("profile", help="write a complete readonly.<account>.user-config.properties with every setting")
    p.add_argument("--name"); p.add_argument("--force", action="store_true"); p.set_defaults(fn=cmd_profile)
    sub.add_parser("selftest", help="offline matcher test").set_defaults(fn=cmd_selftest)
    args = ap.parse_args(argv)
    try:
        args.fn(args)
        return EXIT_OK
    except BotExit as e:
        log(e.msg) if e.msg else None
        if e.code:
            print("exit code: %d" % e.code, flush=True)
        return e.code
    except KeyboardInterrupt:
        log("Stopped")
        return EXIT_OK
    except (ConnectionError, OSError) as e:
        log("Lost the game: %s" % e)
        print("exit code: %d" % EXIT_NO_GAME, flush=True)
        return EXIT_NO_GAME


if __name__ == "__main__":
    sys.exit(main())
