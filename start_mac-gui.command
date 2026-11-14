#!/usr/bin/env bash
# NOT TESTED on macOS - written without a Mac to try it on. The project relies on the community to test and improve it.
# Please report what works / breaks (log + screenshot, personal data removed) at https://github.com/Skribble-1/BHbWrapper/issues
#
# macOS version of start_win-gui.bat. Double-click this file in Finder to open the BH Bot Controller (BHPyBot/bh_gui.py).
# If macOS refuses to open it: right-click > Open, or in Terminal run  chmod +x start_mac-gui.command
#   1. needs Python 3 with tkinter and Google Chrome already installed - it only tells you how, it installs no system software
#   2. creates a private Python environment in  .venv  next to this file and installs opencv-python, numpy, psutil there
cd "$(dirname "$0")" || exit 1
HERE="$(pwd)"

PY=""
for c in python3 python; do
    if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info[:2] >= (3, 8) else 1)' 2>/dev/null; then PY="$c"; break; fi
done
if [ -z "$PY" ]; then
    echo "Python 3 (3.8 or newer) was not found."
    echo "Install it with:  brew install python python-tk   (or from https://www.python.org/downloads/)"
    read -r -p "Press Enter to close this window..." _; exit 1
fi
if ! "$PY" -c 'import tkinter' 2>/dev/null; then
    echo "This Python has no (working) tkinter, which the window needs."
    echo "Install it with:  brew install python-tk   (or use the installer from python.org)"
    read -r -p "Press Enter to close this window..." _; exit 1
fi

if [ ! -x "$HERE/.venv/bin/python" ]; then
    echo "Creating the Python environment (.venv) ..."
    if ! "$PY" -m venv "$HERE/.venv"; then
        rm -rf "$HERE/.venv"
        echo "Could not create a virtual environment."
        read -r -p "Press Enter to close this window..." _; exit 1
    fi
fi
VPY="$HERE/.venv/bin/python"
for pair in cv2:opencv-python numpy:numpy psutil:psutil; do
    mod="${pair%%:*}"; pkg="${pair##*:}"
    if ! "$VPY" -c "import $mod" 2>/dev/null; then
        echo "Installing $pkg ..."
        "$VPY" -m pip install --disable-pip-version-check "$pkg" || { echo "Could not install $pkg. Check your internet connection and run this file again."; read -r -p "Press Enter to close this window..." _; exit 1; }
    fi
done

# the bot needs Chrome for the game window (warning only)
if [ ! -x "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" ] && [ ! -x "$HOME/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" ]; then
    echo "Warning: Google Chrome was not found in /Applications. The bot needs it to show the game."
    echo "(or set external.application.chrome.path in user-config.properties)"
    sleep 4
fi

"$VPY" BHPyBot/bh_gui.py
echo; read -r -p "The window was closed. Press Enter to close this Terminal window..." _
