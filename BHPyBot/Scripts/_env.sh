#!/usr/bin/env bash
# NOT TESTED on Linux/macOS - written without a Linux or Mac to try it on. The project relies on the community to test and improve it.
# Please report what works / breaks (log + screenshot, personal data removed) at https://github.com/Skribble-1/BHbWrapper/issues
# Shared by the other .sh scripts in this folder (they "source" it; it is not listed in the window).
# Moves into the BHPyBot folder and picks Python: the .venv that start_lin-gui.sh / start_mac-gui.command made, else python3.
cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1
if [ -x "../.venv/bin/python" ]; then PY="$(cd .. && pwd)/.venv/bin/python"; else PY="python3"; fi
