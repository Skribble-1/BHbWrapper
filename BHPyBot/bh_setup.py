"""
Initial Setup Help window for the BH Bot Controller (Chrome / Kongregate only - the Steam client is not supported).
The parsing / file-merging functions have no GUI dependency so they can be tested on their own.
"""
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

GAME_URL = "https://www.kongregate.com/games/Juppiomenz/bit-heroes"
SCRIPT_NAME = "prepare-mini-chrome-client.txt"
IS_WIN = os.name == "nt"
KEYS = ("game.kong.user.id", "game.kong.user.name", "game.auth.token")
KEY_RE = re.compile(r"^\s*(?:(\d+)\.)?(game\.kong\.user\.id|game\.kong\.user\.name|game\.auth\.token)\s*[=:]\s*(.*?)\s*$")

# The helper's own console script (the original repo's prepare-mini-chrome-client.txt no longer works on Kongregate's current page).
# It uses the method confirmed working on the live page: window.parent.kongregateAPI._flashVarsObject; the others are backups.
FALLBACK_SCRIPT = """(function () {
  var id, name, token, wins = [window.parent, window, window.top];
  try { wins.push(window.frames[0]); } catch (e) {}
  wins.forEach(function (w) {
    if (token) return;
    try {
      var fv = w.kongregateAPI._flashVarsObject;
      if (fv && fv.kongregate_game_auth_token) { id = fv.kongregate_user_id; name = fv.kongregate_username; token = fv.kongregate_game_auth_token; }
    } catch (e) {}
    try {
      if (!token && w.active_user && w.active_user.gameAuthToken()) { id = w.active_user.id(); name = w.active_user.username(); token = w.active_user.gameAuthToken(); }
    } catch (e) {}
  });
  if (!token || !id || !name) { console.log("You have to login using your Kongregate account first."); return; }
  var text = "# Game tokens\\n1.game.kong.user.id=" + id + "\\n1.game.kong.user.name=" + name + "\\n1.game.auth.token=" + token;
  console.log(text);
  try { copy(text); } catch (e) {}
})();
"""


# --------------------------------------------------------------------------- #
# Pure helpers
# --------------------------------------------------------------------------- #
def parse_console_output(text):
    """Pull the three account values out of whatever the user pasted. Returns ({key: value}, [problems])."""
    found = {}
    for line in text.replace("\r", "\n").split("\n"):
        line = line.strip().strip("'\"")
        m = KEY_RE.match(line)
        if m and m.group(3):
            found[m.group(2)] = m.group(3).strip().strip("'\"")
    alt = {"user id": "game.kong.user.id", "username": "game.kong.user.name", "game auth token": "game.auth.token"}
    for line in text.replace("\r", "\n").split("\n"):                   # also accepts the 'User ID: ...' style lines
        m = re.match(r"^\s*(?:VM\d+:\d+\s+)?(User ID|Username|Game Auth Token)\s*:\s*(\S+)\s*$", line, re.I)
        if m and alt[m.group(1).lower()] not in found:
            found[alt[m.group(1).lower()]] = m.group(2)
    problems = []
    if "You have to login" in text:
        problems.append("The console says you are not logged in to Kongregate. Log in on the Kongregate tab and run the script again.")
    for k in KEYS:
        if k not in found:
            problems.append("Missing %s in the pasted text." % k)
    return found, problems


def merge_properties(existing, values, account):
    """Put the values into a properties file's text as `<account>.<key>=<value>`. Existing lines for those keys are replaced
    in place (everything else - comments, other accounts, other keys - is kept); missing ones are appended."""
    wanted = {"%d.%s" % (account, k): v for k, v in values.items()}
    out, done = [], set()
    nl = "\r\n" if "\r\n" in existing else "\n"
    for line in existing.replace("\r\n", "\n").split("\n"):
        stripped = line.strip()
        key = stripped.split("=", 1)[0].strip() if "=" in stripped and not stripped.startswith(("#", "!")) else None
        if key in wanted:
            if key not in done:
                out.append("%s=%s" % (key, wanted[key]))
                done.add(key)
            continue                                    # a duplicate of a key we set: drop it
        out.append(line)
    while out and out[-1] == "":
        out.pop()
    for key in wanted:
        if key not in done:
            out.append("%s=%s" % (key, wanted[key]))
    return nl.join(out) + nl


def save_account(config_path, values, account):
    """Backs the file up once per save, then rewrites it. Returns the backup path (or None if the file is new)."""
    config_path = Path(config_path)
    existing, backup = "", None
    if config_path.is_file():
        existing = config_path.read_text(encoding="utf-8", errors="replace")
        backup = config_path.with_name(config_path.name + ".bak")
        shutil.copy2(config_path, backup)
    config_path.write_text(merge_properties(existing, values, account), encoding="utf-8", newline="")
    return backup


