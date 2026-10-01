# FM-1 Controller

An instrument plugin (VST3 and CLAP) that plays and programs the **M-VAVE FM-1** hardware synth from a DAW. It makes no sound itself: notes and parameter changes go straight to the FM-1's MIDI port, and the voice is saved in the project and sent again when the project loads.

Status: first working version. Verified against the hardware outside a DAW and inside FL Studio 26.1.5 (see [Verified in FL Studio](#verified-in-fl-studio)).

## What it does

- **Notes** from the host go to the FM-1 with their timing inside the audio block kept (CoreMIDI timestamps). Sustain pedal (CC 64) is forwarded.
- **All 145 DX7 voice parameters** are host parameters (`OP1 Level`, `Algorithm`, ...), so they can be automated and are stored with the project. Only parameters that differ from what the unit holds are sent.
- **Effects**: the FM-1's 24 effect controllers (filter, reverb, delay, distortion, chorus, phaser) are parameters too. They are sent only while **Control Effects** is on; it is off by default, so the plugin leaves the unit's own effect settings alone until you ask.
- **Panic** sends a note-off for every key. **Resend Voice** sends the whole voice again, for when the unit was edited on its own knobs. Each fires once when switched on; switch it off to arm it again.
- **Note Channel** / **Effect Channel** default to 1 and 2, the FM-1's defaults.
- **FM-1+VA Firmware** (off by default): switch it on if the unit runs Baud Girl's FM-1+VA firmware. The voice's LFO speed and delay then also go out as CC 76 and 78, which is the only way they reach that firmware's LFO, and **Volume** (CC 7) becomes a master volume. Stock M-VAVE firmware ignores all three. Volume needs the note and effect channels to differ, because on a shared channel CC 7 is the reverb mix.

It talks to the FM-1 directly and finds it by name (a MIDI output containing `FM-1`), so the host's own MIDI routing and port numbers do not matter. Set `FM1_MIDI_OUT` to a fragment of another output's name to send somewhere else, for example a virtual port when testing.

### Editor window

The editor brings over the FM-1 workbench app's features that make sense inside a DAW. Everything it changes goes through the host's parameters, so edits are automatable, undoable in the host and saved with the project.

- **Library** (left): the voice library you built from your own DX7 banks (see "Voices" in the top-level README; none ship with the project). Search by name or bank, filter by category, click a voice to load it. "Play on load" plays a short note so you hear it.
- **Voice**: the algorithm (with which operators are heard and what modulates what), all six operators side by side, and the voice-wide parameters. Drag a value, or double-click to type.
- **Generate**: Randomize in a style (any, keys, bass, pad, bell, pluck), Mutate by an amount, and Morph between two captured voices. Undo is in the top bar.
- **Effects**: the six effects, and "use distortion as output trim", the only volume control the FM-1 accepts over MIDI.
- **Drum kit**: see below.
- **Speech**: see below.
- Top bar: connection status, voice name, Init, Undo, Play, Resend, Panic.

The library is `app/library/index.json` in the workbench folder, which the plugin looks for in this order:

1. the folder named by the `FM1_WORKBENCH` environment variable;
2. the folder named in `~/Library/Application Support/FM-1 Controller/workbench-path.txt` (one line; `~/` is understood). Use this for a DAW started from the Finder, which never sees shell environment variables;
3. `~/Library/Application Support/FM-1 Controller/library/` itself;
4. `~/fm1-workbench`, then `~/fm1-workbench-main`.

Without a library the editor still works, with a message in place of the list. The workbench's sequencer and MIDI file player are not ported: the host's Piano roll and playlist do those jobs.

### Drum kit mode

With **Kit Mode** on, each key plays its own track's voice instead of the voice parameters: the plugin sends the differences between voices just before each hit, and a ringing hit keeps the sound it started with. Kits come from `app/library/kits.json`, which the workbench's `build_kits.py` makes from your library by measuring drum voices on the unit, with tracks on consecutive keys from MIDI note 36 (C3 in FL Studio). Each track has a key, a pitch, a voice and the workbench's drum macros (level, decay, release, tone, punch, grit). Select a track and click a library voice to replace its sound. The kit is saved with the project. Keys without a track are silent.

**Kit Lookahead** (default 20 ms): the unit needs up to about 14 ms to take in a voice change, and a note sent straight after it waits behind it. So the change is sent when the hit arrives and the note this much later; every hit is late by the same amount, which the plugin reports to the host as latency so it can line the hits up with other tracks. Set it to 0 for the least delay when playing live.

### Speech

Type a phrase in the **Speech** tab and the FM-1 speaks it: one DX7 voice per 20 ms, about fifty notes a second. **Speak** plays it; with **Speech Mode** on, a MIDI note starts the phrase at that note's pitch (keys 36 to 60, the workbench's Pitch range; notes outside it use the nearest), so phrases can be placed and pitched in the Piano roll. A new note restarts it. The tab has the workbench's controls: character (feminine, child, robot, whisper, singer, old radio, ...), intonation, smoothness, diphthongs, voice design, pitch, speed, accent and brightness, and it shows how the text was read (numbers, abbreviations, guessed pronunciations). The phrase and its settings are saved with the project.

**The speech engine is the workbench's own `app/speech.js`, run unmodified** in an embedded JavaScript engine (QuickJS) on its own thread. Nothing is ported, so the plugin speaks exactly as the workbench does and picks up changes to that code the next time it loads. `src/speech_glue.js` mirrors what the workbench's Speech tab does to build a phrase. It needs `speech.js`, `dx7.js` and `speech/{units.json,cmudict.txt,pos.json}` in the workbench's `app/` folder (found as above; no voice library is needed for speech); without them the tab says speech is unavailable and everything else works. The engine starts only when a phrase is first needed, and each phrase is compiled for all 25 pitches (about 20 ms each), the chosen one first.

With Speech Mode on but no phrase compiled (nothing typed yet, or the engine unavailable), notes play the voice as usual and the top bar says so; the plugin never goes mute. Some hosts keep the typing keyboard for themselves, so the tab also has **Paste**, which takes the text from the clipboard.

While it speaks the FM-1 can do nothing else: host notes are ignored, and the phrase overwrites the unit's voice, which the plugin sends again afterwards. The engine schedules the first note about 50 ms after the trigger, to get the first voice into the unit; the Speak button adds 60 ms, as the workbench does. This has not been measured on the unit. Speaking from a recording, which the workbench also offers, is not included.

### FM-1 behaviour it works around

These come from measurements in the FM-1 workbench (`FINDINGS.md`):

| The FM-1... | So the plugin... |
|---|---|
| stalls for up to 450 ms on a full voice dump | sends single-parameter SysEx, differences only |
| hangs a held note if its transpose changes | keeps the device transpose fixed and shifts note numbers; **Transpose** is that shift |
| keeps sounding a note ended by note-on velocity 0 | always ends notes with a real note-off |
| ignores "all notes off" (CC 120/123) | releases its own notes when playback stops or the plugin is removed, and offers Panic |
| cannot report its current voice | assumes nothing: everything is sent when the unit connects or reconnects |
| (FM-1+VA) overwrites the selected stored preset when it receives a voice dump | never sends dumps |
| (FM-1+VA) ignores a CC 76 that carries the LFO speed the voice was just given by SysEx | sends a neighbouring value first, then the real one (found here; see FINDINGS.md) |

### Looking inside a running plugin

Create an empty file named `debug` in `~/Library/Application Support/FM-1 Controller/`. While it exists, every loaded plugin rewrites `status.json` beside it once a second: whether the unit is connected, messages sent and dropped, the modes, the speech engine's state and phrase, and counters showing that the host is calling the audio callback and delivering notes. Delete `debug` to stop.

Two environment variables help outside a host: `FM1_MIDI_OUT` (above) and `FM1_SPEAK="some text"`, which makes the standalone build speak once at start-up through its audio callback.

## Limits

- **macOS only** for now (CoreMIDI). It builds elsewhere but sends nothing.
- **One voice at a time, or one kit**: the FM-1 holds a single edit buffer. Kit mode switches it per hit; playing different voices on different MIDI channels is not implemented.
- **Kit voice changes are not automatable**: a kit is saved state, not host parameters.
- **The FM-1's audio does not come back through the plugin.** Record or monitor its USB audio input in the DAW.
- **Speech is English text only**, and only partly intelligible: the workbench measures roughly 60 of 95 words recognised from the FM-1. The plugin cannot score that; it checks that phrases play as the engine planned.
- **Offline rendering sends nothing**: the hardware cannot play faster than real time.
- **One controller at a time.** The plugin, the workbench app and the MCP servers each keep their own idea of what the unit holds; using two at once will leave one of them wrong. Use Resend Voice after the other has been used.
- Every message is scheduled 3 ms ahead, so the FM-1 plays 3 ms after the host's timeline, plus the unit's own latency.

## Build

Needs Rust and Apple's command line tools; no Xcode.

```sh
cd vst
cargo test                               # 46 unit tests, no hardware; writes target/gui/*.svg
cargo xtask bundle fm1_vst --release     # target/bundled/FM-1 Controller.{vst3,clap,app}
```

Install by copying the bundle into the user plugin folder, then rescan plugins in the host:

```sh
mkdir -p ~/Library/Audio/Plug-Ins/VST3
cp -R "target/bundled/FM-1 Controller.vst3" ~/Library/Audio/Plug-Ins/VST3/
```

## Check against the hardware

With the FM-1 connected and nothing else driving it, from the repository root (needs the packages in `requirements.txt`):

```sh
cargo build --release --examples --manifest-path vst/Cargo.toml
python vst/hw_check.py            # add --va if the unit runs the FM-1+VA firmware
```

It silences the unit, starts the plugin's standalone build, plays eight notes into it through a virtual MIDI port and records the FM-1's USB audio. It passes only if the plugin reprogrammed the voice, every note sounded on time, nothing was left hanging, a held note stopped when the output shut down, every hit of a library drum kit sounded with a voice switch on each, a spoken phrase lasted as long as the engine planned, left nothing sounding and stopped when cut short, and the standalone plugin spoke the phrase from its own audio callback and played its own voice again afterwards. With `--va` it also requires the master volume and the LFO speed to take effect. Last run (2026-10-01, FM-1+VA 093): all fifteen checks passed.

| Check | Result |
|---|---|
| Note spacing, eight notes 250 ms apart | worst error 0.98 ms |
| Kit, a voice switch on every hit | 20 of 20 hits, no MIDI dropped |
| Volume 100 to 32 | 9.9 dB quieter (the workbench measured 9.7) |
| LFO speed 31 and 62 | vibrato at 4.8 and 10.2 Hz (the workbench measured 5.0 and 10.4 for those controller values) |
| "Hello there. Can you hear me?" spoken | 2.22 s of sound for a 2.3 s phrase, no MIDI dropped, silent afterwards |
| The same phrase stopped at 0.7 s | 0.6 s of sound, then silence |
| The same phrase from the standalone plugin's audio callback | 2.12 s of sound; a note afterwards played the plugin's own voice (440 Hz) |

**Speech reaches the unit as the engine planned.** A 6.8-second sentence spoken through the plugin's runner (`examples/speak.rs`, 4,114 messages, none dropped) was compared with the workbench's Software FM-1 rendering of the same phrase: sounding for 6.70 s against 6.72 s, and the loudness contours correlate at 0.77 with no lag. `cargo test` also checks that the embedded engine produces the same events as Node does from the same files, for four phrases across characters, smoothness modes, diphthong modes and voice designs (skipped if Node or the workbench is absent).

**A voice sent in one burst arrives intact.** The plugin sends all 156 parameters without gaps and a note may follow at once. Eight library voices sent that way (`examples/play_voice.rs`) were compared with the same voices loaded slowly, one parameter every 2 ms: spectral similarity 1.000 and level within 0.1 dB for seven of them. The eighth (TAKE OFF) scored 0.97 to 0.98, but two slow loads of it only agree to 0.985 with each other: its sound depends on the free-running LFO.

Kit hit *timing* is not scored. Across four kits, 82 of 82 hits sounded with no MIDI dropped, and with the 20 ms lookahead most repeats of a voice landed within 1 to 2 ms of that voice's first hit; but some voices showed deviations of 5 to 20 ms that appeared with and without lookahead and that I could not attribute to the plugin or to the measurement (drum voices differ too much in attack for one onset detector).

The editor is tested without a window: `cargo test` lays every tab out, checks its controls are present, clicks them (load a library voice, randomize, load a kit, assign a voice to a kit track) and checks what the editor asks the host to do. It also writes each tab as an SVG to `target/gui/`, a rough picture of the layout for checking without a window.

## Verified in FL Studio

2026-10-01, FL Studio 26.1.5 on macOS, plugin on a Channel Rack channel, driven through a scripting bridge into FL (the separate FL-MCP project) while recording the FM-1's USB audio:

| Check | Result |
|---|---|
| FL lists the plugin's parameters | 172 automatable parameters with the expected names and values |
| A note played through FL reaches the FM-1 | −26.1 dBFS at 440.0 Hz; −240 dBFS after note-off |
| `OP1 Level` to 0, then back to 99 | silent (−240 dBFS), then −25.8 dBFS |
| `OP2 Level` raised | 2nd harmonic went from −72 dB to −12 dB relative to the fundamental |
| `Transpose` +12 | 440.0 Hz became 879.9 Hz |
| `Transpose` moved back while a note was held | note still ended; −240 dBFS afterwards |
| `Panic` during a 4-second note | −25.8 dBFS held, −240 dBFS after |

Later the same day, with the editor build, after restarting FL:

| Check | Result |
|---|---|
| A voice clicked in the editor's library reaches the host parameters | All 145 parameters read back from FL equal the library's "BASS 2" (ROM1A #16) |
| A kit loaded in the editor plays per key | Keys 36 to 41 sound, each with its own voice; other keys are silent |
| Each key keeps its voice across repeats, a voice switch on every hit | Same key, two passes: spectral similarity 0.999 to 1.0, peak level within 0.6 dB. Different keys: similarity as low as 0.01 |
| Resend Voice while in kit mode | The next hits sounded correctly after the unit had been reprogrammed behind the plugin's back |

Speech in FL (same day, after one failed attempt): the status file showed the unit connected, the phrase "Speech synthesis is fun and easy" compiled at all 25 pitches (135 notes, 2.8 s) and Speech Mode on, with no MIDI dropped.

| Check | Result |
|---|---|
| A note on the plugin's channel speaks the phrase | 2.71 to 2.74 s of sound for the 2.8 s phrase, at three triggers; silent afterwards |
| A second note one second in restarts it | 3.90 s of sound in total, then silence |
| The plugin reports itself speaking | true mid-phrase, false afterwards |

That the phrase follows the note's pitch is checked at the message level by the unit tests (a key 12 higher gives notes 12 higher). Measured from the FL recordings it was only roughly visible (harmonic spacing 65 Hz at key 45 against 108 Hz at key 57, and 75 Hz at key 40 against 118 Hz at key 52): speech alternates between two keys an octave apart and moves with intonation, so that is not a clean pitch measurement.

The first attempt produced no sound from the plugin at all. The likeliest cause was Speech Mode on with no phrase compiled, which at that point swallowed every note; the fallback, the Paste button and the status file were added in response. Whether typing reaches the text box in FL was not established.

Not yet checked in FL: the FM-1+VA firmware switch and Volume, the sound of a library voice outside kit mode, the voice coming back after saving and reopening a project, notes played from a pattern rather than through the bridge, automation clips, and removing the plugin while a note sounds.

FL did not list the new **Kit Mode** parameter for the plugin instance that was already in the project (it showed the 172 parameters of the previous build), so Kit Mode could not be read or automated from FL there; the editor's own checkbox works. A rescan of plugins, or a fresh instance, may be needed after the parameter list changes; this was not tried.

When a parameter is set through FL's scripting API, the value reads back correctly at once but the display text FL returns is the previous one; it catches up on the next read.

## Licence

GNU General Public License, version 3: see `LICENSE` and `NOTICE.md` at the top of the repository. The VST3 bindings the plugin is built with are GPLv3, so a distributed VST3 build must be too.

## Layout

| File | Role |
|---|---|
| `src/dx7.rs` | DX7 parameter table, init voice, packed-voice unpacking, parameter-change SysEx |
| `src/engine.rs` | What to send: voice and effect differences, note shifting, releases. No I/O |
| `src/midi_out.rs` | Lock-free queue from the audio thread to a CoreMIDI sender thread |
| `src/params.rs` | Host parameters and saved state. Parameter ids are part of saved projects |
| `src/algo.rs` | The 32 algorithms: modulation routes, carriers, feedback operator |
| `src/library.rs` | Finds the workbench folder; loads the locally built `index.json` and `kits.json`; search |
| `src/voicegen.rs` | Randomize, mutate, morph (ported from the workbench's `app.js`) |
| `src/kit.rs` | Drum kits, drum macros, the per-key table the audio thread plays |
| `src/speech.rs` | Speech: the embedded engine and its thread, phrases, and the audio thread's runner |
| `src/speech_glue.js` | Builds a phrase with the workbench's `speech.js`, as its Speech tab does |
| `src/status.rs` | The opt-in status file |
| `src/editor.rs` | The editor window, and its headless tests |
| `src/lib.rs` | The plugin |
| `examples/hold_and_drop.rs` | Hardware check for the shutdown path |
| `examples/kit_play.rs` | Hardware check for kit playback |
| `examples/va_check.rs` | Hardware check for the FM-1+VA controllers |
| `examples/play_voice.rs` | Sends a library voice in one burst and plays it |
| `examples/speak.rs` | Speaks a phrase on the unit through the plugin's runner |
| `hw_check.py` | Runs the hardware checks and scores the recordings |
