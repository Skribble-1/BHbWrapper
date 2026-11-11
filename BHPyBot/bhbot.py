#!/usr/bin/env python3
"""
bhbot - Bit Heroes bot (single file, one pip dependency: opencv-python).

It starts Chrome with the game page, attaches through Chrome's built-in DevTools port and then
works purely on screenshots + synthetic clicks/keys sent to the page. It does NOT use your real
mouse/keyboard, and it does not care where the window is or what the Windows display scaling is.

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
import argparse
import base64
import collections
import json
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
cv2.setNumThreads(1)          # tiny images: OpenCV's worker threads only spin up the CPU for nothing
import math
import numpy as np

ROOT = Path(__file__).resolve().parent                 # this script's folder (BHPyBot): templates, out, overrides.json, bh-client scripts
# Your account files (user-config.properties, optional readonly.<name>.user-config.properties), the generated game pages (bh-client)
# and the bot's Chrome profile live one folder up (the main folder, next to ~start_gui.bat) when this script sits in a folder
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
        cands += [shutil.which(n) or "" for n in ("google-chrome", "google-chrome-stable", "chrome", "chromium", "chromium-browser")]
        cands.append("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    for c in cands:
        if c and Path(c).is_file():
            return c
    raise BotExit(EXIT_NO_GAME, "Chrome not found; set external.application.chrome.path in user-config.properties")


# =========================================================================== #
# Game page (replaces client.bat / mini-game-on-chrome*.bat)
# =========================================================================== #
GAME_HTML = """<html lang="en"><head><title>Bit Heroes</title>
<script src="./sitewide_javascripts.js"></script>
<script>
const kongUserId = __ID__; const kongUserName = __NAME__; const gameAuthToken = __TOKEN__;
const gameId = 266462;
var active_user = new ActiveUser({"kong_user_data": {"id": kongUserId, "username": kongUserName, "current_locale": "en-US"}});
active_user.setGame(gameId, null, {"game_auth_token": gameAuthToken});
var channel_id = uuid.v4();
</script>
<script src="./holodeck_javascripts.js"></script>
<script>(function () { window.imaVideoBumper = new IMAVideoBumper({targetingParams: null, baseUrl: null, metricTracker: null});
  window.imaVideoBumper.loadGame(true); }).runWhenDomLoaded();</script>
</head>
<body id="play" style="background-color:#000;margin:0">
<div id="game"><iframe id="gameiframe" name="gameiframe" style="width:800px;height:520px;position:absolute;top:0;left:0;border:0" width="800" height="520"></iframe>
<script>
gameLoader = new Kongregate.GameLoader(function (urlOptions) {
  new GameIframe({"iframe_url": "https://web.bitheroesgame.com/kongregatewebgl/", "game_width": 800, "game_height": 520,
    "max_game_width": 800, "max_game_height": 520, "game_left": 0, "game_top": 0, "host": "https://www.kongregate.com",
    "game_id": gameId, "game_version": 1610392853, "flash_var_prefix": "kv_", "game_type": "iframe"},
    urlOptions, channel_id).createGameIframeElement();
}, false, false);
function activateGame(urlOptions) { if (activateGame._alreadyActivated) return; activateGame._alreadyActivated = true;
  gameLoader.loadGame(urlOptions || ''); document.fire('game:activated'); };
