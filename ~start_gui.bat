@echo off
:: Launches the BH Bot Controller (BHPyBot\bh_gui.py).
:: First it makes sure everything needed is installed:
::   1. Python 3 (installed with winget if missing)
::   2. the Python packages: opencv-python, numpy, psutil (installed with pip if missing)
::   3. tkinter (comes with Python from python.org) and Google Chrome (only a warning if Chrome is missing)
setlocal EnableExtensions
cd /d "%~dp0BHPyBot" || (echo Cannot find the BHPyBot folder next to this file. & pause & exit /b 1)

call :findpython
if defined PY goto :havepython

echo Python 3 was not found on this PC.
where winget >nul 2>nul
if errorlevel 1 (
    echo winget is not available here, so Python cannot be installed automatically.
    echo Install Python 3 from https://www.python.org/downloads/ ^(tick "Add python.exe to PATH"^), then run this file again.
    pause
    exit /b 1
)
echo Installing Python 3.12 with winget ...
winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements
:: a freshly installed Python is not on this window's PATH yet: reload PATH from the registry, then look again
for /f "usebackq delims=" %%P in (`powershell -NoProfile -Command "[Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [Environment]::GetEnvironmentVariable('Path','User')"`) do set "PATH=%%P;%PATH%"
call :findpython
if not defined PY call :trypath "%LOCALAPPDATA%\Programs\Python\Python312"
if not defined PY call :trypath "%ProgramFiles%\Python312"
if not defined PY (
    echo.
    echo Python was installed but this window cannot see it yet. Close this window and run ~start_gui.bat again.
    pause
    exit /b 1
)

:havepython
echo Using Python: %PY%
%PY% -m pip --version >nul 2>nul || %PY% -m ensurepip --upgrade >nul 2>nul

set "FAILED="
call :need cv2 opencv-python
call :need numpy numpy
call :need psutil psutil
if defined FAILED (
    echo.
    echo Some packages could not be installed: %FAILED%
    echo Check your internet connection, then run ~start_gui.bat again.
    pause
    exit /b 1
)

%PY% -c "import tkinter" >nul 2>nul
if errorlevel 1 (
    echo.
    echo This Python has no tkinter ^(needed for the window^). Re-install Python from python.org with "tcl/tk and IDLE" ticked,
    echo or run:  winget install -e --id Python.Python.3.12
    pause
    exit /b 1
)

:: the bot needs Chrome for the game window (warning only)
set "CHROME="
for %%C in ("%ProgramFiles%\Google\Chrome\Application\chrome.exe" "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe" "%LocalAppData%\Google\Chrome\Application\chrome.exe") do if exist %%C set "CHROME=%%~C"
if not defined CHROME (
    reg query "HKCU\Software\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe" >nul 2>nul && set "CHROME=registry"
    reg query "HKLM\Software\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe" >nul 2>nul && set "CHROME=registry"
)
if not defined CHROME (
    echo Warning: Google Chrome was not found. The bot needs it to show the game.
    echo Install it with:  winget install -e --id Google.Chrome
    echo ^(or set external.application.chrome.path in user-config.properties^)
    timeout /t 6 >nul
)

start "" %PYW% bh_gui.py
exit /b 0


:: ---------------------------------------------------------------- helpers
:findpython
set "PY=" & set "PYW="
:: the py launcher first, then python (the Microsoft Store stub fails the test below, so it is skipped)
py -3 -c "import sys; sys.exit(0 if sys.version_info[0]==3 else 1)" >nul 2>nul && (set "PY=py -3" & set "PYW=pyw -3" & exit /b 0)
python -c "import sys; sys.exit(0 if sys.version_info[0]==3 else 1)" >nul 2>nul && (set "PY=python" & set "PYW=pythonw" & exit /b 0)
exit /b 0

:trypath
:: %~1 = a folder that may contain python.exe
if exist "%~1\python.exe" (
    set PY="%~1\python.exe"
    if exist "%~1\pythonw.exe" (set PYW="%~1\pythonw.exe") else (set PYW="%~1\python.exe")
)
exit /b 0

:need
:: %1 = module to import, %2 = pip package that provides it
%PY% -c "import %1" >nul 2>nul && exit /b 0
echo Installing %2 ...
%PY% -m pip install --disable-pip-version-check %2
%PY% -c "import %1" >nul 2>nul || set "FAILED=%FAILED% %2"
exit /b 0
