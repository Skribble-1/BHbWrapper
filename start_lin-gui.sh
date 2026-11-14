#!/usr/bin/env bash
# NOT TESTED on Linux - written without a Linux or Mac to try it on. The project relies on the community to test and improve it.
# Please report what works / breaks (log + screenshot, personal data removed) at https://github.com/Skribble-1/BHbWrapper/issues
#
# LINUX version of start_win-gui.bat (macOS has its own: start_mac-gui.command). Opens the BH Bot Controller (BHPyBot/bh_gui.py).
#   1. needs Python 3 with tkinter and Google Chrome (or Chromium) already installed - it only tells you how, it installs no system software
#   2. creates a private Python environment in  .venv  next to this file and installs opencv-python, numpy, psutil there
# Run:  bash start_lin-gui.sh      (or  chmod +x start_lin-gui.sh  and  ./start_lin-gui.sh)
# Runs in this terminal; closing the terminal closes the window. For a headless/always-on box:  nohup ./start_lin-gui.sh >/dev/null 2>&1 &
cd "$(dirname "$0")" || exit 1
HERE="$(pwd)"
OS="$(uname -s)"

PY=""
for c in python3 python; do
    if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info[:2] >= (3, 8) else 1)' 2>/dev/null; then PY="$c"; break; fi
done
if [ -z "$PY" ]; then
    echo "Python 3 (3.8 or newer) was not found."
    if [ "$OS" = "Darwin" ]; then echo "Install it with:  brew install python python-tk   (or from https://www.python.org/downloads/)"
    else echo "Install it with your package manager, e.g.:  sudo apt install python3 python3-venv python3-tk"; fi
    exit 1
fi

if ! "$PY" -c 'import tkinter' 2>/dev/null; then
    echo "This Python has no tkinter (needed for the window)."
    if [ "$OS" = "Darwin" ]; then echo "Install it with:  brew install python-tk   (or use the installer from python.org)"
    else echo "Install it with:  sudo apt install python3-tk   (Fedora: sudo dnf install python3-tkinter)"; fi
    exit 1
fi

if [ ! -x "$HERE/.venv/bin/python" ]; then
    echo "Creating the Python environment (.venv) ..."
    if ! "$PY" -m venv "$HERE/.venv"; then
        rm -rf "$HERE/.venv"
        echo "Could not create a virtual environment."
        echo "Debian/Ubuntu: sudo apt install python3-venv   then run this file again."
        exit 1
    fi
fi
VPY="$HERE/.venv/bin/python"
for pair in cv2:opencv-python numpy:numpy psutil:psutil; do
    mod="${pair%%:*}"; pkg="${pair##*:}"
    if ! "$VPY" -c "import $mod" 2>/dev/null; then
        echo "Installing $pkg ..."
        "$VPY" -m pip install --disable-pip-version-check "$pkg" || { echo "Could not install $pkg. Check your internet connection and run this file again."; exit 1; }
    fi
done

# the bot needs Chrome for the game window (warning only)
if ! command -v google-chrome >/dev/null 2>&1 && ! command -v google-chrome-stable >/dev/null 2>&1 && ! command -v chromium >/dev/null 2>&1 \
   && ! command -v chromium-browser >/dev/null 2>&1 && [ ! -x "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" ] \
   && [ ! -x "$HOME/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" ]; then
    echo "Warning: Chrome / Chromium was not found. The bot needs it to show the game."
    echo "(or set external.application.chrome.path in user-config.properties)"
    sleep 4
fi

if [ "$OS" != "Darwin" ] && [ -z "$DISPLAY" ] && [ -z "$WAYLAND_DISPLAY" ]; then
    echo "No desktop display found: the window cannot open here. Run a script directly instead, e.g.:  bash BHPyBot/Scripts/AFK.sh"
    exit 1
fi

exec "$VPY" BHPyBot/bh_gui.py