</script></div></body></html>
"""


def write_game_page(account):
    """Write bh-client/index<N>.html for the account; make sure the two Kongregate scripts exist."""
    CLIENT_DIR.mkdir(exist_ok=True)
    jar = BOT_DIR / "BitHeroes.jar"
    for js in ("sitewide_javascripts.js", "holodeck_javascripts.js"):
        if not (CLIENT_DIR / js).is_file():
            shipped = ROOT / "bh-client" / js                         # the copy that comes with the bot (BHPyBot/bh-client)
            if shipped.is_file():
                (CLIENT_DIR / js).write_bytes(shipped.read_bytes())
            elif jar.is_file():                                       # an old Java bot folder: take it from the jar
                with zipfile.ZipFile(jar) as z:
                    (CLIENT_DIR / js).write_bytes(z.read("game-scripts/" + js))
            else:
                raise BotExit(EXIT_NO_GAME, "%s is missing: the BHPyBot/bh-client folder must contain it" % js)
    page = CLIENT_DIR / ("index%d.html" % account["index"])
    page.write_text(GAME_HTML.replace("__ID__", json.dumps(int(account["id"])))
                    .replace("__NAME__", json.dumps(account["name"]))
                    .replace("__TOKEN__", json.dumps(account["token"])), encoding="utf-8")
    return page


# =========================================================================== #
# Chrome DevTools Protocol client (stdlib only: tiny websocket implementation)
# =========================================================================== #
class WebSocket:
    def __init__(self, url, timeout=30):
        host, rest = url[5:].split("/", 1)
        h, p = host.split(":")
        self.sock = socket.create_connection((h, int(p)), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall(("GET /%s HTTP/1.1\r\nHost: %s\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                           "Sec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n" % (rest, host, key)).encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = self.sock.recv(1)
            if not chunk:
                raise ConnectionError("websocket handshake failed")
            buf += chunk
        if b" 101 " not in buf.split(b"\r\n")[0]:
            raise ConnectionError("websocket handshake refused: " + buf.split(b"\r\n")[0].decode(errors="replace"))
        self.next_id = 0

    def _recv(self, n):
        data = b""
        while len(data) < n:
            chunk = self.sock.recv(n - len(data))
            if not chunk:
                raise ConnectionError("connection to Chrome closed")
            data += chunk
        return data

    def _read_frame(self):
        b1, b2 = self._recv(2)
        n = b2 & 0x7F
        if n == 126:
            n = struct.unpack(">H", self._recv(2))[0]
        elif n == 127:
            n = struct.unpack(">Q", self._recv(8))[0]
        return b1 & 0x0F, bool(b1 & 0x80), self._recv(n)

    def call(self, method, **params):
        self.next_id += 1
        data = json.dumps({"id": self.next_id, "method": method, "params": params}).encode()
        mask, n = os.urandom(4), len(data)
        head = b"\x81" + (bytes([0x80 | n]) if n < 126 else b"\xfe" + struct.pack(">H", n) if n < 65536
                          else b"\xff" + struct.pack(">Q", n))
        self.sock.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))
        msg = b""
        while True:
            op, fin, payload = self._read_frame()
            if op == 8:
                raise ConnectionError("Chrome closed the connection")
            if op not in (0, 1, 2):
                continue
            msg += payload
            if not fin:
                continue
            reply, msg = json.loads(msg), b""
            if reply.get("id") == self.next_id:
                if "error" in reply:
                    raise RuntimeError("%s: %s" % (method, reply["error"].get("message")))
                return reply.get("result", {})

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


KEYS = {"space": dict(key=" ", code="Space", windowsVirtualKeyCode=32, text=" "),
        "escape": dict(key="Escape", code="Escape", windowsVirtualKeyCode=27),
        "enter": dict(key="Enter", code="Enter", windowsVirtualKeyCode=13, text="\r")}


def port_open(port):
    try:
        urllib.request.urlopen("http://127.0.0.1:%d/json/version" % port, timeout=1).read()
        return True
    except Exception:
        return False


class Game:
    """The running game: screenshots in, clicks/keys out."""

    def __init__(self, port=9222, watch=False):
        self.port, self.watch, self.ws = port, watch, None

    # ---- start / attach ---------------------------------------------------- #
    def open(self, props, account, headless=False):
        if not port_open(self.port):
            page = write_game_page(account)
            user_dir = Path(props.get("external.mini-client.user.dir") or BOT_DIR)
            user_dir = user_dir if user_dir.name == "chrome-user-dir" else user_dir / "chrome-user-dir"
            args = [find_chrome(props), "--user-data-dir=%s" % user_dir, "--remote-debugging-port=%d" % self.port,
                    "--window-size=820,565", "--window-position=0,0", "--app=%s" % page.as_uri(),
                    "--no-first-run", "--disable-renderer-backgrounding", "--disable-backgrounding-occluded-windows",
                    "--disable-background-timer-throttling"]
            if headless:
                args.append("--headless=new")
            kw = ({"creationflags": 0x00000008 | 0x00000200} if os.name == "nt" else {"start_new_session": True})
            log("Starting Chrome for account %s ..." % account["name"])
            subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kw)
            for _ in range(60):
                if port_open(self.port):
                    break
                time.sleep(0.5)
            else:
                raise BotExit(EXIT_NO_GAME, "Chrome did not open its control port %d. If a game window from the old "
                                            "mini-game-on-chrome*.bat is still open, close it and try again." % self.port)
        self._attach()

    def _attach(self):
        tabs = json.load(urllib.request.urlopen("http://127.0.0.1:%d/json" % self.port, timeout=5))
        pages = [t for t in tabs if t.get("type") == "page"]
        pages.sort(key=lambda t: 0 if "bh-client" in t.get("url", "") else 1)
        if not pages:
            raise BotExit(EXIT_NO_GAME, "Chrome has no open page")
        self.ws = WebSocket(pages[0]["webSocketDebuggerUrl"])
        # fixed 800x520 viewport at scale 1: independent of window size and Windows display scaling
        self.ws.call("Emulation.setDeviceMetricsOverride", width=GAME_W, height=GAME_H, deviceScaleFactor=1, mobile=False)
        self.ws.call("Emulation.setFocusEmulationEnabled", enabled=True)   # works even when the window is not focused
        self.focus()

    def focus(self):
        try:
            self.ws.call("Runtime.evaluate", expression="var f=document.getElementById('gameiframe'); if(f) f.focus();")
        except Exception:
            pass

    # ---- eyes --------------------------------------------------------------- #
    def screenshot(self):
        r = self.ws.call("Page.captureScreenshot", format="png", optimizeForSpeed=True,
                         clip={"x": 0, "y": 0, "width": GAME_W, "height": GAME_H, "scale": 1})
        img = cv2.imdecode(np.frombuffer(base64.b64decode(r["data"]), np.uint8), cv2.IMREAD_COLOR)
        if img.shape[:2] != (GAME_H, GAME_W):
            img = cv2.resize(img, (GAME_W, GAME_H), interpolation=cv2.INTER_NEAREST)
        return Frame(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))

    # ---- hands -------------------------------------------------------------- #
    def click(self, x, y):
        if self.watch:
            return log("[watch] would click %d,%d" % (x, y))
        x, y = int(x), int(y)
        self.ws.call("Input.dispatchMouseEvent", type="mouseMoved", x=x, y=y)
        time.sleep(0.1)
        self.ws.call("Input.dispatchMouseEvent", type="mousePressed", x=x, y=y, button="left", buttons=1, clickCount=1)
        time.sleep(0.05)
        self.ws.call("Input.dispatchMouseEvent", type="mouseReleased", x=x, y=y, button="left", buttons=0, clickCount=1)

    def scroll(self, x, y, dy):
        """Mouse wheel at x,y: dy > 0 scrolls the list down, dy < 0 up."""
        if self.watch:
            return log("[watch] would scroll %d at %d,%d" % (dy, x, y))
        self.ws.call("Input.dispatchMouseEvent", type="mouseMoved", x=int(x), y=int(y))
        self.ws.call("Input.dispatchMouseEvent", type="mouseWheel", x=int(x), y=int(y), deltaX=0, deltaY=int(dy))

    def press(self, name):
        if self.watch:
            return log("[watch] would press %s" % name)
        k = KEYS[name]
        self.ws.call("Input.dispatchKeyEvent", type="keyDown" if "text" in k else "rawKeyDown", **k)
        time.sleep(0.04)
        self.ws.call("Input.dispatchKeyEvent", type="keyUp", **{a: b for a, b in k.items() if a != "text"})

    def esc(self, times=4, gap=0.4):
        for _ in range(times):
            self.press("escape")
            time.sleep(gap)


# =========================================================================== #
# Vision: text-colour templates (same idea as the original bot, whole-screen + vectorised)
# =========================================================================== #
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
        return hits[0] if hits else None

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
    """The screenshot with a caption strip on top and green boxes around every template the bot recognised."""
    bgr = cv2.cvtColor(img_rgb, cv2.COLOR_RGB2BGR)
    for name, (x, y, w, h) in boxes:
        cv2.rectangle(bgr, (x, y), (x + w, y + h), (0, 255, 0), 1)
        cv2.putText(bgr, name[:30], (x, max(10, y - 3)), cv2.FONT_HERSHEY_SIMPLEX, 0.33, (0, 255, 0), 1, cv2.LINE_AA)
    rows = []
    for ln in lines:
        rows += textwrap.wrap(ln.encode("ascii", "replace").decode(), 112) or [""]
    strip = np.full((14 * len(rows) + 10, GAME_W, 3), 32, np.uint8)
    for i, ln in enumerate(rows):
        cv2.putText(strip, ln, (6, 16 + 14 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (120, 220, 255) if i == 0 else (235, 235, 235), 1, cv2.LINE_AA)
    return np.vstack([strip, bgr])


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
        base = "%s_%s" % (time.strftime("%Y%m%d-%H%M%S"), label)
        path = d / (base + ".png")
        cv2.imwrite(str(path), cv2.cvtColor(frame.img, cv2.COLOR_RGB2BGR))
        try:
            boxes = []
            for name in self.tpl.names():
                try:
                    t = self.tpl.get(name)
                    if t.kind == "mask" or t.ink == (0, 0, 0):          # black-ink / mask templates match almost anything
                        continue
                    pos = t.find(frame, DEFAULT_MARGIN)
                except Exception:
                    continue
                if pos:
                    boxes.append((name, (pos[0], pos[1], t.w, t.h)))
            lines = ["Saved %s: %s" % (time.strftime("%H:%M:%S"), why or label),
                     "Bot was doing: %s" % (self.ctx or "-"),
                     "Screen unchanged for %d s" % self.still_for(),
                     "Known templates visible: %s" % (", ".join(n for n, _ in boxes) or "NONE - this screen needs a new template"),
                     "Last console lines:"] + [ln for ln in RECENT] + \
                    ["Clean copy for `bhbot.py template`: %s.png" % base]
            cv2.imwrite(str(d / (base + "_annotated.png")), render_annotated(frame.img, lines, boxes))
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
        for label, (since, folder, why, cd) in list(self._pending.items()):     # screenshots that were waiting for a frozen screen
            if self.still_for() >= self.STILL_SECS:
                del self._pending[label]
                path = self.save_frame(frame, folder, label, why + " (screen then froze for %d s)" % self.still_for(), cd)
                log("Screenshot saved: %s" % path)
            elif time.time() - since > 180:
                del self._pending[label]
                log("No screenshot for '%s': the screen kept changing, so the game was working normally" % label)

    def still_for(self):
        return time.time() - self.last_motion

    STILL_SECS = 10.0           # a screen must be unchanged this long before a 'stuck' screenshot is worth saving

    def evidence(self, frame, folder, label, why, cooldown=600):
        """A screenshot for YOU to look at - saved only if the game really is stuck (picture unchanged for STILL_SECS).
        While it is still moving (battle, loading, a normal quest run) the request waits; it is dropped after 3 minutes.
        The same kind is saved at most once per `cooldown` seconds."""
        if label in self._pending or time.time() - self._saved_at.get(label, 0) < cooldown:
            return None
        if self.still_for() >= self.STILL_SECS:
            path = self.save_frame(frame, folder, label, why + " (screen unchanged for %d s)" % self.still_for(), cooldown)
            log("Screenshot saved: %s" % path)
            return path
        self._pending[label] = (time.time(), folder, why, cooldown)
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
    RESUME_WAIT = 25                          # seconds the game needs to reload into the dungeon

    def resume_dungeon(self, frame):
        """'Uh oh! You were recently disconnected from a dungeon. Continue?' -> YES, then wait for the dungeon to load."""
        text, yes = self.tpl.get("resume.text"), self.tpl.get("resume.yes")
        if not text.find(frame, None):
            return False
        pos = yes.find(frame, None)
        if not pos:
            return False
        log("'Recently disconnected from a dungeon' message -> pressing YES, waiting %d s for the dungeon to load" % self.RESUME_WAIT)
        self.game.click(*yes.center_of(pos))
        time.sleep(self.RESUME_WAIT)
        return True

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

    def on_main_screen(self, frame):
        """The map with its menu icons. Needs at least 3 of them: the loading screen's big QUEST logo looks like one."""
        n = sum(1 for name in ("pvp", "quest", "raid", "world-boss", "gvg", "invasion", "expedition", "trials", "gauntlet", "bait")
                if self.tpl.get("place." + name).find(frame, None))
        return n >= 3

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
    LOAD_WAIT = 120                   # seconds to wait for the game to log in and show something
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
        # 1) the game takes a while to log in after Chrome starts: wait (up to 2 min) until anything recognisable shows
        log("Waiting for the game to load ...")
        t_start, last_note = time.time(), 0.0
        while True:
            frame = self.game.screenshot()
            if self.resume_dungeon(frame):
                t_start = time.time()
                continue
            if self.daily_login(frame):
                continue
            if time.time() - t_start >= self.LOAD_MIN and (label.find(frame) or play.find(frame) or self.on_main_screen(frame)):
                break
            if time.time() - t_start > self.LOAD_WAIT:
                self.save_frame(frame, "unknown", "fishing_game_not_loaded", "game did not show the main screen, fishing menu or fishing scene within %d s" % self.LOAD_WAIT)
                raise BotExit(EXIT_NO_ANCHOR, "the game did not load (no main screen after %d s)" % self.LOAD_WAIT)
            if time.time() - last_note > 10:
                last_note = time.time()
                log("... still loading (%d s)" % (time.time() - t_start))
            time.sleep(1)
        if icon.find(frame, None):
            self.collect_bounties("start")
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
            log("FISHING scene not visible yet (%d/5) -> ESC x2 in case a menu is in the way" % (attempt + 1))
            self.game.esc(2, gap=1.0)
            time.sleep(1.5)
        else:
            frame = self.game.screenshot()
            if not label.find(frame):
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
                   (2600, 2899, 14, "yellow"), (2900, 3169, 14, "cyan"), (3170, 4283, 15, "yellow"), (4284, 4575, 15, "cyan"),
                   (4415, 6014, 16, "yellow"), (6015, 6424, 16, "cyan"), (6425, 7424, 17, "yellow"), (7425, 8314, 17, "cyan"),
                   (8315, 9464, 18, "yellow"), (9465, 9754, 18, "cyan"), (9755, 11734, 19, "yellow"), (11735, 12487, 19, "cyan"),
                   (12488, 13494, 20, "yellow"), (13495, 14360, 20, "cyan"), (14361, 15518, 21, "yellow"), (15519, 25001, 21, "cyan")]


