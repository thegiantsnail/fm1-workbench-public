# Voice library

This folder holds two generated files that are **not part of the repository**:

- `index.json`: your DX7 voices, deduplicated and tagged, built by `build_library.py` from the `.syx` banks you put in
  `sysexFinal/` (or any folder you name).
- `kits.json`: drum kits picked from that library and measured on the FM-1 by `build_kits.py`.

The web app, the Android app, both MCP servers and the plugin in `vst/` read them from here. Without them everything
still runs with an empty library: you can import `.syx` files in the web app, or edit voices from the init voice.

See "Voices" in the top-level README for where to find patches and how to build these files.