def chrome_candidates(config_path=None):
    """Every place Chrome is normally found, most reliable first."""
    cands = []
    if config_path and Path(config_path).is_file():                       # the original bot's own setting
        for line in Path(config_path).read_text(encoding="utf-8", errors="replace").splitlines():
            if line.strip().startswith("external.application.chrome.path") and "=" in line:
                cands.append(line.split("=", 1)[1].strip().strip('"'))
    if IS_WIN:
        try:                                                                # Windows' own list of installed programs
            import winreg
            for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
                for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
                    try:
                        with winreg.OpenKey(hive, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe", 0,
                                            winreg.KEY_READ | view) as k:
                            cands.append(winreg.QueryValueEx(k, "")[0])
                    except OSError:
                        pass
        except ImportError:
            pass
        for var in ("PROGRAMFILES", "PROGRAMW6432", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            if os.environ.get(var):
                cands.append(str(Path(os.environ[var]) / "Google" / "Chrome" / "Application" / "chrome.exe"))
        for d in (r"C:\Program Files", r"C:\Program Files (x86)"):
            cands.append(d + r"\Google\Chrome\Application\chrome.exe")
        cands.append(shutil.which("chrome") or "")
        try:
            out = subprocess.run(["where", "chrome"], capture_output=True, text=True, timeout=10,
                                 creationflags=subprocess.CREATE_NO_WINDOW).stdout
            cands += [ln.strip() for ln in out.splitlines()]
        except (OSError, subprocess.SubprocessError):
            pass
    else:
        cands += [shutil.which(n) or "" for n in ("google-chrome", "google-chrome-stable", "chrome", "chromium", "chromium-browser")]
        cands.append("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    return cands


def find_chrome(config_path=None, extra=None):
    for c in ([extra] if extra else []) + chrome_candidates(config_path):
        if c and Path(c).is_file():
            return c
    return None


def launch_chrome(url, config_path=None, extra=None):
    """Opens the URL in Chrome. Returns (ok, how). Tries the exe, then Windows' `start chrome`, and never a different browser."""
    exe = find_chrome(config_path, extra)
    try:
        if exe:
            subprocess.Popen([exe, url])
            return True, exe
        if IS_WIN:                                                          # chrome.exe is usually resolvable by name via App Paths
            r = subprocess.run(["cmd.exe", "/c", "start", "", "chrome", url], capture_output=True, timeout=15,
                               creationflags=subprocess.CREATE_NO_WINDOW)
            if r.returncode == 0:
                return True, "start chrome"
    except (OSError, subprocess.SubprocessError):
        pass
    return False, ""


def find_console_script(bot_dir):
    for d in (Path(bot_dir), Path(bot_dir).parent, Path(__file__).resolve().parent):
        p = d / SCRIPT_NAME
        if p.is_file():
            return p
    return None


def client_files(bot_dir):
    bot_dir = Path(bot_dir)
    found = sorted(p.name for p in bot_dir.glob("mini-game-on-chrome*"))
    found += sorted("bh-client/" + p.name for p in (bot_dir / "bh-client").glob("index*.html")) if (bot_dir / "bh-client").is_dir() else []
    return found


def make_pages(bot_dir):
    """Creates bh-client/index<N>.html for every account in user-config.properties using the Python bot itself (no Java needed).
    Returns (ok, lines)."""
    import importlib.util
    lines = []
    here = Path(__file__).resolve().parent
    try:
        spec = importlib.util.spec_from_file_location("bhbot_for_setup", here / "bhbot.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    except Exception as e:                                   # e.g. opencv not installed yet
        return False, ["Could not load the bot (%s). Run start_gui.bat once to install the required packages." % e]
    props = mod.load_props(Path(bot_dir) / "user-config.properties")
    accounts = mod.load_accounts(props)
    if not accounts:
        return False, ["No account found in user-config.properties - do step 3 first."]
    ok = True
    for a in accounts:
        if not a.get("token") or not a.get("name"):
            lines.append("Account %d is missing its name or token." % a["index"])
            ok = False
            continue
        try:
            page = mod.write_game_page(a)
            lines.append("Account %d (%s): created %s" % (a["index"], a["name"], Path(page).relative_to(Path(bot_dir)) if Path(bot_dir) in Path(page).parents else page))
        except Exception as e:
            lines.append("Account %d (%s): failed - %s" % (a["index"], a["name"], getattr(e, "msg", e)))
            ok = False
    return ok, lines


def java_available():
    return shutil.which("java") is not None


def run_client(bot_dir, timeout=120):
    """Runs the original client.bat / client.sh in the bot folder. Returns (ok, output)."""
    bot_dir = Path(bot_dir)
    script = bot_dir / ("client.bat" if IS_WIN else "client.sh")
    if not script.is_file():
        return False, "%s was not found in %s" % (script.name, bot_dir)
    cmd = ["cmd.exe", "/d", "/c", "call", str(script)] if IS_WIN else ["bash", str(script)]
    try:
        r = subprocess.run(cmd, cwd=str(bot_dir), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           timeout=timeout, creationflags=subprocess.CREATE_NO_WINDOW if IS_WIN else 0)
    except subprocess.TimeoutExpired:
        return False, "%s did not finish within %d s." % (script.name, timeout)
    except OSError as e:
        return False, str(e)
    return r.returncode == 0, r.stdout.decode("utf-8", errors="replace")


# --------------------------------------------------------------------------- #
# The window
# --------------------------------------------------------------------------- #
def open_setup(root, bot_dir):
    import tkinter as tk
    from tkinter import ttk, messagebox

    bot_dir = Path(bot_dir)
    config = bot_dir / "user-config.properties"
    win = tk.Toplevel(root)
    win.title("Initial Setup Help")
    win.geometry("680x820")
    frm = ttk.Frame(win, padding=12)
    frm.pack(fill="both", expand=True)

    ttk.Label(frm, text="Set up your bot account files", font=("TkDefaultFont", 12, "bold")).pack(anchor="w")
    ttk.Label(frm, wraplength=600, justify="left", foreground="#a33",
              text="Chrome browser option only. The Steam client option is NOT supported by this helper.").pack(anchor="w", pady=(2, 4))
    ttk.Label(frm, wraplength=600, justify="left",
              text="This does what the original bot's wiki describes for the mini client. Your game token works like a password: "
                   "never post it or the config file anywhere public.").pack(anchor="w", pady=(0, 8))

    # 1
    ttk.Label(frm, text="1. Open Kongregate in Chrome and log in", font=("TkDefaultFont", 10, "bold")).pack(anchor="w")
    ttk.Label(frm, wraplength=600, justify="left",
              text="Opens your normal Chrome (with your usual sign-ins). Log in, open the game page and make sure Bit Heroes loads.").pack(anchor="w")

    chosen = {"exe": None}

    def open_chrome():
        ok, how = launch_chrome(GAME_URL, config, chosen["exe"])
        if ok:
            chrome_msg.set("Opened with " + how)
            return
        messagebox.showinfo("Chrome not found", "I could not find Chrome automatically. Please select chrome.exe "
                            "(usually in C:\\Program Files\\Google\\Chrome\\Application).", parent=win)
        from tkinter import filedialog
        p = filedialog.askopenfilename(parent=win, title="Select chrome.exe", filetypes=[("Chrome", "chrome.exe chrome google-chrome*"), ("All files", "*")])
        if p:
            chosen["exe"] = p
            ok, how = launch_chrome(GAME_URL, config, p)
            chrome_msg.set(("Opened with " + how) if ok else "That file would not start.")
        else:
            chrome_msg.set("Chrome not selected. Open %s in Chrome yourself, then continue with step 2." % GAME_URL)
    chrome_msg = tk.StringVar()
    ttk.Button(frm, text="Open Kongregate in Chrome", command=open_chrome).pack(anchor="w", pady=(2, 0))
    ttk.Label(frm, textvariable=chrome_msg, wraplength=600, foreground="#555").pack(anchor="w", pady=(0, 8))

    # 2
    ttk.Label(frm, text="2. Run the account script in Chrome's console", font=("TkDefaultFont", 10, "bold")).pack(anchor="w")
    ttk.Label(frm, wraplength=600, justify="left",
              text="Click the button to copy the script. In Chrome press F12, open the Console tab, paste (Ctrl+V) and press Enter. "
                   "If Chrome blocks pasting, type  allow pasting  and press Enter first, then paste again.").pack(anchor="w")
    src = None                                           # always the built-in script (see FALLBACK_SCRIPT)
    ttk.Label(frm, foreground="#555", text="This is the helper's own script (the original's %s no longer works on Kongregate's current page)." % SCRIPT_NAME).pack(anchor="w")

    def copy_script():
        text = src.read_text(encoding="utf-8", errors="replace") if src else FALLBACK_SCRIPT
        win.clipboard_clear()
        win.clipboard_append(text)
        copied.set("Copied (%d characters). Now paste it into Chrome's console." % len(text))
    copied = tk.StringVar()
    ttk.Button(frm, text="Copy the console script", command=copy_script).pack(anchor="w", pady=(2, 0))
    ttk.Label(frm, textvariable=copied, foreground="#262").pack(anchor="w")
    ttk.Label(frm, wraplength=620, justify="left", foreground="#444", text=(
        "Nothing shows up, or only the word 'undefined'?\n"
        " - Clear the Filter box in the console toolbar (a leftover filter such as 'top' hides the output) and make sure the "
        "'Default levels' / 'Info' option is ticked.\n"
        " - 'undefined' on its own line after the output is normal. Look for the lines starting with 1.game.kong...\n"
        " - The context dropdown at the top of the console should say 'top'. Run the script on the Kongregate game page, "
        "while logged in and with the game visible.\n"
        " - The script also copies its result to your clipboard, so you can use 'Paste from clipboard' in step 3.")).pack(anchor="w", pady=(2, 8))

    # 3
    ttk.Label(frm, text="3. Paste the console output here and save it", font=("TkDefaultFont", 10, "bold")).pack(anchor="w")
    row = ttk.Frame(frm)
    row.pack(anchor="w", pady=2)
    ttk.Label(row, text="Save as account number").pack(side="left")
    acct = tk.StringVar(value="1")
    ttk.Spinbox(row, from_=1, to=10, width=3, textvariable=acct).pack(side="left", padx=4)
    ttk.Label(row, text="(1 = first account)").pack(side="left")
    box = tk.Text(frm, height=6, wrap="word")

    def paste_clip():
        try:
            box.delete("1.0", "end")
            box.insert("end", win.clipboard_get())
        except tk.TclError:
            result.set("The clipboard is empty or does not contain text.")
    ttk.Button(frm, text="Paste from clipboard", command=paste_clip).pack(anchor="w")
    box.pack(fill="x", pady=2)
    result = tk.StringVar()

    def save():
        values, problems = parse_console_output(box.get("1.0", "end"))
        if problems:
            result.set("\n".join(problems))
            return
        try:
            n = min(max(int(acct.get()), 1), 10)
        except ValueError:
            n = 1
        try:
            backup = save_account(config, values, n)
        except OSError as e:
            result.set("Could not write %s: %s" % (config, e))
            return
        box.delete("1.0", "end")                                    # don't leave the token sitting in the window
        result.set("Saved account %d (%s) to %s%s." % (n, values["game.kong.user.name"], config.name,
                                                       "; the old file is kept as %s" % backup.name if backup else ""))
    ttk.Button(frm, text="Save to user-config.properties", command=save).pack(anchor="w", pady=2)
    ttk.Label(frm, textvariable=result, wraplength=600, justify="left").pack(anchor="w", pady=(0, 8))

    # 4
    ttk.Label(frm, text="4. Create the game files", font=("TkDefaultFont", 10, "bold")).pack(anchor="w")
    ttk.Label(frm, wraplength=620, justify="left",
              text="Creates the game page for every account in your config (bh-client/index<N>.html). This bot does that itself, so Java and "
                   "the original's client.bat are NOT needed.").pack(anchor="w")
    use_orig = tk.BooleanVar(value=False)
    ttk.Checkbutton(frm, variable=use_orig, text="Also run the original %s (only needed for the original Java bot; requires Java)" %
                    ("client.bat" if IS_WIN else "client.sh")).pack(anchor="w")
    out = tk.Text(frm, height=6, wrap="word", state="disabled", background="#f4f4f4")

    def gen():
        ok, lines = make_pages(bot_dir)
        text = "\n".join(lines)
        if use_orig.get():
            if not java_available():
                text += "\n\nThe original client script was skipped: Java was not found on this computer (it is not needed for this bot)."
            else:
                ok2, out2 = run_client(bot_dir)
                text += "\n\n[original client script]\n" + out2.strip()
                files = client_files(bot_dir)
                text += "\n" + (("Generated: " + ", ".join(files)) if files else "It created no mini-client files.")
        out.configure(state="normal")
        out.delete("1.0", "end")
        out.insert("end", text)
        out.configure(state="disabled")
        final.set("Done - you can close this window and start the bot." if ok else "Something went wrong - see the output below.")
    final = tk.StringVar()
    ttk.Button(frm, text="Create the game files", command=gen).pack(anchor="w", pady=2)
    ttk.Label(frm, textvariable=final, font=("TkDefaultFont", 10, "bold")).pack(anchor="w")
    out.pack(fill="both", expand=True, pady=2)
    return win