def gauntlet_tier_of(number):
    """Difficulty number -> (tier, 'yellow'|'cyan'), or None when it is outside the table."""
    for lo, hi, tier, kind in GAUNTLET_RANGES:
        if lo <= number <= hi:
            return tier, kind or "cyan"
    return None


def apply_overrides(base, ov):
    """Profile (from readonly.<name>.user-config.properties and the command line) + the GUI overrides -> (profile, disabled events, notes)."""
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
        notes.append("Quest: zone %s dungeon %s mode %s" % (p["quest_zone"] or "auto", p["quest_dungeon"] or "auto", p["quest_mode"]))
    d = sec("pvp")
    if d:
        p["pvp_target"] = num(d, "target")
        notes.append("PVP: target %s" % (p["pvp_target"] or "default"))
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
        p["gauntlet_pick"] = "lowest" if str(d.get("pick", "highest")).lower() == "lowest" else "highest"
        notes.append("Gauntlet: tier %s, %s drops, %s value" % (tier or "(unchanged)", "legendary (yellow)" if kind == "yellow" else "set+mythic (light blue)", p["gauntlet_pick"]))
    d = sec("invasion")
    if d:
        p["invasion_wave"] = num(d, "wave")
        notes.append("Invasion: wave %s" % (p["invasion_wave"] or "top of the list"))
    ev = ov.get("events") if isinstance(ov.get("events"), dict) else {}      # older overrides.json: one 'events' section
    ev = dict(ev, **{t: ov[t] for t in ("gvg", "expedition", "invasion", "trials", "gauntlet") if isinstance(ov.get(t), dict)})
    if True:
        for task in ("gvg", "invasion", "expedition", "trials", "gauntlet"):
            e = ev.get(task)
            if isinstance(e, dict) and e.get("on") and not e.get("enabled", True):
                disabled.add(task)
        if disabled:
            notes.append("switched off: " + ", ".join(sorted(disabled)))
    return p, disabled, notes


