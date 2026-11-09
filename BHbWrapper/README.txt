==============================================================================
  BHbWrapper  -  README
  A Windows window ("BH Bot Controller") that plays Bit Heroes for you
==============================================================================

It fishes, and it runs an "AFK chain" (Quest, PVP, World Boss, Raid, Trials,
Gauntlet, GVG, Expedition, Invasion ...). It is an add-on built on the free
Bit Heroes bot by 9-9-9-9 (https://github.com/9-9-9-9/Bit-Heroes-bot), whose
files are all included in this repository.

YOU NEED
  - A Windows PC and the normal Google Chrome
  - A Kongregate account that can already play Bit Heroes in your browser:
      https://www.kongregate.com/games/Juppiomenz/bit-heroes
  - Internet for the first start (it installs Python and a few packages)

  CHROME / KONGREGATE ONLY. The Steam version is NOT supported.
  You do NOT need Java.

------------------------------------------------------------------------------
  1. DOWNLOAD
------------------------------------------------------------------------------
  1. Open  https://github.com/Skribble-1/BHbWrapper
  2. Click the green "Code" button > "Download ZIP".
  3. Right-click the downloaded .zip > Properties > tick "Unblock" if you see
     it > OK.  (Skipping this can make Windows block the files.)
  4. Right-click the .zip > "Extract All..." and extract to a simple place such
     as your Documents folder. You get one folder: this is "THE BOT FOLDER".
  5. Do not move the "BHbWrapper" folder out of it: it must stay right next
     to  ~start_gui.bat.

------------------------------------------------------------------------------
  2. START THE WINDOW  (the first start installs what is needed)
------------------------------------------------------------------------------
  1. Double-click  ~start_gui.bat  in THE BOT FOLDER.
  2. "Windows protected your PC"? Click "More info" > "Run anyway".
  3. First time only: it installs Python (if missing) and a few packages. This
     can take several minutes.
       - Click Yes if Windows asks for permission.
       - If it says Python was just installed, close the black window and
         double-click ~start_gui.bat again.
       - Keep the black window open while you use the bot window.
  4. The "BH Bot Controller" window opens. If it asks for the bot folder,
     pick THE BOT FOLDER.

  Hint: right-click ~start_gui.bat > Send to > Desktop (create shortcut) for
  one-click starts later.

------------------------------------------------------------------------------
  3. LINK YOUR GAME ACCOUNT  (button: "Initial Setup Help")  - once
------------------------------------------------------------------------------
  Click "Initial Setup Help" and do its four steps:
    1. "Open Kongregate in Chrome": log in and wait until Bit Heroes has
       loaded. Keep that tab open.
    2. "Copy the console script", then in that Chrome tab press F12, click the
       "Console" tab, press Ctrl+V and Enter. (If Chrome blocks pasting, type
       allow pasting , press Enter, and paste again.) Three lines starting
       with 1.game.kong... appear.
    3. "Paste from clipboard", choose account number 1, click "Save to
       user-config.properties". Your old file is kept as a .bak copy.
    4. "Create the game files". It should say "created ...index1.html".

  Nothing shows in the Console, or only the word "undefined"?
    - Clear the Filter box in the Console toolbar (an old filter such as
      "top" hides the answer) and make sure "Default levels" / "Info" is on.
    - "undefined" alone on a line AFTER the three lines is normal.
    - Be on the Kongregate Bit Heroes page, logged in.
  A second account: log in to it, run steps 1-4 again, choose account number 2
  (use AFK2.bat for it).

  KEEP YOUR TOKEN PRIVATE. The "1.game.auth.token=" line in
  user-config.properties works like a password. Never post that file or a
  screenshot of it. If the bot later cannot log in, the token expired (it
  changes when you log out of Kongregate): repeat this step.

------------------------------------------------------------------------------
  4. FIRST RUN
------------------------------------------------------------------------------
  1. Script box: BHbWrapper/Fish.bat      Then run box: BHbWrapper/AFK.bat
     Click Start. A small game window opens by itself and logs in.
     Fish.bat fishes 18 times (edit the number in the .bat to change it), then
     AFK.bat runs the full event chain. Between the two the window presses ESC
     a few times to get back to the main map.
     Hint: watch the first fishing round. If it does not start fishing, open
     the fishing screen in the game window yourself.
  2. Safe test: BHbWrapper/AFK.watch.bat only says what it sees and would do;
     it never clicks.
  3. Overrides: the Quest, PVP, World Boss, Raid, Trials, Gauntlet, GVG,
     Expedition and Invasion buttons choose what each event does. A dot on a
     button means its override is on. They only matter when switched on - your
     normal account settings are the default - and they apply while the bot is
     running. (Invasion/Gauntlet can target a specific wave/difficulty number;
     World Boss can wait for other players.)

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
    Look at the game every     how often the bot checks the screen (seconds).

  Hints:
    - Keep the PC awake (Windows sleep stops the bot) and leave the little game
      window open. Playing the same account elsewhere will disconnect it.
    - The bot recognises the game by pictures, so a game update can break
      parts of it until it is updated.

------------------------------------------------------------------------------
  IF SOMETHING GOES WRONG
------------------------------------------------------------------------------
  "Chrome not found"       The Initial Setup Help window asks you to pick
                           chrome.exe. Or add this line to
                           user-config.properties:
                           external.application.chrome.path=C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe
  "Python not found"       Close everything and run ~start_gui.bat again.
  Game window is blank     Close it, then click Start again.
  Bot seems stuck          Screenshots go to  BHbWrapper\out\unknown . When
                           you ask for help, send one and the log text, but
                           hide your name and NEVER include the token.
  Settings seem ignored    Delete BHbWrapper\overrides.json to go back to the
                           defaults.
  Can't tell what's wrong  The black ~start_gui.bat window shows error text.

------------------------------------------------------------------------------
  WHICH FILES THE ADD-ON USES (everything else is the original, legacy bot)
------------------------------------------------------------------------------
  USED:
    ~start_gui.bat                     opens the window (installs Python, etc.)
    BHbWrapper\bh_gui.py               the controller window
    BHbWrapper\bh_setup.py             the "Initial Setup Help" window
    BHbWrapper\bhbot.py                the bot (Python)
    BHbWrapper\templates\              the pictures/colour patterns it knows
    BHbWrapper\*.bat                   Fish, AFK, AFK2, AFK.watch, Esc
    BHbWrapper\overrides.json          made by the override buttons
    BHbWrapper\out\                    screenshots the bot saves
  Created or read in THE BOT FOLDER:
    user-config.properties (+ .bak)    your account (id, name, token)
    readonly.<name>.user-config.properties
                                       optional per-account profile; not needed
    bh-client\                         the game page files
    chrome-user-dir\                   the bot's own Chrome profile (automatic)

  LEGACY (original Java bot; NOT used by this add-on, need Java):
    src, pom.xml, build.*, release.sh, _.bat, bot.sh, web.bot.bat,
    steam.bot.bat, AFK.web.bat, AFK.steam.bat, help.*, setting.*, client.*,
    prepare-mini-chrome-client.txt, sample-scripts, notes, web, .github, and
    the original README.md.
    (client.bat / setting.bat only made files this add-on creates itself or
    that the override buttons replace.)

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
