#!/usr/bin/env python3
"""bhbot launcher - the bot itself lives in the engine/ folder (V2.0). Run `python bhbot.py <command>`
(afk, fish, esc, launch, snap, check, template, screen, profile, selftest; `-h` lists them)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from engine.commands import main

if __name__ == "__main__":
    sys.exit(main())
