@echo off
rem Serves the FM-1 Workbench on localhost (Web MIDI needs a secure origin) and opens it.
cd /d "%~dp0"
start "" http://localhost:8731/
python -m http.server 8731 --bind 127.0.0.1 --directory app
