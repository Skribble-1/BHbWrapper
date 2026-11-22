# Changelog

## V2.0
The bot is rebuilt around one question, "which screen is this?". Everything below was run live on both heroes during the release candidates.

**New**
- **Screen classifier** (`engine/screens.py`, 78 test screenshots in `BHPyBot/tests/screens`, checked by `bhbot.py selftest`) and **event handlers** (`engine/handlers.py`) for Quest, PVP, Raid, Expedition, Trials, World Boss, Gauntlet, GVG, Invasion. One popup table closes the harmless popups (News, items, familiars, chat, profiles, terms of service ...).
- **Shared first startup** (`Bot.startup`): after every game start it waits for the login, answers "recently disconnected from a dungeon", claims daily rewards, closes the News popup, checks which hero is active and collects the bounties, then hands over to the script.
- **Watchdogs that restart the game:** crashed / hung Chrome tab, Unity error dialog (closed first), flat grey page (80 s), completely black picture (180 s), frozen battle (240 s), identical unknown screens (stuck guard). Exit code 7 restarts regardless of settings.
- **GUI:** the bottom settings are in a **Settings** window; **Troubleshooting** window with an interval per diagnostic (default 3 s), Hide on all, Off only on the optional ones (Screen check, Battle check, Event handler lines; Chrome health and Frozen battle are required). Starts at 1000 x 900. The Compact log button is gone (replaced by Troubleshooting > Hide).
- **Per-hero override windows** for every event; **placement pickers** for PVP and GVG (1st, 2nd, 3rd row); **exact numbers** for Trials, Gauntlet, Expedition difficulty and Invasion wave (tier notes for all Trials / Gauntlet tiers).
- **Scripts that cannot run** on a hero (Fish.bat before fishing is unlocked) end with exit code 11: no restart, the next script runs.
- **GVG** is back, including the weekly "You cannot leave or be removed from your guild" popup; GVG owns the shared "Select opponent" list while a GVG run is on.
- Black screens between scenes are recognised (no more "unknown screen" pictures); Fish.bat closes the News popup; Quest zone loops are detected and closed; calm World Boss lobbies are no longer saved as unknown.
- **Evidence pictures:** unknown screens are saved (annotated + `_matches.txt`) after 10 s of standing still, with a re-check so one-frame flickers are dropped.
- **`start_win-gui.bat --start`** (also `start_lin-gui.sh` / `start_mac-gui.command`): Running in a CLI with the "--start" option opens the controller and then presses Start by itself after 5 s with the saved script choice and settings. README has the Task Scheduler recipe ("When I log on") for near full automation.

**Code**
- `bhbot.py` is now a small launcher; the bot lives in `BHPyBot/engine/` (common, game, vision, bot, config, afk, commands, screens, handlers, picker).
- Template matching: exact white-ink masks with a 1 px border ring; a 2 % tolerance for the place icons; AFK margin 80 px.
- Removed: the Compact log filter, the shadow / popup / battle environment switches (`BHBOT_SHADOW`, `BHBOT_POPUPS`, `BHBOT_BATTLE`), the classifier and handler switches (`BHBOT_GATE`, `BHBOT_HANDLERS`) and the old popup fallback code.

**Known limits:** browser version only; Linux / macOS start files are untested; Unity crashes inside the game (for example right after a hero switch) can only be recovered by restarting.

## V1.1
- **Linux and macOS support (UNTESTED - the project relies on the community to test and improve it):** `start_lin-gui.sh`,
  `start_mac-gui.command`, `BHPyBot/Scripts/*.sh`, Linux/macOS Chrome detection, headless start without a desktop, ESC through the bot.
  The Windows start file is now `start_win-gui.bat` (was `~start_gui.bat`).
- **Overrides per hero:** "Override settings for: Both heroes / Left hero / Right hero". Every event can be switched off per hero.
- **Events a hero has not unlocked** (low-level second hero) are recognised, paused and logged once instead of repeating screenshots.
- **Main-map detection** also uses the bottom menu row, so heroes with few icons are no longer treated as "unknown screen".
- **Quest boss dialogues:** the yellow "next" arrow is found reliably in dark rooms; no more stray ESC presses.
- **Invasion:** the "wait is over - checking again" message no longer repeats every look when GVG / Expedition are switched off.
- `.gitattributes` keeps `.bat` files CRLF and `.sh` files LF.

## V1.0
- First public release.
