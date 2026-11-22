"""engine/game.py - the game window: Chrome, the debugging connection, screenshots, clicks and keys."""
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
        self.dialog = None                       # a JavaScript alert/confirm that is open in the page (Unity crash message ...)

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
            if reply.get("method") == "Page.javascriptDialogOpening":
                self.dialog = reply.get("params") or {"message": "?"}
            elif reply.get("method") == "Page.javascriptDialogClosed":
                self.dialog = None
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
                    "--force-device-scale-factor=1",         # the window is 820x565 REAL pixels whatever the Windows display scaling (125 / 150 % ...)
                    "--no-first-run", "--disable-renderer-backgrounding", "--disable-backgrounding-occluded-windows",
                    "--disable-background-timer-throttling"]
            if os.name != "nt":
                # LINUX / MACOS: NOT TESTED. Chrome refuses to start as root without --no-sandbox (containers, some servers),
                # and a Linux box with no desktop (no DISPLAY) can only run it headless.
                if hasattr(os, "geteuid") and os.geteuid() == 0:
                    args.append("--no-sandbox")
                if sys.platform != "darwin" and not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
                    headless = True
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
        try:
            self.ws.call("Page.enable")                                     # so that alert() dialogs are reported to us (Unity crash messages)
        except Exception:
            pass
        self.focus()
        self.check_view()

    def focus(self):
        try:
            self.ws.call("Runtime.evaluate", expression="var f=document.getElementById('gameiframe'); if(f) f.focus();")
        except Exception:
            pass

    def close_dialog(self):
        """Press OK on an open alert() dialog (so Chrome is not left blocked)."""
        try:
            self.ws.call("Page.handleJavaScriptDialog", accept=True)
        except Exception:
            pass
        self.ws.dialog = None

    def health(self):
        """Is the Chrome tab alive? -> (True, "") or (False, why). Asks the page a trivial question with a short timeout: a crashed or hung
        renderer ('Aw, Snap!'), an error page (chrome-error://) or a closed connection all answer wrongly or not at all."""
        if self.ws.dialog:
            return False, "a browser dialog is open: %s" % str(self.ws.dialog.get("message", "?"))[:200]
        old = self.ws.sock.gettimeout()
        try:
            self.ws.sock.settimeout(8)
            r = self.ws.call("Runtime.evaluate", expression="JSON.stringify({h: location.href, r: document.readyState})", returnByValue=True)
            v = r.get("result", {}).get("value")
            if not v:
                return False, "the page returned nothing (%s)" % (r.get("exceptionDetails", {}).get("text", "no value"),)
            info = json.loads(v)
            if str(info.get("h", "")).startswith("chrome-error:"):
                return False, "Chrome is showing an error page (%s)" % info.get("h")
            return True, ""
        except (OSError, ConnectionError, RuntimeError, ValueError) as e:
            if self.ws.dialog:                                              # an open alert() blocks the page: that is why it did not answer
                return False, "a browser dialog is open: %s" % str(self.ws.dialog.get("message", "?"))[:200]
            return False, "no proper answer from Chrome (%s: %s)" % (type(e).__name__, e)
        finally:
            try:
                self.ws.sock.settimeout(old)
            except OSError:
                pass

    def check_view(self):
        """Log how big the game window really is. The pictures are only recognised pixel for pixel, so a window smaller than the game
        (Windows display scaling above 100 % with an old window) shows as 'still loading' while the game is up."""
        try:
            w = self.ws.call("Browser.getWindowForTarget")
            b = w.get("bounds", {})
            log("[display] game window %sx%s, game picture %dx%d (Chrome flag --force-device-scale-factor=1)" % (b.get("width"), b.get("height"), GAME_W, GAME_H))
            if b.get("width") and (b["width"] < GAME_W or b.get("height", GAME_H) < GAME_H):
                log("[display] WARNING: the window is smaller than the game. Close the game window and start again (the bot opens a new one at the right size).")
        except Exception:
            pass

    # ---- eyes --------------------------------------------------------------- #
    def screenshot(self):
        r = self.ws.call("Page.captureScreenshot", format="png", optimizeForSpeed=True,
                         clip={"x": 0, "y": 0, "width": GAME_W, "height": GAME_H, "scale": 1})
        img = cv2.imdecode(np.frombuffer(base64.b64decode(r["data"]), np.uint8), cv2.IMREAD_COLOR)
        if img.shape[:2] != (GAME_H, GAME_W):
            if not getattr(self, "_size_warned", False):
                self._size_warned = True
                log("[display] WARNING: the screenshot is %dx%d, not %dx%d: stretched pictures are not recognised (display scaling?)" % (img.shape[1], img.shape[0], GAME_W, GAME_H))
            img = cv2.resize(img, (GAME_W, GAME_H), interpolation=cv2.INTER_NEAREST)
        from engine.vision import Frame                       # (vision.py comes after this file: imported here, when first needed)
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
