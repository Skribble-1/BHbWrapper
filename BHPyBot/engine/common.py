#!/usr/bin/env python3
"""
bhbot - Bit Heroes bot (V2.0: engine/ package, one pip dependency: opencv-python).

It starts Chrome with the game page, attaches through Chrome's built-in DevTools port and then
works purely on screenshots + synthetic clicks/keys sent to the page. It does NOT use your real
mouse/keyboard, and it does not care where the window is. Chrome is started with --force-device-scale-factor=1, so the Windows display scaling does not change the pictures.

Commands                                   (run `python bhbot.py <command> -h` for options)
  fish N            fish N times (needs the fishing scene open, Start button visible)
  afk [TASKS]       chain PVP, Quest, GVG, Invasion, Expedition, Trials, Gauntlet, bait claim (--ear, --profile, --watch)
  esc [N]           press ESC N times (default 4) -> back to the main screen
  launch            just open the game window (and stay)
  snap              save a screenshot of the running game to out/snap/
  check [PNG]       list which known templates are visible in a screenshot / the live game
  template PNG NAME create a template from a screenshot (drag a box around a button's text)
  selftest          offline sanity test of the image matcher

Based on the matching ideas of https://github.com/9-9-9-9/Bit-Heroes-bot (GPL-3.0); the image
templates in templates/ come from that project, so this program is GPL-3.0 as well.
"""

VERSION = "2.0"
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

cv2.setNumThreads(1)          # tiny images: OpenCV's worker threads only spin up the CPU for nothing

ROOT = Path(__file__).resolve().parent.parent                 # this script's folder (BHPyBot): templates, out, overrides.json, bh-client scripts
# Your account files (user-config.properties, optional readonly.<name>.user-config.properties), the generated game pages (bh-client)
# and the bot's Chrome profile live one folder up (the main folder, next to start_win-gui.bat) when this script sits in a folder
# called BHPyBot (or the old name BHbWrapper); otherwise next to the script.
BOT_DIR = ROOT.parent if ROOT.name.lower() in ("bhpybot", "bhbwrapper") else ROOT
TEMPLATE_DIR = ROOT / "templates"
OUT_DIR = ROOT / "out"
CLIENT_DIR = BOT_DIR / "bh-client"
OVERRIDES_FILE = ROOT / "overrides.json"
GAME_W, GAME_H = 800, 520

# exit codes (also printed as "exit code: N" so bh_gui.py can tell success from failure)
EXIT_OK, EXIT_ERROR, EXIT_STUCK, EXIT_NO_ANCHOR, EXIT_DISCONNECTED, EXIT_DM, EXIT_NO_GAME = 0, 1, 2, 3, 4, 5, 6
EXIT_RESTART, EXIT_SHUTDOWN = 7, 8
EXIT_SWITCHED = 10                    # character switched (or not possible) because the GUI asked for it just before a full restart
EXIT_UNAVAILABLE = 11                   # this script cannot run on this hero (e.g. fishing not unlocked yet): the GUI skips to the next script, no restart
EXIT_MAINTENANCE = 9                    # 'Bit Heroes is down for maintenance': the GUI closes the game and starts again later      # 'screen stuck': the GUI restarts / shuts down the game (chosen in the GUI)


class BotExit(Exception):
    def __init__(self, code, msg=""):
        super().__init__(msg)
        self.code, self.msg = code, msg


RECENT = collections.deque(maxlen=8)      # last console lines, written onto saved screenshots


def log(msg):
    line = "%s  %s" % (time.strftime("%H:%M:%S"), msg)
    RECENT.append(line)
    print(line, flush=True)


# =========================================================================== #
# Config (reads the existing user-config.properties, so nothing to migrate)
# =========================================================================== #
def load_props(path=BOT_DIR / "user-config.properties"):
    props = {}
    if Path(path).is_file():
        for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                props[k.strip()] = v.strip().replace("\\\\", "\\")
    return props


def load_accounts(props):
    accounts = []
    for k, v in props.items():
        m = re.fullmatch(r"(\d+)\.game\.kong\.user\.id", k)
        if m and v:
            i = m.group(1)
            accounts.append({"index": int(i), "id": v, "name": props.get(f"{i}.game.kong.user.name", ""),
                             "token": props.get(f"{i}.game.auth.token", "")})
    return sorted(accounts, key=lambda a: a["index"])


def pick_account(accounts, wanted):
    if not accounts:
        raise BotExit(EXIT_ERROR, "no game account found in user-config.properties (keys like 1.game.kong.user.id)")
    if wanted is None:
        return accounts[0]
    for a in accounts:
        if wanted == a["name"] or wanted == str(a["index"]):
            return a
    raise BotExit(EXIT_ERROR, "account '%s' not found; known: %s" % (wanted, ", ".join(a["name"] for a in accounts)))


def find_chrome(props):
    cands = [props.get("external.application.chrome.path", "").strip().strip('"')]
    if os.name == "nt":
        try:                                    # Windows' own list of installed programs (finds Chrome wherever it is installed)
            import winreg
            for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
                for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
                    try:
                        with winreg.OpenKey(hive, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe", 0, winreg.KEY_READ | view) as k:
                            cands.append(winreg.QueryValueEx(k, "")[0])
                    except OSError:
                        pass
        except ImportError:
            pass
        for var in ("PROGRAMFILES", "PROGRAMW6432", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            if os.environ.get(var):
                cands.append(str(Path(os.environ[var]) / "Google/Chrome/Application/chrome.exe"))
        cands.append(shutil.which("chrome") or "")
    else:
        # LINUX / MACOS: NOT TESTED - community help wanted (see README.txt, "Linux and macOS").
        cands += [shutil.which(n) or "" for n in ("google-chrome", "google-chrome-stable", "chrome", "chromium", "chromium-browser")]
        cands += ["/usr/bin/google-chrome", "/usr/bin/google-chrome-stable", "/opt/google/chrome/chrome",
                  "/usr/bin/chromium", "/usr/bin/chromium-browser", "/snap/bin/chromium",
                  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                  str(Path.home() / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
                  "/Applications/Chromium.app/Contents/MacOS/Chromium"]
    for c in cands:
        if c and Path(c).is_file():
            return c
    raise BotExit(EXIT_NO_GAME, "Chrome not found; set external.application.chrome.path in user-config.properties "
                                "(Linux: e.g. /usr/bin/google-chrome or /usr/bin/chromium; macOS: /Applications/Google Chrome.app/Contents/MacOS/Google Chrome)")


# =========================================================================== #
# Game page (replaces client.bat / mini-game-on-chrome*.bat)
# =========================================================================== #
