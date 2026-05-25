@echo off
cd /d "%~dp0.."
echo Starting TTS Dev Lab - keep this window open.
echo Open http://127.0.0.1:8765/ in your browser after the server starts.
python start_tts_lab.py --open-browser
if errorlevel 1 pause
