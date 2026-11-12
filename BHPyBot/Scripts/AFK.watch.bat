@echo off
:: Look-only mode: says what it sees and would do, never clicks. Safe first test.
cd /d "%~dp0.."
python bhbot.py afk a --watch