def load_profile(name):
    p = load_props(BOT_DIR / ("readonly.%s.user-config.properties" % name)) if name else {}
    num = lambda key, default: int(p.get(key, default) or default)
    boss = (p.get("ig.user.world-boss.name") or "").strip().lower() or WB_BY_OLD_LEVEL.get(num("ig.user.world-boss.level", 0), "")
    return {"name": name, "found": bool(p), "quest_mode": num("ig.user.quest.mode", 3), "pvp_target": num("ig.user.pvp.target", 0),
            "raid_level": num("ig.user.raid.level", 0), "raid_mode": num("ig.user.raid.mode", 3),
            "wb_boss": boss, "wb_tier": num("ig.user.world-boss.tier", 0), "wb_mode": num("ig.user.world-boss.mode", 3),
            "quest_zone": 0, "quest_dungeon": 0, "wb_private": True, "wb_players": 1, "wb_wait": 15,
            "trials_tier": 0, "trials_kind": "cyan", "trials_pick": "highest",
            "gauntlet_tier": 0, "gauntlet_kind": "cyan", "gauntlet_pick": "highest", "invasion_wave": 0}


class Afk(Bot):
    PVP_FIGHT_ROW = 54            # pixels between the PVP 'Fight' buttons
    GIVE_UP_NAV = 5               # clicked a map icon this many times and nothing opened -> skip the task for a while

    def __init__(self, game, templates, props, wanted, profile, ear=False, tick=1.0, retry=10):
        super().__init__(game, templates, props)
        self.wanted, self.profile, self.ear, self.tick, self.retry = wanted, profile, ear, tick, retry
        self.base_profile, self.disabled, self._ov_mtime = dict(profile), set(), -1
        self._lobby, self._wb_solo = None, False
        self._tr = dict(done=False, tries=0, seen=0.0, stage="", scrolls=0, clicked=0.0, top=False, ups=0)
        self._gt = dict(done=False, tries=0, seen=0.0, stage="", scrolls=0, clicked=0.0, top=False, ups=0)
        self._iv = dict(done=False, tries=0, seen=0.0, stage="", clicked=0.0, ups=0, top=False, last_first=None, rate=None, hi=None, last_k=0, scrolls=0, fallback=False, bad=0, closes=0)
        self.nav_block_until = 0.0         # no map-icon clicks before this time (an activity is loading)
        self.lobby_seen = self.battle_seen = 0.0     # when a World Boss lobby / a battle (AUTO button) was last on screen
        self.activity = None              # task whose button was clicked last ('pvp' = no yellow-arrow checks while it runs)
        self._qz = dict(stage="left", n=0, steps=0, seen=0.0)
        self._was_waiting = set()
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
        if m == self._ov_mtime:
            return
        first = self._ov_mtime == -1
        self._ov_mtime = m
        self.profile, self.disabled, notes = apply_overrides(self.base_profile, load_overrides())
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
        if due("talk", 1) and self.activity != "pvp":
            t = self.tpl.get("globally.talkRightArrow")
            pos = None if self.tpl.get("dungeons.zones").find(frame, None) else (t.find(frame, 60) or t.find(frame, None))   # the zone map's yellow arrow looks the same; prefer the arrow at its usual spot (a grey carousel arrow can look alike)
            if pos and self.difficulty_screen(frame):                     # NORMAL / HARD / HEROIC picker: its '>' arrows and title are not a dialogue
                pos = None
            plate = self.dialogue_plate(frame) if pos else None           # a name plate (any enemy-tier colour) must be there too
            if pos and plate:
                box, rgb = plate
                if self._talk_box is None or max(abs(a - c) for a, c in zip(box, self._talk_box)) > 6:
                    self._talk_box, self._talk_since, self._talk_clicks = box, now, 0
                elif now - self._talk_since >= 1.5:                     # name + arrow have stood still for a moment
                    self._talk_clicks += 1
                    if self._talk_clicks > 24:                          # a real dialogue ends long before this
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
            else:
                self._talk_box = None
        if due("buy", 2):                                       # 'not enough ...' for a task that is already paused: just close it
            for task in self.tasks:
                done = TASKS[task].get("done")
                if done and not self.is_ready(task) and self.tpl.get(done).find(frame, None):
                    self.say("%s: 'not enough ... buy more?' dialog -> ESC x4, 1 s apart" % TASKS[task]["label"], every=10)
                    self.game.esc(4, gap=1.0)
                    return True
        if due("items", 2):
            if self.tpl.get("globally.items-popup").find(frame, None):
                self.say("'Items' popup -> ESC", every=10)
                self.game.press("escape")
                return True
        if due("fishranks", 2):                                 # 'FISHING RANKS' window (the game opens it by itself now and then)
            if self.tpl.get("globally.fishing-ranks").find(frame):
                self._ranks_n = getattr(self, "_ranks_n", 0) + 1
                if self._ranks_n % 2:
                    self.say("'Fishing Ranks' window -> clicking its red X", every=10)
                    self.game.click(668, 75)
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
        if due("news", 60):
            t, pos = self.see("globally.news", frame, None)
            if pos:
                self.say("News popup -> ESC")
                self.game.press("escape")
                return True
        if due("daily", 10):
            t, pos = self.see("globally.dailyRewards", frame, None)
            if pos:
                self.say("Daily rewards popup -> claiming and closing")
                self.tap(t, pos)
                self.game.esc(6, gap=1.0)
                self.next_small["daily"] = now + 240
                return True
        if due("leave", 10):
            t, pos = self.see("globally.leave-this-dungeon", frame, None)
            if pos:
                self.say("'Leave this dungeon?' popup -> ESC (staying)")
                self.game.press("escape")
                return True
        return False

    # ---- things that can show up at any time -------------------------------- #
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
        for name, text, key in (("globally.confirm-start-not-full-team", "Start with a non-full team? -> confirming", "space"),
                                ("globally.familiars-title", "Familiars page is open -> ESC x4, 1 s apart", "escape")):
            t, pos = self.see(name, frame, None)
            if pos:
                self.say(text, every=10)
                if key != "escape":
                    self.tap(t, pos)
                yes = self.tpl.get("resume.yes").find(frame, None) if name == "globally.confirm-start-not-full-team" else None
                if yes:
                    self.game.click(*self.tpl.get("resume.yes").center_of(yes))        # the YES button of the 'team is not full' question
                elif key == "escape":
                    self.game.esc(4, gap=1.0)
                else:
                    self.game.press(key)
                if key == "enter":
                    time.sleep(1)
                    self.game.esc(2)
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
                if self.resume_dungeon(fr) or self.daily_login(fr):
                    time.sleep(1)
                    continue
                if time.time() - t0 >= self.HERO_LOAD_WAIT and self.on_main_screen(fr):
                    break
            except (ConnectionError, OSError, ValueError) as e:        # the page may blink while it reloads
                log("(waiting for the game to reload: %s)" % e)
            time.sleep(1)
        self.cur_char = 1 - self.cur_char
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

    def difficulty_screen(self, frame):
        """The dungeon's NORMAL / HARD / HEROIC picker (all three buttons visible)."""
        return all(self.tpl.get(n).find(frame, AFK_MARGIN) for n in ("dungeons.normal", "dungeons.hard", "dungeons.heroic"))

    def quest_difficulty(self, frame):
        if not self.difficulty_screen(frame):
            return False
        name = {1: "dungeons.normal", 2: "dungeons.hard", 3: "dungeons.heroic"}.get(self.profile["quest_mode"], "dungeons.heroic")
        t, pos = self.see(name, frame)
        self.say("Quest: difficulty screen -> choosing %s" % name.split(".", 1)[1], every=10)
        self.tap(t, pos)
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
            dy = 0
            if name == "pvp-arena.fight" and self.profile["pvp_target"] > 1:
                dy = (self.profile["pvp_target"] - 1) * self.PVP_FIGHT_ROW
            self.say("%s: clicking '%s'%s" % (spec["label"], name.split(".", 1)[1],
                                              " (target %d)" % self.profile["pvp_target"] if dy else ""), every=20)
            self.tap(t, pos, dy)
            if any(k in name for k in ("accept", "fight", "enter", "start", "rerun")):      # these start a battle / dungeon: it needs time to load
                self.nav_block_until = time.time() + self.NAV_GRACE
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
            self.say("Quest zone screen is open but Quest is paused -> ESC")
            self.game.press("escape")
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
        self.zone_seen = time.time()
        if override_cands:
            cands = override_cands
        elif qz and not cands and not qd:
            pass                                                 # zone override without a dungeon: normal rule, inside that zone
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
                    self.game.press("escape")
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
        entered = False
        for p, what, t in cands[:6]:
            log("Quest: clicking %s at %d,%d" % (what, p[0], p[1]))
            self.game.click(*p) if t is None else self.tap(t, p)
            time.sleep(2)
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
        if entered:
            time.sleep(1)
            f3 = self.game.screenshot()
            te, pe = self.see("dungeons.enter", f3, None)
            if pe:
                self.tap(te, pe)
            else:
                mode = {1: "dungeons.normal", 2: "dungeons.hard", 3: "dungeons.heroic"}.get(self.profile["quest_mode"], "dungeons.heroic")
                tm, pm = self.see(mode, f3)
                if pm:
                    log("Quest: choosing %s mode" % mode.split(".", 1)[1])
                    self.tap(tm, pm)
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
        if all(t.get(n).find(frame, None) for n in names.values()):                           # difficulty buttons
            mode = self.profile["raid_mode"] if self.profile["raid_mode"] in names else 3
            tm = t.get(names[mode])
            self.say("Raid: choosing %s" % names[mode].split(".")[1], every=10)
            self.game.click(*tm.center_of(tm.find(frame, None)))
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
                S.update(done=False, tries=0, stage="", scrolls=0, top=False, ups=0)     # left the Trials page: next visit starts over
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
            self.say(label + ": scrolling the list %s (looking for tier %d)" % ("down" if direction > 0 else "up", tier), every=0)
            self.game.scroll(397, 300, 120 * direction)
            time.sleep(0.6)
            return True
        # --- the exact-number list (highest / lowest value inside the chosen range)
        S["stage"] = "numbers"
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

    def wave_rows(self, frame):
        """Numbers in the visible part of the Invasion WAVE list: [(y, value)], top to bottom. The list counts down by 1, so every
        row is rebuilt from the most common 'value + row index' and a misread digit cannot send us to the wrong wave."""
        reg = frame.img[140:445, 330:450].astype(int)
        m = reg.min(axis=2) >= 235
        bands, start = [], None
        for i, v in enumerate(list(m.any(axis=1)) + [False]):
            if v and start is None:
                start = i
            elif not v and start is not None:
                if i - start >= 13:                                   # a whole number (borders are 3 px, cut-off rows are shorter)
                    bands.append((start, i))
                start = None
        samples, reads = self.digit_samples(), []
        for a, b in bands:
            row, text, x0, cols = m[a:b], "", None, []
            for x, v in enumerate(list(row.any(axis=0)) + [False]):
                if v and x0 is None:
                    x0 = x
                elif not v and x0 is not None:
                    cols.append((x0, x))
                    x0 = None
            for c0, c1 in cols:
                cut = row[:, c0:c1]
                ys = np.nonzero(cut.any(axis=1))[0]
                cut = cut[ys.min():ys.max() + 1]
                g = cv2.resize(cut.astype(np.uint8) * 255, (10, 14), interpolation=cv2.INTER_AREA) > 127
                d, ch = min((int((g != r).sum()), ch) for ch, r in samples) if samples else (999, "?")
                text += ch if d <= 35 else "?"
            reads.append(((a + b) // 2 + 140, int(text) if text.isdigit() else None))
        votes = {}
        for i, (y, v) in enumerate(reads):
            if v is not None:
                votes[v + i] = votes.get(v + i, 0) + 1
        if not votes:
            return []
        base, n = max(votes.items(), key=lambda kv: kv[1])
        if n < 2 and len(reads) > 1:
            return []
        return [(y, base - i) for i, (y, v) in enumerate(reads)]

    def invasion_wave(self, frame):
        """Invasion menu -> arrow under WAVE -> list -> wheel to the top -> (top wave | the wave you chose) -> PLAY is the chain's job."""
        S, now, t = self._iv, time.time(), self.tpl
        title = t.get("trials.difficulty-title").find(frame, None)
        menu = t.get("invasion.title").find(frame, None)
        play = t.get("invasion.play").find(frame, None)
        if title and S["stage"] == "list":
            S["seen"] = now
            rows = self.wave_rows(frame)
            want = 0 if S["fallback"] else int(self.profile.get("invasion_wave", 0) or 0)

            def choose(y, why):
                self.say("Invasion: choosing %s" % why, every=0)
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
                return choose(157, "the top wave (the numbers could not be read)")
            hi, lo = rows[0][1], rows[-1][1]
            if not want:
                return choose(rows[0][0], "the top wave (%d)" % hi)
            hit = next((y for y, v in rows if v == want), None)
            if hit:
                return choose(hit, "wave %d" % want)
            if S["hi"] is not None and S["last_k"]:                       # how far one wheel step moves the list
                moved = abs(S["hi"] - hi)
                if moved == 0:                                            # the list did not move: that was its end
                    S["fallback"], S["top"], S["ups"], S["last_first"] = True, False, 0, None
                    log("Invasion: wave %d is not in the list (it runs %d down to %d) -> taking the top wave" % (want, hi, lo))
                    return True
                S["rate"] = moved / S["last_k"]
            if want > hi:
                if S["top"] and S["scrolls"] == 0:                        # asked for more than the top wave
                    log("Invasion: wave %d is higher than the top wave (%d) -> taking the top wave" % (want, hi))
                    return choose(rows[0][0], "the top wave (%d)" % hi)
                dist, sign = want - hi, -1
            else:
                dist, sign = lo - want, 1
            k = 1 if not S["rate"] else max(1, min(30, int(0.8 * dist / S["rate"])))
            S["scrolls"] += 1
            if S["scrolls"] > 60:
                S["fallback"], S["top"], S["ups"], S["last_first"] = True, False, 0, None
                log("Invasion: could not reach wave %d -> taking the top wave" % want)
                return True
            S["hi"], S["last_k"] = hi, k
            self.say("Invasion: scrolling to wave %d (list shows %d..%d)" % (want, hi, lo), every=0)
            self.game.scroll(397, 300, 120 * k * sign)
            time.sleep(0.5)
            return True
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
            if now - S.get("play_seen", 0) > 3:
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
        if self.map_visible(frame):
            if still < 3:
                return
            self.map_miss += 1
            names = ", ".join(TASKS[t]["label"] for t in ready)
            self.say("On the map, but no icon for: %s (look %d of 3)" % (names, self.map_miss), every=20)
            if self.map_miss >= 3:
                self.evidence(frame, "unknown", "icons_missing", "on the map but no icon for: %s" % names, 300)
                for t in ready:
                    self.block(t, 10, "its map icon is not on screen")
                log("Icons for %s were not found on the map (an event overlay? the screenshot, if saved, is in out/unknown)" % names)
                self.map_miss = 0
            return
        ta = self.tpl.get("globally.talkRightArrow")
        # Only the right-hand arrow of a popup counts (gear / pet pages: x ~650). Never in PVP, and never the yellow turn
        # marker over a hero in a battle (it sits on the left side of the screen).
        apos = None if self.activity == "pvp" else next((p for p in ta.find_all(frame, margin=None) if p[0] >= 600), None)
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
        log("Profile: %s%s | quest mode %d | pvp target %s | raid %s (mode %d) | world boss %s tier %s" % (
            self.profile["name"] or "-", "" if self.profile["found"] else " (file not found, using defaults)",
            self.profile["quest_mode"], self.profile["pvp_target"] or "default", self.profile["raid_level"] or "-", self.profile["raid_mode"],
            self.profile["wb_boss"] or "-", "%s%s" % (self.profile["wb_tier"] or "default", ", " + self.MODE_NAMES.get(self.profile["wb_mode"], "") if self.profile["wb_mode"] else "")))
        log("Waiting for the game to load ...")
        t_start = time.time()
        while time.time() - t_start < 120:                  # login: the daily reward screen may come first
            frame = self.game.screenshot()
            if self.resume_dungeon(frame):
                t_start = time.time()
                continue
            if self.daily_login(frame):
                continue
            if time.time() - t_start >= self.LOAD_MIN and self.on_main_screen(frame):
                break
            time.sleep(1)
        self.collect_bounties("start")
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
            self.housekeeping(frame)
            if self.always_on(frame) or self.keepalive(frame) or self.small_tasks(frame) or self.dismiss_known(frame):
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
                self.say("Waiting - " + "; ".join(left(t) for t in self.tasks), every=300)
                time.sleep(getattr(self, "slow_wait", SLOW_WAIT))
                continue
            # A battle (the AUTO tab is on screen) or one that is still loading (just clicked accept / fight): nothing to choose
            # here. Pale enemies and the snowy background can look like menu text, so the menu screens are not even looked at.
            busy = "battle" if self.tpl.get("globally.auto-green").find(frame, AFK_MARGIN) else ("loading" if time.time() < self.nav_block_until else ("switch" if wanting else False))
            if busy == "switch":
                self.say("Alternate characters: finishing the current run, then switching hero before the restart", every=120)
                if self.tpl.get("dungeons.zones").find(frame, AFK_MARGIN):          # leave the zone map: the hero page opens from the main map
                    self.game.press("escape")
                    continue
            elif busy:
                self.say("A battle is running or loading - not touching the menus", every=300)
            if not busy and self.tpl.get("dungeons.zones").find(frame, AFK_MARGIN):
                self.quest_screen(frame, "quest" in ready)
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
            if not busy and "trials" in ready and self.trials_difficulty(frame):
                self.acted()
                continue
            if not busy and "gauntlet" in ready and self.trials_difficulty(frame, "gauntlet"):
                self.acted()
                continue
            if any(self.try_chain(t, frame, busy) for t in sorted(ready, key=lambda t: t != self.activity) if TASKS[t]["chain"] or TASKS[t].get("done")):   # the activity we are in goes first (the Team screen's ACCEPT is shared)
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


def cmd_selftest(args):
    """Paste every template onto a fake screen and check the matcher finds it - and only it - where it belongs."""
    tpls, bad, n = Templates(), [], 0
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
    p.add_argument("--retry", type=int, default=10, help="longest pause (minutes) after running out of tickets/energy before looking again (default 10)")
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
