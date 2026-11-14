#!/usr/bin/env bash
# NOT TESTED on Linux/macOS - written without a Linux or Mac to try it on. The project relies on the community to test and improve it.
# Please report what works / breaks (log + screenshot, personal data removed) at https://github.com/Skribble-1/BHbWrapper/issues
# Press ESC 4 times in the game: back to the main screen. Use between two scripts.
. "$(dirname "$0")/_env.sh"
exec "$PY" bhbot.py esc 4
