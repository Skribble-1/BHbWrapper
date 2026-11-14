==============================================================================
  - BHbWrapper -
  A Bot that grinds Bit Heroes for you
==============================================================================
- - Note - - 
I tried to make this as simple as possible - so long as you READ, you shouldn't 
have any trouble getting this to run...
(V1.1 adds Linux and macOS start files - (UNTESTED, Linux and MAC community will 
need to provide input/feedback for proper use: see "LINUX AND MACOS" further down.))

  This bot fishes and runs the "AFK chain" (Quest, PVP, World Boss, Raid, Trials,
Gauntlet, GVG, Expedition, Invasion ...). 

  This project started off as an add-on wrapper built on top of the Bit Heroes bot 
by 9-9-9-9 (https://github.com/9-9-9-9/Bit-Heroes-bot), whose files can be found 
there.
  The original version of this bot (was just a gui wrapper back then) required use
  of those files and has since transformed into a standalone bot.

YOU'll NEED
  - A Windows PC  (Linux and macOS are UNTESTED: see "LINUX AND MACOS" below)
  - Internet access
  - Google Chrome browser
  - A Kongregate account that can already play Bit Heroes in your browser:
      https://www.kongregate.com/games/Juppiomenz/bit-heroes

NOTE - This is the browser version ONLY. The Steam version is NOT supported.

------------------------------------------------------------------------------
  1. DOWNLOAD / SETUP
------------------------------------------------------------------------------
  1. Open  https://github.com/Skribble-1/BHbWrapper
  2. Click the green "Code" button > "Download ZIP".
  3. Right-click the zip file > Properties > tick "Unblock" if you see it > OK.
  4. Right-click the zip file > "Extract All..." 
  5. Extract to your desired directory such as your Documents folder. You get
     a folder (named BHbWrapper-main or similar): this is THE MAIN FOLDER.
  6. Do not move the "BHPyBot" folder out of it: it must stay right next
     to  start_win-gui.bat.

Expected folder layout:
  C:\<Your Directory>\
    BHbWrapper-main\            <- THE MAIN FOLDER
      BHPyBot\                  <- the bot and the window (bhbot.py, bh_gui.py ...)
        Scripts\               <- the scripts you pick in the window (Fish.bat, AFK.bat ...)
      sample-scripts(Legacy)\   <- old scripts of the original Java bot (not used)
      start_win-gui.bat
  Your account files (user-config.properties, bh-client\) are created in THE
  MAIN FOLDER by the setup below.

------------------------------------------------------------------------------
  2. START THE WINDOW  (the first start installs what is needed)
------------------------------------------------------------------------------
  1. Double-click  start_win-gui.bat  in THE MAIN FOLDER.
  2. "Windows protected your PC"? Click "More info" > "Run anyway".
  3. First time only: it installs Python (if missing) and a few packages. This
     can take several minutes.
       - Click Yes if Windows asks for permission.
       - If it says Python was just installed, close the command (black) window and
         double-click start_win-gui.bat again.
  4. The "BH Bot Controller" window opens. If it asks for the main folder,
     select THE MAIN FOLDER (the one that contains the BHPyBot folder).

  Hint: right-click start_win-gui.bat > Send to > Desktop (create shortcut) for
  one-click starts later.
  Hint: Use task scheduler to set it on a schedule to auto run on a set time or 
  when the computer restarts/logs on for further afk automation.

------------------------------------------------------------------------------
  3. LINK YOUR GAME ACCOUNT  (button: "Initial Setup Help")  - once
------------------------------------------------------------------------------
On the GUI, Click "Initial Setup Help" and perform the four steps:
    1. Open Kongregate in Chrome: log in and wait until Bit Heroes has loaded to the 
main game screen (you can move your character around the map). Keep that tab open.
    2. Copy the console script:
In that Chrome tab press F12 to open the developer console, 
Click the "Console" tab, 
Press Ctrl+V and Enter. 
  (If Chrome blocks pasting, follow the instructions - type allow pasting, press Enter, and paste again.) 
Three lines starting with 1.game.kong... appear.
    3. Paste from clipboard: 
Choose account number 1, 
Click "Save to user-config.properties". 
Your old file is kept as a .bak copy.
    4. Create the game files: 
It should say "created ...index1.html".

------------------------------------------------------------------------------
Troubleshooting step 3 if needed...
------------------------------------------------------------------------------
IF:
  Nothing shows in the Console, or only the word "undefined"
    - Clear the Filter box in the Console toolbar (an old filter next to
      "top" hides the answer)
    - Make sure "Default levels" / "Info" is on.
    - "undefined" alone on a line AFTER the three lines is normal.
    - Make sure to be in the game and able to move around while logged into your
      Kongregate account.
  You need to setup a second account (not a second character on the same account)
    - Log into the second account and run steps 1-4 again, choose account number 2 
      (use AFK2.bat for it).


------------------------------------------------------------------------------
- - - - - - - - IMPORTANT - - - - - - - -
------------------------------------------------------------------------------
KEEP YOUR TOKEN PRIVATE. The "1.game.auth.token=" line in
  user-config.properties works like a password. Never post that file or a
  screenshot of it. 
If the bot later cannot log in, the token may have expired (it changes when you 
  log in and out out of Kongregate): repeat steps above in the dev console to acquire
  your new token.

------------------------------------------------------------------------------
  4. FIRST RUN
------------------------------------------------------------------------------
I recommend trying 'BHPyBot/Scripts/Fish.bat' in the 'Script' box and in the 'Then run'
    box: BHPyBot/Scripts/AFK.bat.
    Click Start. A small game window opens by itself and logs in.
    Fish.bat fishes 18 times (at default; edit the number in the .bat to change it), then
    AFK.bat runs the full event chain. Between the two scripts, the ESC button is pressed
    a few times to ensure any stray menus are closed.

Note - For a safe test: BHPyBot/Scripts/AFK.watch.bat only says in the console what it sees and 
    what it would do; it never clicks. Useful for troubleshooting / testing.

------------------------------------------------------------------------------
- - - - - - - - Buttons / Controls - - - - - - - -
------------------------------------------------------------------------------
Overrides: the Quest, PVP, World Boss, Raid, Trials, Gauntlet, GVG,
     Expedition, and Invasion buttons choose what each event does. A dot on a
     button means its override is on. They only matter when switched on - your
     normal account settings are the default - and they apply while the bot is
     running. When off, your default account settings file is applied.
     Two heroes: the box "Override settings for" under those buttons picks
     whose settings the buttons edit - "Both heroes" (shared), "Left hero" or
     "Right hero" (the portrait on the YOUR HEROES page). A hero's own setting
     replaces the shared one while that hero plays; a hero without one uses the
     shared setting. Dot = this hero's own setting is on, hollow dot = it uses a
     shared setting that is on. The bot opens YOUR HEROES once at the start (and
     after each hero switch) to see which hero is active. Needs a 2nd hero.

Buttons and boxes in the window:
    Pause / Force Stop         freeze / end the bot. "Force Stop also closes
                               the game window" closes the game as well.
    Compact log                hides repetitive "waiting" lines.
    Full restart every N min   closes the game and restarts everything on a
                               timer (0 = never).
    If the screen is stuck     after 3 identical unknown screenshots: do
                               nothing / restart game + scripts / shut down.
    Maintenance wait           if the game says it is down for maintenance, it
                               closes the game and starts again after N min.
    Clear out/unknown ...      deletes old unknown-screen screenshots when you
                               click Start (default 14 days).
    Alternate characters...    (needs a 2nd hero unlocked) switches between your
                               two heroes: when every task of one is waiting for
                               tickets/energy, and just before a 'Full restart'.
                               Hover over it for the details.
    Base Check Interval        extra pause between looks when EVERY task is
                               waiting for tickets/energy (default 15 s).
                               Higher = cooler PC.
    Look at the game every     how fast the bot reacts while something is
                               going on (default 1 s; 2-3 s is fine).
    Repeat 'waiting' messages  only changes how often the log repeats a line.
    (Hover over any setting in the window for what it does and where it is used.)

Hints:
    - Keep the PC awake (Windows sleep stops the bot) and leave the little game
      window open. Playing the same account elsewhere will disconnect it.
    - The bot recognises the game by pictures, so a game update can break
      parts of it until it is updated.

------------------------------------------------------------------------------
  LINUX AND MACOS  -  NOT TESTED, COMMUNITY HELP WANTED
------------------------------------------------------------------------------
  The Linux / macOS support was written WITHOUT access to a Linux or Mac machine.
  Nothing in this section has been run on those systems, and the author does not
  plan to test it. It relies on the community to try it, report what breaks and
  send improvements (issues / pull requests on GitHub, with log + screenshot and
  your personal data removed). The Windows version is the one that has been used.

  What exists for it:
    start_lin-gui.sh           Linux: replaces start_win-gui.bat (checks Python 3 + tkinter,
                               makes a private .venv, installs opencv-python, numpy,
                               psutil, then opens the window)
    start_mac-gui.command      macOS: the same, double-click it in Finder
    BHPyBot/Scripts/*.sh       the same scripts as the .bat files (Fish, AFK, AFK2,
                               AFK.watch, Esc); the window lists .sh files on Linux/macOS
  Needs (install yourself, the scripts only tell you how):
    Python 3.8+ with tkinter  Debian/Ubuntu: sudo apt install python3 python3-venv python3-tk
                              macOS: brew install python python-tk
    Google Chrome or Chromium (Chrome in a snap/flatpak may not accept the control port; use the
                              .deb / official Chrome if the game window never opens)
  Start:   Linux: bash start_lin-gui.sh      macOS: double-click start_mac-gui.command
           (first start installs the packages: a few minutes)
           Without a desktop (server / NUC without monitor): run a script directly,
           e.g.  bash BHPyBot/Scripts/AFK.sh   - Chrome then starts headless automatically.
  Chrome path: if it is not found, add to user-config.properties (main folder):
           external.application.chrome.path=/usr/bin/google-chrome
           (macOS: /Applications/Google Chrome.app/Contents/MacOS/Google Chrome)
  Differences from Windows:
    - The ESC presses between two scripts go through the bot (Chrome's control port),
      not real key presses. Untested.
    - Running as root adds --no-sandbox to Chrome automatically.
    - The Initial Setup Help window's "Open Kongregate in Chrome" button starts Chrome directly.
    - No auto-install of Python or Chrome.
  Always Restart / Force Stop close only the bot's own Chrome (the one using the bot's
  control port / chrome-user-dir); your own Chrome is left alone. Untested on Linux/macOS.

------------------------------------------------------------------------------
  IF SOMETHING GOES WRONG
------------------------------------------------------------------------------
  "Chrome not found"       The Initial Setup Help window asks you to pick
                           chrome.exe. Or add this line to
                           user-config.properties:
                           external.application.chrome.path=C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe
  "Python not found"       Close everything and run start_win-gui.bat again.
  Game window is blank     Close it, then click Start again.
  Bot seems stuck          Screenshots go to  BHPyBot\out\unknown . When
                           you ask for help, send both associated screenshots.
                           Hide your name and NEVER include the token.
  Settings seem ignored    Delete BHPyBot\overrides.json to go back to the
                           defaults.

------------------------------------------------------------------------------
  WHICH FILES ARE USED
------------------------------------------------------------------------------
  USED:
    bh-client\                         two Kongregate script files the game page needs
    start_win-gui.bat                     opens the window (installs Python, etc.)
    start_lin-gui.sh / start_mac-gui.command   Linux / macOS versions (UNTESTED)
    BHPyBot\bh_gui.py                  the controller window
    BHPyBot\bh_setup.py                the "Initial Setup Help" window
    BHPyBot\bhbot.py                   the bot (Python)
    BHPyBot\templates\                 the pictures/colour patterns it knows
    BHPyBot\Scripts\*.bat              Fish, AFK, AFK2, AFK.watch, Esc (the scripts the window lists)
    BHPyBot/Scripts/*.sh               the same for Linux / macOS (UNTESTED; _env.sh is shared by them)
    BHPyBot\overrides.json             made by the override buttons
    BHPyBot\out\                       screenshots the bot saves
  Created in THE MAIN FOLDER by the setup:
    user-config.properties (+ .bak)    your account (id, name, token)
    readonly.<name>.user-config.properties
                                       optional per-account profile; not needed
    bh-client\                         the game page files (made from your account)
    chrome-user-dir\                   the bot's own Chrome profile (automatic)

  LEGACY (original Java bot; NOT used, would need Java):
    sample-scripts(Legacy), LICENSE and README.md (the original's, kept for
    history).

------------------------------------------------------------------------------
  ADVANCED (optional)
------------------------------------------------------------------------------
  Install by hand:  pip install opencv-python numpy psutil
  Trials/Gauntlet tiers: the bot recognises the tier labels it has seen
  (Trials 13-15) and counts rows from a known tier for others. To teach it one:
  open the list, save a screenshot (python bhbot.py snap), then
    python bhbot.py template out\snap\<file>.png trials.tier.12 --rect=x,y,w,h --ink CCCCCC
  (a box around the text "TIER 12"). Run  python bhbot.py selftest  to check
  the templates.

------------------------------------------------------------------------------
  GOOD TO KNOW
------------------------------------------------------------------------------
  Unofficial community tool. Automating a game may be against its rules; use
  it at your own risk. Credit for the original bot and its ideas: 9-9-9-9
  (https://github.com/9-9-9-9/Bit-Heroes-bot).

Did you find this useful? - Let me know - Star it.
