"""
BH Bot Controller 
"""
import json
import os
import re
import subprocess
import sys
import threading
import time
import queue
from pathlib import Path

import psutil


class Tip:
    """Small hover note: Tip(widget, "text"). Shows after a short pause, hides when the mouse leaves."""

    def __init__(self, widget, text):
        self.widget, self.text, self.win, self.job = widget, text, None, None
        widget.bind("<Enter>", self._later, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _later(self, _e=None):
        self._hide()
        self.job = self.widget.after(500, self._show)

    def _show(self):
        import tkinter as tk                      # imported here: this file only loads tkinter inside main()
        x, y = self.widget.winfo_rootx() + 12, self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        self.win = tk.Toplevel(self.widget)
        self.win.wm_overrideredirect(True)
        self.win.wm_geometry("+%d+%d" % (x, y))
        tk.Label(self.win, text=self.text, justify="left", wraplength=420, background="#ffffe0", relief="solid",
                 borderwidth=1, padx=6, pady=4).pack()

    def _hide(self, _e=None):
        if self.job:
            self.widget.after_cancel(self.job)
            self.job = None
        if self.win:
            self.win.destroy()
            self.win = None

IS_WIN = os.name == "nt"
APP_DIR = Path(__file__).resolve().parent          # the BHPyBot folder
DEFAULT_BOT_DIR = APP_DIR.parent if APP_DIR.name.lower() in ("bhpybot", "bhbwrapper") else APP_DIR   # the main folder (holds ~start_gui.bat and your account files)
SETTINGS_FILE = APP_DIR / "bh_gui_settings.json"
OVERRIDES_FILE = APP_DIR / "overrides.json"        # read by bhbot.py (live); written by the override buttons below

SCRIPT_EXT = ".bat" if IS_WIN else ".sh"
HIDDEN_SCRIPTS = {"_", "web.bot", "steam.bot", "help", "bot", "client"}  # internal launchers
JAVA_NAMES = {"java", "javaw", "java.exe", "javaw.exe"}
GAME_NAME_PARTS = ("chrome",)  # the bot's mini game client; spared on pause / (by default) on stop

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
EXIT_RE = re.compile(r"exit code:\s*(-?\d+)", re.I)
PAUSE_PROMPT_RE = re.compile(r"press any key", re.I)  # cmd's `pause` (English Windows)

NO_SELECTION = "(none)"

# Lines the "Compact log" button hides: the bot repeats them every few seconds while it just waits.
NOISE_RE = re.compile(r"(In a battle - waiting|Screen is changing|\(still\)\s*$|still loading|Auto is OFF in this battle - turning it on in"
                      r"|Just entered an activity|\[watch\])", re.I)


# --------------------------------------------------------------------------- #
# Process control (no GUI dependencies, so it can be tested on its own)
# --------------------------------------------------------------------------- #
def is_game(proc):
    try:
        name = proc.name().lower()
    except psutil.Error:
        return False
    return any(part in name for part in GAME_NAME_PARTS)


def clean_old_files(folder, days):
    """Deletes files in `folder` (and empty sub-folders) last changed more than `days` days ago. Returns (files deleted, bytes freed)."""
    folder = Path(folder)
    if not folder.is_dir() or days < 0:
        return 0, 0
    cutoff, n, freed = time.time() - days * 86400, 0, 0
    for p in folder.rglob("*"):
        try:
            if p.is_file() and p.stat().st_mtime < cutoff:
                size = p.stat().st_size
                p.unlink()
                n, freed = n + 1, freed + size
        except OSError:
            pass
    for d in sorted((p for p in folder.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
        try:
            d.rmdir()                                   # only succeeds when empty
        except OSError:
            pass
    return n, freed


def kill_chrome(port=9222):
    """Close the game window the bot started: every Chrome whose command line has the bot's debugging port or profile folder
    (plus their helper processes). The player's own Chrome has neither, so it is left alone."""
    victims = []
    for p in psutil.process_iter():
        try:
            if "chrome" not in p.name().lower():
                continue
            cl = " ".join(p.cmdline())
        except psutil.Error:
            continue
        if ("remote-debugging-port=%d" % port) in cl or "chrome-user-dir" in cl:
            victims.append(p)
            try:
                victims.extend(p.children(recursive=True))
            except psutil.Error:
                pass
    seen, uniq = set(), []
    for p in victims:
        if p.pid not in seen:
            seen.add(p.pid)
            uniq.append(p)
    for p in uniq:
        try:
            p.kill()
        except psutil.Error:
            pass
    psutil.wait_procs(uniq, timeout=5)
    return len(uniq)


class ScriptRun:
    """One running script. Emits events through `events` (a queue.Queue):
         ("log", text) ("state", "running"|"paused") ("done", ok, user_stopped)
    """

    def __init__(self, script_path, workdir, events):
        self.script = Path(script_path)
        self.workdir = Path(workdir)
        self.events = events
        self.proc = None
        self.paused = False
        self._suspended = []
        self.user_stopped = False
        self.nonzero_exit = False   # the bot printed "exit code: N" with N != 0
        self.last_exit = None       # N of the last "exit code: N" line (7 = stuck -> restart, 8 = stuck -> shut down)
        self._seen_java = False
        self._idle_ticks = 0
        self._done = threading.Event()
        self._lock = threading.Lock()
        self._fin_lock = threading.Lock()

    # -- lifecycle ---------------------------------------------------------- #
    def start(self):
        if IS_WIN:
            cmd = ["cmd.exe", "/d", "/c", "call", str(self.script)]
            flags = subprocess.CREATE_NO_WINDOW
        else:
            cmd = ["bash", str(self.script)]
            flags = 0
        self.proc = subprocess.Popen(
            cmd, cwd=str(self.workdir), stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            bufsize=0, creationflags=flags,
        )
        self.events.put(("state", "running"))
        threading.Thread(target=self._read_output, daemon=True).start()
        threading.Thread(target=self._monitor, daemon=True).start()

    def _read_output(self):
        fd = self.proc.stdout.fileno()
        tail = ""
        while True:
            try:
                chunk = os.read(fd, 4096)
            except OSError:
                break
            if not chunk:
                break
            text = ANSI_RE.sub("", chunk.decode("utf-8", errors="replace")).replace("\r\n", "\n").replace("\r", "\n")
            self.events.put(("log", text))
            tail = (tail + text)[-300:]
            for m in EXIT_RE.finditer(text):
                self.last_exit = int(m.group(1))
                if int(m.group(1)) != 0:
                    self.nonzero_exit = True
            # Release the `pause` at the end of the bot's .bat files so the script can finish
            # (or move on to its next command). We only get here if the prompt text was seen.
            if PAUSE_PROMPT_RE.search(tail):
                tail = ""
                self.send_input("")
        self._finish()

    def _monitor(self):
        """Fallback for non-English Windows where we can't recognise the `pause` text:
        if the script has run java before but nothing is running now, press Enter."""
        while not self._done.is_set():
            time.sleep(1)
            if self.paused or self.proc.poll() is not None:
                continue
            try:
                kids = psutil.Process(self.proc.pid).children(recursive=True)
            except psutil.Error:
                continue
            if any(k.name().lower() in JAVA_NAMES for k in self._safe(kids)):
                self._seen_java, self._idle_ticks = True, 0
            elif self._seen_java:
                self._idle_ticks += 1
                if self._idle_ticks >= 5:
                    self._idle_ticks = 0
                    self.send_input("")

    @staticmethod
    def _safe(procs):
        for p in procs:
            try:
                p.name()
                yield p
            except psutil.Error:
                pass

    def _finish(self):
        with self._fin_lock:            # reader thread and stop() can both get here
            if self._done.is_set():
                return
            self._done.set()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        ok = (not self.user_stopped) and (not self.nonzero_exit) and (self.proc.returncode in (0, None))
        self.events.put(("done", ok, self.user_stopped))

    @property
    def alive(self):
        return self.proc is not None and not self._done.is_set()

    # -- controls ----------------------------------------------------------- #
    def send_input(self, line):
        try:
            self.proc.stdin.write((line + "\n").encode())
            self.proc.stdin.flush()
        except (OSError, ValueError):
            pass

    def _tree(self):
        try:
            root = psutil.Process(self.proc.pid)
            return [root] + root.children(recursive=True)
        except psutil.Error:
            return []

    def pause(self):
        with self._lock:
            if self.paused or not self.alive:
                return
            self._suspended = [p for p in self._tree() if not is_game(p)]
            for p in self._suspended:
                try:
                    p.suspend()
                except psutil.Error:
                    pass
            self.paused = True
        self.events.put(("state", "paused"))

    def resume(self):
        with self._lock:
            if not self.paused:
                return
            for p in self._suspended:
                try:
                    p.resume()
                except psutil.Error:
                    pass
            self._suspended = []
            self.paused = False
        self.events.put(("state", "running"))

    def stop(self, kill_game=False):
        """Force stop: kill the whole bot tree right now."""
        self.user_stopped = True
        with self._lock:
            tree = self._tree()
            victims = [p for p in tree if kill_game or not is_game(p)]
            # also take the game's helper processes down only if asked
            for p in reversed(victims):          # children first, root last
                try:
                    p.kill()
                except psutil.Error:
                    pass
            psutil.wait_procs(victims, timeout=5)
            self.paused = False
            self._suspended = []
        try:
            self.proc.stdout.close()
        except Exception:
            pass
        self._finish()


# --------------------------------------------------------------------------- #
# Game window helper (Windows only): bring the game forward and press ESC
# --------------------------------------------------------------------------- #
GAME_WINDOW_TITLE = "Bit Heroes"       # <title> of bh-client/index*.html
GAME_WINDOW_CLASS = "Chrome_WidgetWin_1"


def press_esc_in_game(times=4, gap=0.4, settle=1.0):
    """Focus the mini-client window and press ESC `times` times.
    Returns (ok, message). Uses real keyboard input, same as the bot itself."""
    if not IS_WIN:
        return False, "ESC reset is only available on Windows"
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]

    found = []
    enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def visit(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        title, cls = ctypes.create_unicode_buffer(256), ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, title, 256)
        user32.GetClassNameW(hwnd, cls, 256)
        if cls.value == GAME_WINDOW_CLASS and title.value.strip() == GAME_WINDOW_TITLE:
            found.append(hwnd)
        return True

    user32.EnumWindows(enum_proc(visit), 0)
    if not found:
        return False, "game window titled '%s' not found" % GAME_WINDOW_TITLE
    hwnd = found[0]

    KEYUP, VK_MENU, VK_ESC = 0x0002, 0x12, 0x1B
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)                      # SW_RESTORE
    user32.keybd_event(VK_MENU, 0, 0, 0)                # tap Alt: lets us take the foreground
    user32.keybd_event(VK_MENU, 0, KEYUP, 0)
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.4)
    scan = user32.MapVirtualKeyW(VK_ESC, 0)
    for _ in range(times):
        user32.keybd_event(VK_ESC, scan, 0, 0)
        time.sleep(0.05)
        user32.keybd_event(VK_ESC, scan, KEYUP, 0)
        time.sleep(gap)
    time.sleep(settle)
    return True, "pressed ESC x%d" % times


