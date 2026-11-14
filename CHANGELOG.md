# Changelog

## V1.1
- **Linux and macOS support (UNTESTED - the project relies on the community to test and improve it):**
- For Windows, use `start_win-gui.bat`
- For Linux, use `start_lin-gui.sh`
- For MAC, use  `start_mac-gui.command`
- `BHPyBot/Scripts/*.sh` files added for Linux/macOS Chrome detection, headless start without a desktop, and ESC through the bot.
- **Overrides per hero:** "Override settings for: Both heroes / Left hero / Right hero". Every event can be switched off per hero.
- **Events a hero has not unlocked** (low-level second hero) are recognised, paused and logged once instead of repeating screenshots.
- **Main-map detection** also uses the bottom menu row, so heroes with few icons are no longer treated as "unknown screen".
- **Quest boss dialogues:** the yellow "next" arrow is found reliably in dark rooms; no more stray ESC presses.
- **Invasion:** the "wait is over - checking again" message no longer repeats every look when GVG / Expedition are switched off.
- `.gitattributes` keeps `.bat` files CRLF and `.sh` files LF.

## V1.0
- First public release.
