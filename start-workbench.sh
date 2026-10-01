#!/bin/sh
# Serves the FM-1 Workbench on localhost (Web MIDI needs a secure origin) and opens it.
cd "$(dirname "$0")"
(sleep 1; open http://localhost:8731/ 2>/dev/null || xdg-open http://localhost:8731/ 2>/dev/null) &
exec python3 -m http.server 8731 --bind 127.0.0.1 --directory app