# --------------------------------------------------------------------------- #
# Settings + script discovery
# --------------------------------------------------------------------------- #
def load_settings():
    try:
        return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_settings(data):
    try:
        SETTINGS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError:
        pass


def discover_scripts(bot_dir, extras):
    found = {}
    for folder in (APP_DIR / "Scripts", APP_DIR, bot_dir):        # BHPyBot/Scripts first; your own scripts may also sit in BHPyBot or the main folder
        if folder.is_dir():
            for f in sorted(folder.glob("*" + SCRIPT_EXT)):
                if f.stem in HIDDEN_SCRIPTS or f.stem.endswith(".bot") or f.stem.lstrip("~") == "start_gui":
                    continue
                label = f.name
                if folder == APP_DIR / "Scripts":
                    label = APP_DIR.name + "/Scripts/" + f.name
                elif folder == APP_DIR and APP_DIR != bot_dir:
                    label = APP_DIR.name + "/" + f.name
                if label in found:
                    continue
                found[label] = f
    for e in extras:
        p = Path(e)
        if p.is_file():
            found["* " + p.name] = p
    return found


# --------------------------------------------------------------------------- #
# GUI
# --------------------------------------------------------------------------- #
def run_gui():
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox

    root = tk.Tk()
    root.withdraw()
    settings = load_settings()
    bot_dir = Path(settings.get("bot_dir", DEFAULT_BOT_DIR))
    # the main folder is the one that holds the BHPyBot folder (this script's folder); if the saved one is wrong, ask
    while not (bot_dir == APP_DIR or (bot_dir / APP_DIR.name).is_dir()):
        picked = filedialog.askdirectory(title="Select the main folder (the one that contains the %s folder)" % APP_DIR.name)
        if not picked:
            root.destroy()
            return
        bot_dir = Path(picked)
    settings["bot_dir"] = str(bot_dir)
    root.deiconify()
    root.title("BH Bot Controller")
    root.geometry("820x640")
    root.minsize(640, 480)

    events = queue.Queue()
    state = {"run": None, "queue_next": None, "started": 0.0, "scripts": {}}

    primary = tk.StringVar()
    secondary = tk.StringVar(value=NO_SELECTION)
    only_if_ok = tk.BooleanVar(value=settings.get("only_if_ok", True))
    kill_game = tk.BooleanVar(value=settings.get("kill_game", False))
    status = tk.StringVar(value="Idle")
    esc_count = tk.StringVar(value=str(settings.get("esc_count", 4 if IS_WIN else 0)))
    compact = tk.BooleanVar(value=bool(settings.get("compact_log", False)))
    clean_on = tk.BooleanVar(value=bool(settings.get("clean_unknown", True)))
    alt_var = tk.BooleanVar(value=bool(settings.get("alternate_chars", False)))
    clean_days = tk.StringVar(value=str(settings.get("clean_unknown_days", 14)))

    frm = ttk.Frame(root, padding=10)
    frm.pack(fill="both", expand=True)
    frm.columnconfigure(1, weight=1)
    frm.rowconfigure(6, weight=1)

    view_which = tk.StringVar(value="1")                # which script the 'Script contents' window shows (only one at a time)
    lf1 = ttk.Frame(frm)
    lf1.grid(row=0, column=0, sticky="w")
    ttk.Radiobutton(lf1, text="View", value="1", variable=view_which, command=lambda: show_preview()).pack(side="left", padx=(0, 10))
    ttk.Label(lf1, text="Script:").pack(side="left")
    cb1 = ttk.Combobox(frm, textvariable=primary, state="readonly")
    cb1.grid(row=0, column=1, sticky="ew", padx=6, pady=2)
    lf2 = ttk.Frame(frm)
    lf2.grid(row=1, column=0, sticky="w")
    ttk.Radiobutton(lf2, text="View", value="2", variable=view_which, command=lambda: show_preview()).pack(side="left", padx=(0, 10))
    ttk.Label(lf2, text="Then run:").pack(side="left")
    cb2 = ttk.Combobox(frm, textvariable=secondary, state="readonly")
    cb2.grid(row=1, column=1, sticky="ew", padx=6, pady=2)

    side = ttk.Frame(frm)
    side.grid(row=0, column=3, rowspan=2, sticky="ns")
    ttk.Button(side, text="Browse...", command=lambda: browse()).pack(fill="x")
    ttk.Button(side, text="Refresh", command=lambda: refresh()).pack(fill="x", pady=(2, 0))

    opts = ttk.Frame(frm)
    opts.grid(row=2, column=0, columnspan=4, sticky="ew", pady=(2, 6))
    ttk.Checkbutton(opts, text="Run 2nd script only if the 1st succeeded", variable=only_if_ok).pack(side="left")
    ttk.Checkbutton(opts, text="Force Stop also closes the game window", variable=kill_game).pack(side="left", padx=14)
    ttk.Label(opts, text="ESC presses before 2nd script:").pack(side="left")
    ttk.Spinbox(opts, from_=0, to=10, width=3, textvariable=esc_count).pack(side="left", padx=4)
    ttk.Checkbutton(opts, text="Compact log", variable=compact, style="Toolbutton",
                    command=lambda: settings.update(compact_log=bool(compact.get())) or save_settings(settings)).pack(side="right", padx=(14, 0))

    btns = ttk.Frame(frm)
    btns.grid(row=3, column=0, columnspan=4, sticky="ew")
    alt_row = ttk.Frame(btns)                      # row 0 (level with Quest...Trials): the character switch
    alt_row.grid(row=0, column=0, sticky="w", pady=1)
    lrow = ttk.Frame(btns)                         # row 1 (level with Gauntlet...Invasion): Start, Resume, Force Stop, Setup Help
    lrow.grid(row=1, column=0, sticky="w", pady=1)
    btns.columnconfigure(7, weight=1)
    b_start = ttk.Button(lrow, text="Start", command=lambda: start())
    b_pause = ttk.Button(lrow, text="Pause", command=lambda: toggle_pause(), state="disabled")
    b_stop = ttk.Button(lrow, text="Force Stop", command=lambda: force_stop(), state="disabled")
    for b in (b_start, b_pause, b_stop):
        b.pack(side="left", padx=(0, 6))
    ttk.Label(btns, textvariable=status, font=("TkDefaultFont", 10, "bold")).grid(row=0, column=7, rowspan=2, sticky="e")
    ck_alt = ttk.Checkbutton(alt_row, text="Alternate characters...", variable=alt_var, command=lambda: save_speed())
    ck_alt.pack(side="left")
    Tip(ck_alt, "ALTERNATE CHARACTERS (default off)\n"
                "Designed for: accounts with a second hero (unlocked at higher levels). Both heroes get farmed in turn.\n"
                "Used in: bhbot.py switch_character() - opens YOUR HEROES (the small skull above your player badge), picks the hero "
                "that is NOT active, presses CONFIRM and waits 60 s for the game to reload.\n"
                "It switches in two cases: (1) when EVERY task of the current hero is waiting for tickets/energy and the other hero "
                "still has something to do, then the AFK chain starts over; (2) just before a 'Full restart every...': the bot finishes "
                "its current run, switches hero, waits 60 s so the server remembers it, then the game is restarted and loads that hero.\n"
                "Only 2 heroes are supported. If the YOUR HEROES page does not open, the bot says so in the log and keeps going.")
    # ---- settings overrides (written to overrides.json; the running bot picks changes up by itself) ---- #
    def open_account_setup():
        import bh_setup
        bh_setup.open_setup(root, bot_dir)

    ttk.Button(lrow, text="Initial Setup Help", command=open_account_setup).pack(side="left", padx=(6, 0))
    ov_buttons = {}
    ttk.Separator(btns, orient="vertical").grid(row=0, column=1, rowspan=2, sticky="ns", padx=(10, 10))
    for i, (title, key) in enumerate((("Quest", "quest"), ("PVP", "pvp"), ("World Boss", "boss"), ("Raid", "raid"), ("Trials", "trials"),
                                      ("Gauntlet", "gauntlet"), ("GVG", "gvg"), ("Expedition", "expedition"), ("Invasion", "invasion"))):
        ov_buttons[key] = (title, ttk.Button(btns, text=title, command=lambda k=key: open_override(k)))
        ov_buttons[key][1].grid(row=i // 5, column=2 + i % 5, padx=(0, 4), pady=1, sticky="ew")

    cl = ttk.Frame(frm)
    cl.grid(row=10, column=0, columnspan=4, sticky="e", pady=(4, 0))
    ck_cl = ttk.Checkbutton(cl, text="Clear BHPyBot/out/unknown older than", variable=clean_on, command=lambda: save_clean())
    ck_cl.pack(side="left")
    sp_cl = ttk.Spinbox(cl, from_=1, to=365, width=4, textvariable=clean_days, command=lambda: save_clean())
    sp_cl.pack(side="left", padx=4)
    for w in (ck_cl, sp_cl):
        Tip(w, "CLEAR OLD SCREENSHOTS (default on, 14 days)\n"
               "Designed for: stopping BHPyBot/out/unknown from filling the disk with 'unrecognised screen' pictures.\n"
               "Used in: bh_gui.py clean_old_files(), run once each time you click Start. Only files in out/unknown older than "
               "this many days are deleted.")
    ttk.Label(cl, text="days. Clears on Start").pack(side="left")

    def clean_n():
        try:
            return max(1, min(365, int(float(clean_days.get()))))
        except ValueError:
            return 14

    def save_clean():
        settings.update(clean_unknown=bool(clean_on.get()), clean_unknown_days=clean_n())
        save_settings(settings)

    clean_days.trace_add("write", lambda *_: save_clean())

    preview_title = tk.StringVar(value="Script contents:")
    ttk.Label(frm, textvariable=preview_title).grid(row=5, column=0, columnspan=4, sticky="w", pady=(8, 0))
    pw = ttk.PanedWindow(frm, orient="vertical")
    pw.grid(row=6, column=0, columnspan=4, sticky="nsew")
    preview = tk.Text(pw, height=5, wrap="none", state="disabled", background="#f4f4f4")
    log = tk.Text(pw, wrap="word", state="disabled", background="#101418", foreground="#d6dde4")
    log_sb = ttk.Scrollbar(log, command=log.yview)
    log_sb.pack(side="right", fill="y")
    log.configure(yscrollcommand=log_sb.set)
    pw.add(preview, weight=1)
    pw.add(log, weight=4)

    speed = ttk.Frame(frm)
    speed.grid(row=8, column=0, columnspan=4, sticky="ew", pady=(6, 0))
    speed.columnconfigure((0, 1, 2), weight=1, uniform="speed")
    sl, sm, sr = ttk.Frame(speed), ttk.Frame(speed), ttk.Frame(speed)
    sl.grid(row=0, column=0, sticky="w")
    sm.grid(row=0, column=1)
    sr.grid(row=0, column=2, sticky="e")
    lbl = ttk.Label(sl, text="Base Check Interval")
    lbl.pack(side="left")
    base_var = tk.StringVar(value=str(settings.get("base_tick", 15)))
    sp_base = ttk.Spinbox(sl, from_=1, to=300, increment=1, width=5, textvariable=base_var, command=lambda: save_speed())
    sp_base.pack(side="left", padx=4)
    ttk.Label(sl, text="s").pack(side="left")
    lbl2 = ttk.Label(sm, text="Look at the game every")
    lbl2.pack(side="left")
    tick_var = tk.StringVar(value=str(settings.get("tick", 1.0)))
    sp_tick = ttk.Spinbox(sm, from_=0.2, to=10, increment=0.2, width=5, textvariable=tick_var, command=lambda: save_speed())
    sp_tick.pack(side="left", padx=4)
    ttk.Label(sm, text="s").pack(side="left")
    lbl3 = ttk.Label(sr, text="Repeat 'waiting' messages every")
    lbl3.pack(side="left")
    wait_var = tk.StringVar(value=str(settings.get("wait_every", 60)))
    sp_wait = ttk.Spinbox(sr, from_=5, to=3600, increment=10, width=5, textvariable=wait_var, command=lambda: save_speed())
    sp_wait.pack(side="left", padx=4)
    ttk.Label(sr, text="s").pack(side="left")
    for w in (lbl, sp_base):
        Tip(w, "BASE CHECK INTERVAL (default 15 s)\n"
               "Designed for: saving CPU/heat while the bot has nothing to do. It is the extra pause between looks when EVERY task "
               "is waiting (out of tickets/energy, cooldowns, 'Waiting - ...' in the log).\n"
               "Used in: bhbot.py, the AFK main loop (the 'Waiting' branch).\n"
               "Higher = cooler PC, but a popup that appears while waiting is noticed later. Range 1-300 s.")
    for w in (lbl2, sp_tick):
        Tip(w, "LOOK AT THE GAME EVERY (default 1.0 s)\n"
               "Designed for: how fast the bot reacts while something is going on (menus, battles, popups, loading). "
               "Each look = one screenshot + matching.\n"
               "Used in: bhbot.py, the AFK main loop (pause at the top of every cycle).\n"
               "Lower = quicker reactions but more CPU and heat; 2-3 s is fine for most AFK use. Range 0.2-10 s.")
    for w in (lbl3, sp_wait):
        Tip(w, "REPEAT 'WAITING' MESSAGES EVERY (default 60 s)\n"
               "Designed for: keeping the log readable. The same 'waiting / screen is changing' line is printed at most this often.\n"
               "Used in: bhbot.py say() - it only changes what is printed, never what the bot does.")

    rs = ttk.Frame(frm)
    rs.grid(row=9, column=0, columnspan=4, sticky="ew", pady=(4, 0))
    rl, rr = ttk.Frame(rs), ttk.Frame(rs)
    rl.pack(side="left")
    rr.pack(side="right")
    l_rs = ttk.Label(rl, text="Full restart every")
    l_rs.pack(side="left")
    restart_var = tk.StringVar(value=str(settings.get("restart_min", 0)))
    sp_rs = ttk.Spinbox(rl, from_=0, to=1440, increment=5, width=5, textvariable=restart_var)
    sp_rs.pack(side="left", padx=4)
    ttk.Label(rl, text="min (0 = never)").pack(side="left")
    l_st = ttk.Label(rr, text="If the screen is stuck (3 identical screenshots in out/unknown):")
    l_st.pack(side="left")
    STUCK_CHOICES = {"Do nothing": "none", "Restart game + script(s)": "restart", "Shut down game": "shutdown"}
    stuck_var = tk.StringVar(value=next((k for k, v in STUCK_CHOICES.items() if v == settings.get("stuck_action", "none")), "Do nothing"))
    cb_st = ttk.Combobox(rr, textvariable=stuck_var, values=list(STUCK_CHOICES), state="readonly", width=24)
    cb_st.pack(side="left", padx=4)
    for w in (l_rs, sp_rs):
        Tip(w, "FULL RESTART EVERY (default 0 = never)\n"
               "Designed for: long unattended sessions - Chrome and the game slowly use more memory, a fresh start clears it.\n"
               "Used in: this window (bh_gui.py, the once-a-second timer). After N minutes it closes the game Chrome and starts "
               "the same script(s) again. Remembered between runs.")
    for w in (l_st, cb_st):
        Tip(w, "IF THE SCREEN IS STUCK (default: do nothing)\n"
               "Designed for: a screen the bot does not know and cannot get out of.\n"
               "Used in: bhbot.py note_unknown() counts consecutive 'unknown' screenshots that look the same; at 3 it ends "
               "with exit code 7 (restart) or 8 (shut down). bh_gui.py then restarts the game + script(s), or closes the game.")

    mt = ttk.Frame(frm)
    mt.grid(row=10, column=0, columnspan=4, sticky="w", pady=(4, 0))
    l_mt = ttk.Label(mt, text="If the game is down for maintenance: close it and start again after")
    l_mt.pack(side="left")
    maint_var = tk.StringVar(value=str(settings.get("maintenance_min", 60)))
    sp_mt = ttk.Spinbox(mt, from_=1, to=720, increment=5, width=5, textvariable=maint_var)
    sp_mt.pack(side="left", padx=4)
    for w in (l_mt, sp_mt):
        Tip(w, "MAINTENANCE WAIT (default 60 min)\n"
               "Designed for: server maintenance - no point leaving the game open and clicking.\n"
               "Used in: bhbot.py maintenance() spots the maintenance message and exits with code 9; bh_gui.py then closes the "
               "game and starts the script(s) again after this many minutes.")
    ttk.Label(mt, text="min").pack(side="left")

    def maint_minutes():
        try:
            return max(1, min(720, int(float(maint_var.get()))))
        except ValueError:
            return 60

    def save_maint(*_):
        settings["maintenance_min"] = maint_minutes()
        save_settings(settings)
    maint_var.trace_add("write", save_maint)

    inp = ttk.Frame(frm)
    inp.grid(row=7, column=0, columnspan=4, sticky="ew", pady=(6, 0))
    inp.columnconfigure(1, weight=1)
    ttk.Label(inp, text="Send input:").grid(row=0, column=0)
    entry = ttk.Entry(inp)
    entry.grid(row=0, column=1, sticky="ew", padx=6)
    ttk.Button(inp, text="Send", command=lambda: send_input()).grid(row=0, column=2)

    # ---- helpers ---- #
    def load_ov():
        try:
            d = json.loads(OVERRIDES_FILE.read_text(encoding="utf-8")) if OVERRIDES_FILE.is_file() else {}
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    def save_ov(d):
        try:
            OVERRIDES_FILE.write_text(json.dumps(d, indent=2), encoding="utf-8")
        except OSError as e:
            messagebox.showerror("Cannot save", str(e))
        show_ov_state()

    def show_ov_state():
        d = load_ov()
        for key, (title, button) in ov_buttons.items():
            on = isinstance(d.get(key), dict) and bool(d[key].get("on"))
            if key in EVENT_ONLY:                                          # these only switch an event off: mark it when it is skipped
                on = on and not d[key].get("enabled", True)
            button.configure(text=title + (" \u25cf" if on else ""))      # a dot marks an override that is switched on

    def save_speed():
        try:
            tick = min(max(float(tick_var.get()), 0.2), 10.0)
            every = min(max(int(float(wait_var.get())), 5), 3600)
        except ValueError:
            return
        try:
            rmin = min(max(int(float(restart_var.get())), 0), 1440)
        except ValueError:
            rmin = 0
        action = STUCK_CHOICES.get(stuck_var.get(), "none")
        try:
            base = min(max(float(base_var.get()), 1.0), 300.0)
        except ValueError:
            base = 15.0
        settings.update(tick=tick, base_tick=base, wait_every=every, restart_min=rmin, stuck_action=action, alternate_chars=bool(alt_var.get()))
        save_settings(settings)
        d = load_ov()
        old = d.get("settings") if isinstance(d.get("settings"), dict) else {}
        d["settings"] = {"tick": tick, "base_tick": base, "wait_every": every, "stuck_action": action, "alternate_chars": bool(alt_var.get())}
        if old.get("switch_request"):                   # a pending 'switch hero before the restart' request must survive
            d["settings"]["switch_request"] = old["switch_request"]
        save_ov(d)

    stuck_var.trace_add("write", lambda *_: save_speed())
    for widget_var in (tick_var, base_var, wait_var, restart_var):
        widget_var.trace_add("write", lambda *_: root.after(800, save_speed)) if hasattr(widget_var, "trace_add") else None

    EVENT_ONLY = ("gvg", "expedition")                                    # overrides that hold nothing but 'run it / skip it'
    TITLES = {"quest": "Quest", "pvp": "PVP", "boss": "World Boss", "raid": "Raid", "trials": "Trials", "gauntlet": "Gauntlet",
              "gvg": "GVG", "expedition": "Expedition", "invasion": "Invasion"}
    DIFFS = ["Normal", "Hard", "Heroic"]
    BOSSES = [("orlag", "Orlag Clan", 5), ("netherworld", "Netherworld", 3), ("melvin", "Melvin Factory", 4),
              ("exterminytion", "3xt3rmin4tion", 3), ("brimstone", "Brimstone Syndicate", 3), ("titans", "Titans Attack!", 3),
              ("ignited", "The Ignited Abyss", 3), ("goodall", "Project: Goodall", 4), ("gallus", "Gallus Apotheosis", 4)]

    def open_override(key):
        ov = load_ov()
        cur = ov.get(key) if isinstance(ov.get(key), dict) else {}
        win = tk.Toplevel(root)
        win.title("Override: " + TITLES[key])
        win.transient(root)
        win.resizable(False, False)
        body = ttk.Frame(win, padding=12)
        body.pack(fill="both", expand=True)
        use = tk.BooleanVar(value=bool(cur.get("on")) or key in EVENT_ONLY)
        if key not in EVENT_ONLY:
            ttk.Checkbutton(body, text="Use these settings instead of my profile", variable=use).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 8))
        fields = {}
        row = [1]

        def add_spin(name, label, lo, hi, default, hint=""):
            v = tk.StringVar(value=str(cur.get(name, default)))
            ttk.Label(body, text=label).grid(row=row[0], column=0, sticky="w", pady=2)
            ttk.Spinbox(body, from_=lo, to=hi, width=6, textvariable=v).grid(row=row[0], column=1, sticky="w", padx=6)
            if hint:
                ttk.Label(body, text=hint, foreground="#666").grid(row=row[0], column=2, sticky="w")
            fields[name] = ("int", v)
            row[0] += 1

        def add_diff(name, label="Difficulty:", allow_profile=True):
            opts = (["(profile)"] if allow_profile else []) + DIFFS
            m = int(cur.get(name, 0) or 0)
            v = tk.StringVar(value=DIFFS[m - 1] if m in (1, 2, 3) else (opts[0] if allow_profile else "Heroic"))
            ttk.Label(body, text=label).grid(row=row[0], column=0, sticky="w", pady=2)
            ttk.Combobox(body, values=opts, textvariable=v, state="readonly", width=12).grid(row=row[0], column=1, columnspan=2, sticky="w", padx=6)
            fields[name] = ("diff", v)
            row[0] += 1

        def add_choice(name, label, options, current):
            v = tk.StringVar(value=next((text for val, text in options if val == current), options[0][1]))
            ttk.Label(body, text=label).grid(row=row[0], column=0, sticky="w", pady=2)
            ttk.Combobox(body, values=[t for _, t in options], textvariable=v, state="readonly", width=26).grid(row=row[0], column=1, columnspan=2, sticky="w", padx=6)
            fields[name] = ("choice", (v, options))
            row[0] += 1

        def add_check(name, label, default=True):
            v = tk.BooleanVar(value=bool(cur.get(name, default)))
            ttk.Checkbutton(body, text=label, variable=v).grid(row=row[0], column=0, columnspan=3, sticky="w", pady=2)
            fields[name] = ("bool", v)
            row[0] += 1

        if key == "quest":
            add_spin("zone", "Zone:", 0, 99, 0, "0 = automatic (most recent beaten level)")
            add_spin("dungeon", "Dungeon in that zone:", 0, 12, 0, "0 = automatic; counted left to right on the map")
            add_diff("mode", "Difficulty:")
        elif key == "pvp":
            add_spin("target", "Opponent (1 = top Fight button):", 0, 5, 0, "0 = profile / default")
        elif key == "boss":
            names = [b[1] for b in BOSSES]
            cur_name = next((b[1] for b in BOSSES if b[0] == cur.get("name")), names[5])
            bv = tk.StringVar(value=cur_name)
            ttk.Label(body, text="Boss:").grid(row=row[0], column=0, sticky="w", pady=2)
            bcb = ttk.Combobox(body, values=names, textvariable=bv, state="readonly", width=22)
            bcb.grid(row=row[0], column=1, columnspan=2, sticky="w", padx=6)
            fields["name"] = ("boss", bv)
            row[0] += 1
            add_spin("tier", "Tier:", 0, 40, 12, "0 = leave as the game has it")
            add_diff("mode", "Difficulty:", allow_profile=False)
            add_check("private", "Private (solo) - un-tick to let other players join", True)
            max_others = tk.StringVar()
            add_spin("players", "Other players to wait for:", 1, 4, 1)
            ttk.Label(body, textvariable=max_others, foreground="#666").grid(row=row[0] - 1, column=2, sticky="w")
            add_spin("wait_min", "Minutes to wait for them:", 1, 180, 15, "then it leaves and goes solo (private)")

            def boss_changed(*_):
                n = next((b[2] for b in BOSSES if b[1] == bv.get()), 3)
                max_others.set("max %d for this boss" % (n - 1))
            bcb.bind("<<ComboboxSelected>>", boss_changed)
            boss_changed()
            ttk.Label(body, text="Not private: when you are the lead the bot waits until that many players show the green 'ready' border,\n"
                                 "then presses START. If you are not the lead it presses READY and waits for the lead.",
                      foreground="#666", justify="left").grid(row=row[0], column=0, columnspan=3, sticky="w", pady=(6, 0))
            row[0] += 1
        elif key == "raid":
            add_spin("level", "Raid number (1 = first raid):", 0, 40, 0, "0 = profile")
            add_diff("mode", "Difficulty:")
        elif key == "trials":
            add_spin("tier", "Tier:", 0, 40, 15, "0 = leave the difficulty as the game has it")
            add_choice("drops", "Gear drops:", [("set", "Set + Mythic (light blue)"), ("legendary", "Legendary (yellow)")], cur.get("drops", "set"))
            add_choice("pick", "Value inside that range:", [("highest", "Highest (hardest)"), ("lowest", "Lowest (easiest)")], cur.get("pick", "highest"))
            ttk.Label(body, text="The bot opens the DIFFICULTY list on the Trials page, scrolls to the tier you chose, picks that\n"
                                 "light-blue or yellow row, then the highest/lowest number in it. It knows tiers 13-15; for lower\n"
                                 "or newer tiers it counts rows from a known tier, or needs a one-line template (see README).",
                      foreground="#666", justify="left").grid(row=row[0], column=0, columnspan=3, sticky="w", pady=(6, 0))
            row[0] += 1
        elif key == "gauntlet":
            add_spin("number", "Difficulty number:", 0, 30000, 0, "0 = use the tier below; otherwise its tier / drop type is looked up")
            add_spin("tier", "Tier:", 0, 21, 15, "0 = leave the difficulty as the game has it")
            add_choice("drops", "Gear drops:", [("set", "Set (light blue)"), ("legendary", "Legendary (yellow)")], cur.get("drops", "set"))
            add_choice("pick", "Value inside that range:", [("highest", "Highest (hardest)"), ("lowest", "Lowest (easiest)")], cur.get("pick", "highest"))
            ttk.Label(body, text="A difficulty number is matched against the Gauntlet table (tier 1 = 1-9 ... tier 21 = 15519-25001) to\n"
                                 "find its tier and drop type; the bot picks that row in the DIFFICULTY list, then the highest/lowest\n"
                                 "number in it. Tiers 1-15 are recognised on screen; 16-21 are found by counting rows.\n"
                                 "Tiers 1-4 have one row each (the drop type is ignored).",
                      foreground="#666", justify="left").grid(row=row[0], column=0, columnspan=3, sticky="w", pady=(6, 0))
            row[0] += 1
        elif key == "invasion":
            add_spin("wave", "Wave to play:", 0, 9999, 0, "0 = the top (highest) number in the list")
            ttk.Label(body, text="The bot opens the WAVE list, scrolls to the top, and reads the numbers (a digit table made from\n"
                                 "screenshots). With a number set it scrolls until that wave is on screen and clicks it. If the wave\n"
                                 "is not in the list (too high, too low, unreadable) it plays the top wave instead.",
                      foreground="#666", justify="left").grid(row=row[0], column=0, columnspan=3, sticky="w", pady=(6, 0))
            row[0] += 1
        elif key in EVENT_ONLY:
            ttk.Label(body, text="%s runs with the game's own settings; there is nothing to choose here yet." % TITLES[key],
                      foreground="#666", justify="left").grid(row=row[0], column=0, columnspan=3, sticky="w", pady=(0, 4))
            row[0] += 1
        if key in ("trials", "gauntlet", "invasion") or key in EVENT_ONLY:
            add_check("enabled", "Run %s (un-tick to skip it while this override is on)" % TITLES[key], True)
            if key in ("trials", "gauntlet", "invasion"):
                ttk.Label(body, text="GVG / Expedition / Invasion share one cooldown, Trials / Gauntlet share another;\n"
                                     "only the one currently in the slot is ever run.", foreground="#666", justify="left"
                          ).grid(row=row[0], column=0, columnspan=3, sticky="w", pady=(2, 0))
                row[0] += 1

        def collect():
            out = {}
            for name, (kind, v) in fields.items():
                if kind == "int":
                    try:
                        out[name] = max(0, int(v.get()))
                    except ValueError:
                        out[name] = 0
                elif kind == "diff":
                    out[name] = DIFFS.index(v.get()) + 1 if v.get() in DIFFS else 0
                elif kind == "boss":
                    out[name] = next((b[0] for b in BOSSES if b[1] == v.get()), "")
                elif kind == "choice":
                    var, options = v
                    out[name] = next((val for val, text in options if text == var.get()), options[0][0])
                else:
                    out[name] = bool(v.get())
            return out

        def save():
            d = load_ov()
            vals = collect()
            vals["on"] = bool(use.get())
            d[key] = vals
            legacy = d.pop("events", None)                                 # the old combined section becomes one section per event
            if isinstance(legacy, dict):
                for t, v in legacy.items():
                    if isinstance(v, dict) and t != key:
                        d[t] = dict(d.get(t) if isinstance(d.get(t), dict) else {}, **{"enabled": v.get("enabled", True)}, on=bool(v.get("on")) or bool(d.get(t, {}).get("on")))
            save_ov(d)
            if state["run"] and state["run"].alive:
                write_log("[overrides] saved - the running bot picks it up within a second or two\n")
            win.destroy()

        bar = ttk.Frame(body)
        bar.grid(row=row[0], column=0, columnspan=3, sticky="e", pady=(12, 0))
        ttk.Button(bar, text="Save", command=save).pack(side="left", padx=4)
        ttk.Button(bar, text="Cancel", command=win.destroy).pack(side="left")
        win.grab_set()

    def write_log(text):
        log.configure(state="normal")
        log.insert("end", text)
        if int(log.index("end-1c").split(".")[0]) > 5000:
            log.delete("1.0", "1000.0")
        log.see("end")
        log.configure(state="disabled")

    pending = {"text": "", "at": 0.0, "last": ""}

    def show_log(text):
        """Normal mode: everything. Compact mode: only whole lines that are not repetitive waiting messages."""
        if not compact.get():
            if pending["text"]:
                write_log(pending["text"])
                pending["text"] = ""
            write_log(text)
            return
        data = pending["text"] + text
        lines = data.split("\n")
        pending["text"], pending["at"] = lines.pop(), time.time()       # a last piece without newline waits for its end
        for ln in lines:
            if NOISE_RE.search(ln) or ln.strip() == pending["last"]:
                continue
            pending["last"] = ln.strip()
            write_log(ln + "\n")

    def banner(msg):
        write_log("\n=== %s  [%s] ===\n" % (msg, time.strftime("%H:%M:%S")))

    def refresh():
        state["scripts"] = discover_scripts(bot_dir, settings.get("extras", []))
        names = sorted(state["scripts"])
        cb1["values"] = names
        cb2["values"] = [NO_SELECTION] + names
        for key in ("primary", "secondary"):                 # older versions kept the scripts one level up: BHPyBot/AFK.bat -> BHPyBot/Scripts/AFK.bat
            old = settings.get(key) or ""
            if old and old not in names:
                for cand in (APP_DIR.name + "/Scripts/" + old.split("/")[-1],):
                    if cand in names:
                        settings[key] = cand
        if primary.get() not in names:
            primary.set(settings.get("primary") if settings.get("primary") in names else (names[0] if names else ""))
        if secondary.get() not in names + [NO_SELECTION]:
            secondary.set(settings.get("secondary") if settings.get("secondary") in names else NO_SELECTION)
        show_preview()

    def browse():
        f = filedialog.askopenfilename(title="Choose a script", initialdir=str(bot_dir),
                                       filetypes=[("Scripts", "*" + SCRIPT_EXT), ("All files", "*.*")])
        if f:
            settings.setdefault("extras", [])
            if f not in settings["extras"]:
                settings["extras"].append(f)
            refresh()
            primary.set("* " + Path(f).name)
            view_which.set("1")
            show_preview()

    def show_preview(*_):
        which = view_which.get()
        label = primary.get() if which == "1" else secondary.get()
        path = state["scripts"].get(label)
        preview_title.set("Script contents - %s script: %s" % ("1st" if which == "1" else "2nd", label if path else "(none selected)"))
        preview.configure(state="normal")
        preview.delete("1.0", "end")
        if path:
            try:
                preview.insert("end", path.read_text(encoding="utf-8", errors="replace"))
            except OSError as e:
                preview.insert("end", str(e))
        preview.configure(state="disabled")

    def set_buttons(running, paused=False):
        b_start.configure(state="disabled" if running else "normal")
        b_pause.configure(state="normal" if running else "disabled", text="Resume" if paused else "Pause")
        b_stop.configure(state="normal" if running else "disabled")
        cb1.configure(state="disabled" if running else "readonly")
        cb2.configure(state="disabled" if running else "readonly")

    def launch(label):
        path = state["scripts"].get(label)
        if not path:
            messagebox.showerror("Script missing", "Can't find script: %s" % label)
            return False
        run = ScriptRun(path, bot_dir, events)
        state["run"], state["started"] = run, time.time()
        banner("START " + label)
        try:
            run.start()
        except OSError as e:
            write_log("Failed to start: %s\n" % e)
            state["run"] = None
            set_buttons(False)
            return False
        set_buttons(True)
        return True

    def esc_times():
        try:
            return max(0, min(10, int(esc_count.get())))
        except ValueError:
            return 0

    def start():
        if not primary.get():
            return
        settings.update(primary=primary.get(), secondary=secondary.get(), esc_count=esc_times(),
                        only_if_ok=only_if_ok.get(), kill_game=kill_game.get())
        save_settings(settings)
        save_speed()                                    # make sure overrides.json has every current setting (incl. Alternate characters)
        if clean_on.get():
            try:
                n, freed = clean_old_files(APP_DIR / "out" / "unknown", clean_n())
                if n:
                    write_log("[Cleanup] deleted %d screenshot file(s) older than %d days from out/unknown (%.1f MB)\n" % (n, clean_n(), freed / 1048576))
            except Exception as e:
                write_log("[Cleanup] could not clean out/unknown: %s\n" % e)
        state["queue_next"] = None if secondary.get() == NO_SELECTION else secondary.get()
        state["cancel_chain"] = False
        state["cycle_start"] = time.time()
        launch(primary.get())

    def restart_all(reason):
        """Full restart: stop the script(s), close the game window, wait a moment, then start from the first script again."""
        state["restarting"] = reason
        state["queue_next"] = None
        run = state["run"]
        if run and run.alive:
            run.stop(kill_game=True)             # the 'done' event then carries on (see tick)
        else:
            events.put(("done", True, True))

    def begin_again():
        if not state.pop("restart_pending", False):         # Force Stop was pressed during the wait
            return
        n = kill_chrome()
        write_log("[Full restart] closed the game window (%d processes); starting again\n" % n)
        state["queue_next"] = None if secondary.get() == NO_SELECTION else secondary.get()
        state["cancel_chain"] = False
        state["cycle_start"] = time.time()
        launch(primary.get())

    def chain_to(label):
        """Get the game back to a clean main screen (ESC), then start the next script."""
        n = esc_times()
        status.set("Script finished - ESC sequence (3 s, ESC, 3 s, ESC, 3 s, ESC), then ESC x%d..." % n)
        b_start.configure(state="disabled")
        b_stop.configure(state="normal")
        state["chaining"] = True

        def work():
            msgs, ok = [], True
            for i in range(3):                       # 3 s pause, then one ESC - three times
                for _ in range(30):
                    if state.get("cancel_chain"):      # Force Stop pressed meanwhile
                        events.put(("esc", (ok, "; ".join(msgs) or "cancelled"), label))
                        return
                    time.sleep(0.1)
                r_ok, r_msg = press_esc_in_game(1)
                ok = ok and r_ok
                msgs.append("ESC %d/3: %s" % (i + 1, r_msg))
            if n > 0:
                r_ok, r_msg = press_esc_in_game(n)
                ok = ok and r_ok
                msgs.append("then ESC x%d: %s" % (n, r_msg))
            events.put(("esc", (ok, "; ".join(msgs)), label))
        threading.Thread(target=work, daemon=True).start()

    def toggle_pause():
        run = state["run"]
        if not run:
            return
        if run.paused:
            run.resume()
        else:
            run.pause()

    def force_stop():
        run = state["run"]
        state["queue_next"] = None
        state.pop("switch_wait", None)
        if state.pop("restart_pending", False) or state.pop("maint_until", None):
            banner("STOPPED by user (restart cancelled)")
            status.set("Stopped")
            set_buttons(False)
        if state.get("chaining"):               # stopped while ESC-ing between scripts
            state["cancel_chain"] = True
        if run:
            run.stop(kill_game=kill_game.get())

    def send_input():
        run = state["run"]
        if run and run.alive:
            run.send_input(entry.get())
            write_log("> %s\n" % entry.get())
            entry.delete(0, "end")

    def tick():
        try:
            while True:
                ev = events.get_nowait()
                if ev[0] == "log":
                    show_log(ev[1])
                elif ev[0] == "state":
                    set_buttons(True, paused=(ev[1] == "paused"))
                elif ev[0] == "done":
                    _, ok, user_stopped = ev
                    last_exit = state["run"].last_exit if state["run"] else None
                    switching = state.pop("switch_wait", None)
                    state["run"] = None
                    set_buttons(False)
                    why = state.pop("restarting", None)
                    if why:
                        banner("FULL RESTART (%s)" % why)
                        status.set("Restarting the game and script(s)...")
                        b_start.configure(state="disabled")
                        b_stop.configure(state="normal")
                        state["restart_pending"] = True
                        kill_chrome()
                        root.after(5000, begin_again)
                    elif not user_stopped and last_exit == 10 and switching:
                        d = load_ov()
                        if isinstance(d.get("settings"), dict):
                            d["settings"].pop("switch_request", None)
                            save_ov(d)
                        banner("FULL RESTART (the other character is selected)")
                        status.set("Restarting the game and script(s)...")
                        b_start.configure(state="disabled")
                        b_stop.configure(state="normal")
                        state["restart_pending"] = True
                        kill_chrome()
                        root.after(5000, begin_again)
                    elif not user_stopped and last_exit == 7:
                        banner("STUCK - restarting the game and script(s)")
                        status.set("Stuck screen - restarting...")
                        b_start.configure(state="disabled")
                        b_stop.configure(state="normal")
                        state["restart_pending"] = True
                        kill_chrome()
                        root.after(5000, begin_again)
                    elif not user_stopped and last_exit == 9:
                        mins = maint_minutes()
                        banner("SERVER MAINTENANCE - game closed, starting again in %d min" % mins)
                        kill_chrome()
                        state["queue_next"] = None
                        state["maint_until"] = time.time() + mins * 60
                        b_start.configure(state="disabled")
                        b_stop.configure(state="normal")
                    elif not user_stopped and last_exit == 8:
                        banner("STUCK - game shut down")
                        kill_chrome()
                        state["queue_next"] = None
                        status.set("Stuck screen - game shut down, script ended")
                    elif user_stopped:
                        banner("STOPPED by user")
                        status.set("Stopped")
                        state["queue_next"] = None
                    else:
                        banner("FINISHED (%s)" % ("ok" if ok else "bot reported an error"))
                        nxt = state["queue_next"]
                        state["queue_next"] = None
                        if nxt and (ok or not only_if_ok.get()):
                            chain_to(nxt)
                            continue
                        status.set("Finished" if ok else "Finished with errors")
                elif ev[0] == "esc":
                    (esc_ok, esc_msg), nxt = ev[1], ev[2]
                    state["chaining"] = False
                    write_log("[ESC reset] %s\n" % esc_msg)
                    if state.pop("cancel_chain", False):
                        banner("STOPPED by user")
                        status.set("Stopped")
                        set_buttons(False)
                    else:
                        launch(nxt)        # carry on even if ESC failed; the log says why
        except queue.Empty:
            pass
        if pending["text"] and time.time() - pending["at"] > 0.8:
            write_log(pending["text"])
            pending["text"] = ""
        if state.get("maint_until"):
            left = state["maint_until"] - time.time()
            if left <= 0:
                state.pop("maint_until", None)
                state["restart_pending"] = True
                banner("Maintenance wait is over - starting again")
                begin_again()
            else:
                status.set("Maintenance - starting again in %d min" % (int(left // 60) + 1))
        try:
            rmin = int(float(restart_var.get()))
        except ValueError:
            rmin = 0
        if (rmin > 0 and state["run"] and state["run"].alive and not state.get("restarting") and not state.get("chaining")
                and not state.get("switch_wait") and time.time() - state.get("cycle_start", time.time()) >= rmin * 60):
            if alt_var.get():                      # switch hero first: ask the bot, it finishes its run, switches, waits 60 s, then exits
                d = load_ov()
                d.setdefault("settings", {})["switch_request"] = time.time()
                save_ov(d)
                state["switch_wait"] = time.time()
                banner("FULL RESTART is due - first switching to the other character (the bot finishes its current run)")
                status.set("Switching character before the restart...")
            else:
                restart_all("every %d min" % rmin)
        elif state.get("switch_wait") and state["run"] and state["run"].alive and time.time() - state["switch_wait"] > 20 * 60:
            state.pop("switch_wait", None)         # the bot could not switch in 20 minutes: restart anyway
            restart_all("every %d min (character switch took too long)" % rmin)
        run = state["run"]
        if run:
            el = int(time.time() - state["started"])
            label = "PAUSED" if run.paused else "Running"
            status.set("%s  %d:%02d:%02d" % (label, el // 3600, el % 3600 // 60, el % 60))
        root.after(200, tick)

    def on_close():
        run = state["run"]
        if run and run.alive:
            if not messagebox.askyesno("Bot is running", "Force stop the bot and exit?"):
                return
            run.stop(kill_game=kill_game.get())
        save_settings(settings)
        root.destroy()

    cb1.bind("<<ComboboxSelected>>", lambda e: (view_which.set("1"), show_preview()))
    cb2.bind("<<ComboboxSelected>>", lambda e: (view_which.set("2"), show_preview()))
    entry.bind("<Return>", lambda e: send_input())
    root.protocol("WM_DELETE_WINDOW", on_close)
    refresh()
    show_ov_state()
    tick()
    root.mainloop()


if __name__ == "__main__":
    run_gui()
