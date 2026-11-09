==============================================================================
  BHbWrapper  -  README
  A Windows window ("BH Bot Controller") that plays Bit Heroes for you
==============================================================================
- - Note - - 
I tried to make this as simple as possible - so long as you READ, you shouldn't 
have any trouble getting this to run...(so long as you're on a Windows operating system
and allow python to install [only Python 3.12 has been tested] along with the packages).


  This bot fishes and runs the "AFK chain" (Quest, PVP, World Boss, Raid, Trials,
Gauntlet, GVG, Expedition, Invasion ...). 
  This project started off as an add-on built on top of the free Bit Heroes bot 
by 9-9-9-9 (https://github.com/9-9-9-9/Bit-Heroes-bot), whose files can be found 
there. 
  The original version of this bot required use of those files and has since transformed 
into a standalone bot taking the original concept from the Bit Heroes bot by 9-9-9-9.

YOU'll NEED
  - A Windows PC
  - Internet access
  - Google Chrome browser
  - A Kongregate account that can already play Bit Heroes in your browser:
      https://www.kongregate.com/games/Juppiomenz/bit-heroes

NOTE - This is the browser version ONLY. The Steam version is NOT supported; do try
the original Bit Heroes bot by 9-9-9-9 (https://github.com/9-9-9-9/Bit-Heroes-bot)
if you need steam functionality though additional support is not likely for that.

------------------------------------------------------------------------------
  1. DOWNLOAD / SETUP
------------------------------------------------------------------------------
  1. Open  https://github.com/Skribble-1/BHbWrapper
  2. Click the green "Code" button > "Download ZIP".
  3. Right-click the zip file > "Extract All..." 
  4. Extract to your desired directory such as your Documents folder. 
  5. Do not move the "BHPyBot" folder out of it: it must stay right next
     to  ~start_gui.bat.

Expected folder layout:
  C:\\<Your Directory>\
    BHbWrapper\
      BHPyBot\
      sample_scripts(Legacy)\
      ~start_gui.bat

------------------------------------------------------------------------------
  2. START THE WINDOW  (the first start installs what is needed)
------------------------------------------------------------------------------
  1. Double-click  ~start_gui.bat  in THE BOT FOLDER.
  2. "Windows protected your PC"? Click "More info" > "Run anyway".
  3. First time only: it installs Python (if missing) and a few packages. This
     can take several minutes.
       - Click Yes if Windows asks for permission.
       - If it says Python was just installed, close the command (black) window and
         double-click ~start_gui.bat again.
  4. The "BH Bot Controller" window opens. If it asks for the bot folder,
     select BHbWrapper.

  Hint: right-click ~start_gui.bat > Send to > Desktop (create shortcut) for
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
I recommend trying 'BHPyBot/Fish.bat' in the 'Script' box and in the 'Then run'
    box: BHPyBot/AFK.bat.
    Click Start. A small game window opens by itself and logs in.
    Fish.bat fishes 18 times (at default; edit the number in the .bat to change it), then
    AFK.bat runs the full event chain. Between the two scripts, the ESC button is pressed
    a few times to ensure any stray menus are closed.

Note - For a safe test: BHPyBot/AFK.watch.bat only says in the console what it sees and 
    what it would do; it never clicks. Useful for troubleshooting / testing.

------------------------------------------------------------------------------
- - - - - - - - Buttons / Controls - - - - - - - -
------------------------------------------------------------------------------
Overrides: the Quest, PVP, World Boss, Raid, Trials, Gauntlet, GVG,
     Expedition, and Invasion buttons choose what each event does. A dot on a
     button means its override is on. They only matter when switched on - your
     normal account settings are the default - and they apply while the bot is
     running. When off, your default account settings file is applied.

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
                           you ask for help, send both associated screenshots.
                           Hide your name and NEVER include the token.
  Settings seem ignored    Delete BHbWrapper\overrides.json to go back to the
                           defaults.

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

  LEGACY (original Java bot; NOT used by this add-on, You'll need Java):
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
  (a box around the text "TIER 21"). Run  python bhbot.py selftest  to check
  the templates.

------------------------------------------------------------------------------
  GOOD TO KNOW
------------------------------------------------------------------------------
  Unofficial community tool. Automating a game may be against its rules; use
  it at your own risk. Credit for the original bot and its ideas: 9-9-9-9
  (https://github.com/9-9-9-9/Bit-Heroes-bot).

Did you find this useful? - Let me know - Star it.
