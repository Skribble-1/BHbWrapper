@echo off
:: AFK chain for your first account (uses its settings profile, if you made one). For a second account use AFK2.bat.
:: Add --ear to exit when everything is waiting for tickets.
cd /d "%~dp0"
python bhbot.py afk a
