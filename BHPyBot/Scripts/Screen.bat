@echo off
:: Watch which screen the bot classifier sees in the running game (no clicks). Ctrl+C to stop.
cd /d "%~dp0.."
python bhbot.py screen --live
pause
