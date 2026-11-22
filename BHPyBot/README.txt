==============================================================================
  BHbWrapper  -  README
  A Windows window ("BH Bot Controller") that plays Bit Heroes for you
==============================================================================
- - Note - - 
I tried to make this as simple as possible - so long as you READ, you shouldn't 
have any trouble getting this to run.

(Linux and macOS start files are included but untested, community help wanted: see "LINUX AND MACOS" further down.)

YOU'll NEED
  - A Windows PC  (Linux and macOS: see "LINUX AND MACOS" below - UNTESTED)
  - Internet access
  - Google Chrome browser
  - A Kongregate account that can already play Bit Heroes in your browser:
      https://www.kongregate.com/games/Juppiomenz/bit-heroes

NOTE - This is the browser version ONLY. The Steam version is NOT supported; 

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
      start_win-gui.bat
  Your account files (user-config.properties, bh-client\) are created in THE MAIN FOLDER by the setup below.

------------------------------------------------------------------------------
  2. START THE WINDOW  (the first start installs what is needed)
------------------------------------------------------------------------------
  1. Double-click  start_win-gui.bat  in THE MAIN FOLDER.
  2. "Windows protected your PC"? Click "More info" > "Run anyway".
  3. First time only: it installs Python (if missing) and a few packages.
       - Click Yes if Windows asks for permission.
       - If it says Python was just installed, close the command (black) window and
         run start_win-gui.bat again.
  4. The "BH Bot Controller" window opens. If it asks for the main folder,
     select THE MAIN FOLDER (the one that contains the BHPyBot folder).

------------------------------------------------------------------------------
  3. LINK YOUR GAME ACCOUNT (button: "Initial Setup Help") - <<--Click that button!>
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

You should now be ready to run the AFK and Fish .bat scripts. 

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
I recommend starting with 'BHPyBot/Scripts/Fish.bat' in the 'Script' box 
and in the 'Then run' box 'BHPyBot/Scripts/AFK.bat'.
    Click Start. (A small game window opens by itself and logs in.)
    Fish.bat fishes 18 times (at default; edit the number in the .bat to change it), then
    AFK.bat runs the full event chain. 
- Between the two scripts, the ESC button is pressed a few times to ensure any stray menus are closed.
- If you have more than one Hero unlocked, it'll swap to your second hero
  - I don't have an account with more than 2 to test, No support for 3+ Heroes at this time (V2.0).

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
    Troubleshooting            opens the list of diagnostic functions (see "TROUBLESHOOTING MENU" below).
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

------------------------------------------------------------------------------
  NEAR FULL AUTOMATION  (start the bot by itself when you log on to Windows)
------------------------------------------------------------------------------
  start_win-gui.bat --start   opens the window and presses Start for you after 5 seconds, with the script(s) and settings you used last time. To run it automatically with Windows Task Scheduler:
    1. Start menu > "Task Scheduler" > "Create Basic Task..."
    2. Give it a name. 
    3. Set the Trigger: "When I log on".
    4. Set the Action: "Start a program".  
        - Program/script:   "C:\<Your Directory>\BHbWrapper\start_win-gui.bat"   (keep the quotes)
        - Add arguments:  --start        
    Note for Linux / macOS: 
        - Your start files accept --start too (use your own scheduler, e.g. cron @reboot / launchd)... untested.
    IMPORTANT NOTE FOR ALL:
        - Do not set this up first, make sure the bot runs without issue before setting up these auto runs.

------------------------------------------------------------------------------
  SCREEN CHECK  (what the "[screen]" log lines mean)
------------------------------------------------------------------------------
  The bot asks a "which screen is this?" function (screens.py) every few seconds (Troubleshooting menu, default 3) and writes a
  line like   [screen] main_map   or   [screen] battle   to the log whenever the answer changes.
    - a line that does not match what the game shows = a wrong rule: send the line and the screenshot
    - "[screen] unknown" = a screen it has no rule for: the picture is saved in BHPyBot\out\unknown (screen_unknown)
  Try a saved screenshot:   python bhbot.py screen out\unknown\somefile.png
  Switch the lines off:     GUI > Troubleshooting > Screen check > Off (or Hide to only remove the lines from the window)

------------------------------------------------------------------------------
  TROUBLESHOOTING MENU  (GUI button "Troubleshooting"; saved in overrides.json -> "troubleshooting")
------------------------------------------------------------------------------
  Function            Log lines   Off switch   What it does
  Screen check        [screen]    yes          names the screen the bot sees; saves "unknown screen" pictures
  Battle check        [battle-check] yes       compares the battle check with the classifier; saves a picture on disagreement
  Event handler lines [handler]   yes          says which event handled which screen
  Chrome health       [health]    NO           crashed / hung Chrome, Unity error dialog, flat grey page -> restarts the game
  Frozen battle       [frozen]    NO           battle picture unchanged for 4 minutes -> restarts the game
  Every (s): how often the function runs. Default 3, range 1-60 (Chrome health and Frozen battle 1-30).
  The interval only sets how often it LOOKS; the restart rules are in seconds and do not depend on it
  (Chrome check failing 40 s, grey page 80 s, completely black picture 180 s, frozen battle 240 s).
  Hide: removes that function's lines from the log window only (the bot still runs it and the log file keeps them).
  Off: the function does not run at all. Only the optional ones have it; the others are required to operate the bot
  efficiently and cannot be switched off. "Unknown screen" pictures and the "stuck" restart (stuck_action) are part of
  the bot itself and always run.
  Defaults button: back to 3 s, nothing hidden, nothing off.

------------------------------------------------------------------------------
  LINUX AND MACOS  -  NOT TESTED, COMMUNITY HELP WANTED
------------------------------------------------------------------------------
  The Linux / macOS support was written WITHOUT access to a Linux or Mac machine. Nothing in this section has been run on 
  those systems. It relies on the community to try it, report what breaks and send improvements (issues / pull requests on 
  GitHub, with log + screenshot and your personal data removed).

  Linux and MAC OS files:
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
                                       per-account settings (both heroes + left / right hero lines);
                                       made by the "Create Profile File" button or `bhbot.py profile`
    bh-client\                         the game page files (made from your account)
    chrome-user-dir\                   the bot's own Chrome profile (automatic)

------------------------------------------------------------------------------
  ADVANCED (optional)
------------------------------------------------------------------------------
Tiers for Trials and Gauntlet: the bot knows the tier labels for Trials 1-15 and Gauntlet 11-15,
and counts rows from a known tier for any others. To teach it a new one:
  1. Open the tier list in the game and save a screenshot:      
      python bhbot.py snap
  2. Draw a tight box around the text "TIER ##" (use the clean .png, not the _annotated one):
       Trials:    
          python bhbot.py template out\snap\<file>.png trials.tier.## --rect=x,y,w,h
       Gauntlet:  
          python bhbot.py template out\snap\<file>.png tier.## --rect=x,y,w,h
       (add "--ink CCCCCC" after "--rect=x,y,w,h" only if the text colour is not detected correctly)
  3. Check all templates:                                       
      python bhbot.py selftest
  4. Restart the script so it loads the new template.


Did you find this useful? - Let me know - Star it - https://github.com/Skribble-1/BHbWrapper
