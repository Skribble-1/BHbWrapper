#!/usr/bin/env bash
# NOT TESTED on macOS. Double-click this file in Finder to open the BH Bot Controller (it just runs start_gui.sh).
# If macOS refuses to open it: right-click > Open, or in Terminal run  chmod +x start_gui.command start_gui.sh
cd "$(dirname "$0")" || exit 1
bash ./start_gui.sh
echo; read -r -p "Press Enter to close this window..." _
