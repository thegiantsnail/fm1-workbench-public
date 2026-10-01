# FM-1 Workbench

Tools for the **M-VAVE FM-1** pocket FM synth (DX7-compatible, USB MIDI + USB audio).

- **`app/`** — browser app (Chrome/Edge, Web MIDI + SysEx): DX7 voice library (bring your own banks), full voice editor with
  randomize/mutate/morph, multi-voice **drum sequencer** (kits, pads, drum macros, ratchets, swing, p-locks),
  a **MIDI file player** with per-channel voices, and a **Speech** tab (type text, or record/open audio, and the FM-1
  speaks it: one formant voice per 20 ms note, grammar-driven intonation, text lint).
  - **Software FM-1** (`app/fm1-synth.js`): a DX7-compatible engine calibrated on the unit, offered as an output
    ("Software FM-1 (no hardware)") and picked automatically when no FM-1 is connected — every tab works without the
    hardware, in any browser with Web Audio. The Effects panel drives its filter, reverb, delay, distortion, chorus and
    phaser (same CC map on the FX channel as the unit).
  - **Live:** https://fm1-workbench.web.app (Firebase Hosting, Google Cloud project `fm1-workbench`).
    Plug in the FM-1, open it in Chrome/Edge, click Connect and allow MIDI — or just play the Software FM-1.
  - **Local:** `start-workbench.cmd` (Windows) or `./start-workbench.sh` (macOS/Linux) → http://localhost:8731.
  - **Deploy changes:** `python deploy_hosting.py` (uses your `gcloud` login; uploads only changed files; sends
    CSP and other security headers). Project and account come from `.deploy.local.json` (git-ignored:
    `{"project": "...", "account": "..."}`) or `FM1_FIREBASE_PROJECT` / `FM1_GCLOUD_ACCOUNT`.
    Smoke test the live site against the unit: `python test_live_site.py`.
- **`android/`** — native Kotlin app (Compose, USB MIDI): drum sequencer, loopers, editor, MIDI player, library, and
  the same **Speech** tab and **Software FM-1** as the web app (Kotlin ports kept identical by parity tests generated
  from the JS: `android/tools/make_synth_vectors.cjs`, `make_speech_vectors.cjs`). ⚙ menu: output (Auto / FM-1 /
  Software FM-1), firmware (M-VAVE / FM-1+VA) and CC 7 volume. Build/test: `cd android && gradlew testDebugUnitTest assembleDebug`.
- **`vst/`** — **FM-1 Controller**, an instrument plugin (VST3/CLAP, Rust, macOS) that plays and programs the FM-1
  from a DAW: notes with sample-accurate timestamps, all 145 voice parameters and the effects as automatable host
  parameters saved with the project, and an editor with your voice library, the voice editor, randomize/mutate/morph, a
  drum-kit mode that switches voice per hit, and **Speech**: it runs `app/speech.js` unmodified in an embedded
  JavaScript engine, so a typed phrase can be spoken from a button or from a MIDI note at that note's pitch. It talks
  to the unit directly, so the DAW's MIDI routing does not matter.
  Verified against the unit and inside FL Studio; see `vst/README.md`. Build: `cd vst && cargo xtask bundle fm1_vst --release`.
- **`mcp_server/`** — MCP server so Claude can play, program and *listen to* the FM-1 (17 tools).
  Registered in `.mcp.json`; needs `.venv` (`python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`;
  Windows: `.venv\Scripts\pip`). On macOS the paths in `.mcp.json` work as-is; Windows needs `.venv/Scripts/python.exe`.
  The speech tools that use Windows SAPI phoneme timings (`speech/sapi.py`) are Windows-only; `speech/resynth.py` falls back to `say`.
- **`FINDINGS.md`** — what the hardware actually does, measured by recording its USB audio
  (voice switching via parameter diffs, timing, stuck-note and volume gotchas).
- `build_library.py` / `build_kits.py` — build `app/library/index.json` from your patch banks, and the measured drum kits.
- `test_*.py` — hardware measurements and end-to-end tests (Playwright drives the app against the real unit).

## Voices

No DX7 patches ship with this project. Most collections in circulation have no clear redistribution terms, and factory
voices belong to their manufacturers, so the library is something you build from banks you have.

Everything works without one: the editor starts from the init voice, Randomize makes new voices, and the web app
imports any `.syx` file you drop on it. To get the searchable library and the drum kits:

1. Put DX7 `.syx` banks (32-voice banks or single voices, in any subfolders) in `sysexFinal/`, or any folder.
2. `python build_library.py [folder]` writes `app/library/index.json`: deduplicated voices with bank, slot and category
   tags guessed from their names.
3. Optional, with the FM-1 connected: `python build_kits.py` picks drum voices, measures each on the unit and writes
   `app/library/kits.json`.

Both files are git-ignored. The web app ("Load built library"), the Android app, the MCP server and the plugin read them
from `app/library/`.

Places to find patches. Check each source's own terms before using or sharing what you download:

- **[Dexed](https://asb2m10.github.io/dexed/)**, the free DX7 software synth, links a large cartridge compilation
  (`Dexed_cart_1.0.zip`) assembled by a KVR Audio user. Dexed itself is also a good way to audition a bank.
- **[Bobby Blues' DX7 page](https://bobbyblues.recup.ch/yamaha_dx7/dx7_patches.html)**: several hundred banks collected
  from the internet and from 1980s magazine listings, offered as free material, with a standing offer to remove anything
  a rights holder objects to.
- **[This DX7 Cartridge Does Not Exist](https://www.thisdx7cartdoesnotexist.com/)** generates new 32-voice cartridges
  with a machine-learning model, so no existing patch is copied. The site states no licence for its output.
- **Your own**: the DX7 and its relatives send their voices as SysEx, so a bank dumped from an instrument you own, or
  voices you make in the editor here and export, are the cleanest source of all.

Some hardware measurement scripts (`test_diffswap.py`, `test_voice_identity.py`, `test_soft_parity.py`) expect the DX7's
ROM1A bank at `sysexFinal/0_Original_Yamaha/0_DX7/ROM1A.syx` or a built library, and will not run without them.

## Development and releases

FM-1 Workbench is developed in a private repository and published to
[thegiantsnail/fm1-workbench-public](https://github.com/thegiantsnail/fm1-workbench-public) as scrubbed snapshots, one
commit per release, so the public history is short by design. Issues and pull requests are welcome there; accepted
changes are applied in the development repository and appear with the next release.

## Licence

Copyright (C) 2026 thegiantsnail. Released under the [GNU General Public License, version 3](LICENSE).

[NOTICE.md](NOTICE.md) has the notices for the data in `app/speech/` (CMU Pronouncing Dictionary, Brown Corpus
statistics, measurements of a Microsoft voice) and for the libraries the plugin is built with.

This is an independent project, not affiliated with or endorsed by M-VAVE, Yamaha, Image-Line, Microsoft, Steinberg or
Baud Girl. Product names are trademarks of their owners and are used only to say what the software works with.
