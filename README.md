# BHbWrapper - Bit Heroes bot, V2.0

A Windows OS window ("BH Bot Controller") that plays the browser version of **Bit Heroes** for you, around the clock. It watches the game through screenshots. It does not touch your mouse or keyboard and does not care about window position or resolution.

**What it does**
- Runs the AFK chain: Quest, PVP, World Boss, Raid, Trials, Gauntlet, GVG, Expedition, Invasion, plus fishing and bounties.
- Knows which screen the game is on (one screen classifier, 78 test screenshots) and lets small event handlers act on it.
- Restarts the game by itself when Chrome crashes or hangs, a Unity error dialog appears, the picture goes grey / black or a battle freezes.
- Two heroes: per-hero settings and "Alternate characters".
- - [I don't have an account with more than 2 characters to test, if you do, let me know and we'll figure out how to add in another hero rotation.]
- Settings and Troubleshooting windows, per-event override windows, placement pickers (PVP / GVG), exact difficulty numbers (Trials, Gauntlet, Expedition, Invasion).

**Needs:** Google Chrome, Python 3.12 (the start file installs it and the packages), and a Kongregate account that can already play
[Bit Heroes](https://www.kongregate.com/games/Juppiomenz/bit-heroes) in the browser. Browser version only (no Steam). Linux / macOS start files
are included but untested.

**How to Start:** 
- download the ZIP (green "Code" button), 
- extract it, 
- double-click `start_win-gui.bat` (for windows users)
    - (If it installs python or updates packages the first time, you'll need to run `start_win-gui.bat` again) 
- Then follow the full instructions here. [`BHPyBot/README.txt`](BHPyBot/README.txt).
- Only Python 3.12 was tested, if you have another version, you may need to remove it, otherwise ask the internet for other workarounds / methods.

**Credit and license:** the idea comes from the free Bit Heroes bot by 9-9-9-9 (https://github.com/9-9-9-9/Bit-Heroes-bot). GPL-3.0, see `LICENSE`.

*Disclaimer* Use it at your own risk: bots can get you banned.
- That said, I've been using this for about 3 months straight and I haven't got any warning messages or kicks.
- The bot never sends its own requests to the game server; all traffic is the normal game client’s (hard to detect, only potentially suspicious if the user has been online for months on end with no break)
- My experience is not a guarantee; with the increase in AI, who knows if they'll be able to detect this in the future.

**Friendly Reminder**
- Your login token lives in user-config.properties. Don’t share that file. 
- Don't post pictures from the BHPyBot/out/unknown folder without checking them for account names first.
- It runs 24/7 on your PC, so the machine must stay awake. Playing the same account elsewhere disconnects the bot.
- The bot recognises the game from pictures, so updates can break parts of it until it’s updated. There’s no guaranteed support or timeline.
- What to know What changed? [`CHANGELOG.md`](CHANGELOG.md) and [`RELEASE_NOTES_V2.0.md`](RELEASE_NOTES_V2.0.md).
