# Bit Heroes bot - V2.0

**What it is:** the same bot, rebuilt around one question: *"which screen is this?"* One classifier (`engine/screens.py`) names the screen from 78
test pictures; small handlers (`engine/handlers.py`) own their events; one popup table closes the harmless popups; one shared first startup
(`Bot.startup`) deals with everything that comes first after the game starts.

## Highlights
- **Screen classifier + handlers** for Quest, PVP, Raid, Expedition, Trials, World Boss, Gauntlet, GVG, Invasion.
- **Watchdogs** that restart the game by themselves: crashed / hung Chrome tab, Unity error dialog, flat grey page, completely black screen (180 s), frozen battle (240 s), stuck unknown screen.
- **GUI:** Settings window, Troubleshooting window (interval, Hide, Off per diagnostic), per-hero overrides, placement pickers for PVP / GVG, exact difficulty numbers for Trials / Gauntlet / Expedition / Invasion.
- **Scripts that cannot run on a hero** (e.g. Fish.bat before fishing is unlocked) are skipped without a restart; the next script runs.
- **GVG** with the weekly "you cannot leave your guild during this event" popup.
- **Screen test library** (`BHPyBot/tests/screens`): every wrongly classified screenshot becomes a permanent test (`bhbot.py selftest`).
- **Code split** into `BHPyBot/engine/` behind the same start files.

## Known limits
- Browser version of Bit Heroes only. Linux / macOS start files are untested (community help wanted).
- Game-side crashes (Unity runtime errors, especially right after a hero switch) cannot be fixed by the bot; it restarts the game.
- The bot recognises the game by pictures: a game update can break parts of it until it is updated.

## Upgrading
Copy the zip over the old folder and delete the old `BHPyBot/screens.py`, `handlers.py`, `picker.py` and `__pycache__`. Your profile files and `overrides.json` are kept.
