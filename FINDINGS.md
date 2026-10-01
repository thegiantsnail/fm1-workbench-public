# M-VAVE FM-1 — verified MIDI behaviour (2026-09-27)

USB composite `VID_4C4A PID_C755`: class-compliant audio in/out + MIDI. Ports: MIDI in `FM-1 Audio 0`, out `FM-1 Audio 1`; audio capture `Microphone (FM-1 Audio)` (MME idx 1, 44.1 kHz).

## FM-1 → PC (listen.py)
- Notes only, channel 1, **fixed velocity 90** (keys, arp and sequencer all transmit).
- No CCs from knobs, no program change, no clock, no SysEx.
- Ignores Universal Identity request and DX7 dump requests (all channels) → **no patch readback**.

## PC → FM-1 (measured by recording the USB audio)
| Message | Result |
|---|---|
| Note velocity | Works; depth depends on patch KVS (sine w/ KVS 7: 37 dB range) |
| DX7 VCED single voice (163 B) | Loads, **but stalls the unit 20–450 ms at random; notes after it are delayed/dropped** (test_busy.py) |
| DX7 param change `F0 43 1n gg pp dd F7` | **0 ms added latency**, applies to the next note; 5/5 reliable even with no gap |
| Program change (Midi Chn) | Works, PC 0 = preset 1; unit busy **~45 ms** (notes queue, not lost) |
| CC64 sustain (Midi Chn) | Works. **CC121 reset does NOT clear it** — send CC64=0 explicitly |
| CC120 / CC123 (all sound/notes off) | **Ignored.** Only explicit note-offs release voices (stuck voices seen) |
| CC 0–23 on Effect Chn (default ch 2) | Filter cutoff/type, reverb mix, delay mix verified |

Effect CC map (Effect Chn): 0-3 Filter on/type(0 LP,1 BP,2 HP)/cutoff(0-107)/Q(0-10) · 4-7 Reverb on/type/decay/mix ·
8-11 Delay on/decay/rate/mix · 12-15 Dist on/gain/tone/level · 16-19 Chorus on/freq/depth/mix · 20-23 Phaser on/freq/depth/mix.
Source: https://m-vave-fm1-midi-guide.up.railway.app/

## Stuck notes: transpose (2026-09-29)
- **The FM-1 matches a note-off using the voice transpose (TRNP) in effect at note-OFF time.** Change TRNP while a
  note is held and that note hangs forever (measured: -42 dB 400 ms after note-off vs -99.6 dB normally).
- 49% of the library (17,156 voices, mostly -12) and 25 of 47 kit voices use a transpose, so voice switching with
  ringing notes (kits, player, looper, Transpose p-lock) could hang notes. All engines (web, Android, MCP) now keep
  the device TRNP at 24 and shift note numbers instead; verified on hardware (-99.7 dB after note-off).
- Separately (Baud Girl): a note ended with note-on velocity 0 keeps sounding - our engines always send 0x80.

## Speech resynthesis (2026-09-29, speech/resynth.py --hw)
- TTS -> 20 ms frames, one note + param diff per frame (median 5 params, max 10), alternating keys, TRNP fixed: plays
  cleanly at 50 notes/s, no stuck notes, loudness envelope matches the software mock. Whisper word accuracy on 6
  phrases: FM-1 8/21 vs mock 11/21 vs TTS 20/21; results vary between runs.

## Web speech + Software FM-1 (2026-09-29)
- `app/fm1-synth.js` (Software FM-1, AudioWorklet + Node) is calibrated on the unit: release rate 72 = -38 dB at
  +50 ms (FM-1 ~-40), sideband at modulator OL 58 = -14.6 dB (FM-1 -14..-15). TTS resynthesis through it scores the
  same as the Python mock (11/21), so it stands in for the hardware when developing.
- Typed text: diphone units cut from Windows SAPI speech (`speech/build_units.py`). **SAPI's PhonemeReached
  AudioPosition drifts against the audio (up to ~250 ms late, not a constant scale)**; phone order, pauses and relative
  durations are fine, so phones are aligned to energy islands instead. That took Whisper from 8/46 to 11/46 words
  (12 phrases, software FM-1); smoothing formants across unit joins -> 13/46. Grammar intonation 8 vs flat 6 before
  the alignment fix (within noise). Hardware scoring of the web path: `speech/eval_web.py --hw` (not yet run).

## Speech: soft joins (2026-10-01, `speech/softstart.py`, `test_eg_softstart.py`, recorded on the FM-1)
- **Operator attacks on the FM-1+VA follow the DX7 rates**, and the software model matches (rise to full: R1 80 ~5 ms, 75 ~9 ms,
  70 ~18 ms, 50 ~150 ms, 35 ~700 ms).
  - A **partial-depth start** works too: R1 99 -> L1 86, then R2 65 -> L2 99 jumps to about -7 dB and eases to full over ~10 ms.
  - Probes through `test_soft_parity.hardware()` recorded over a still-sounding previous note: -14 dB before the onset, then a
    6 dB "jump" when it released. That made attacks look instant. `test_eg_softstart.py` uses one MIDI session and checks for
    silence before each note.
- **Parameter changes never reach a sounding note on FM-1+VA** either (level, ratio, fixed frequency: `test_live_params.py`).
- Metrics: 50 Hz line and clicks (the 50 Hz line of the >2.5 kHz envelope) in dB; loudness of the loud stretches against the
  default; words on the 24 phrases.

| FM-1 | 50 Hz line | clicks >2.5 kHz | loudness | words /95 |
|---|---|---|---|---|
| previous default (phase-locked) | 6.1-7.6 | 5.9-6.9 | 0 | 61, 59 |
| partial starts -4/-6/-9 dB (+ short overlap) | 5.1-5.4 | 5.5-6.9 | +0.2-0.5 | - |
| release/overlap only | 4.8 | 5.7 | +0.5 | - |
| crossfade every join (attack 80, overlap 20, release 75) | 2.6-3.6 | 4.4-4.5 | +0.3-0.6 | 39, 60 |
| **soft joins: crossfade only voiced -> voiced** (default) | **2.7-3.4** | **3.6-4.4** | **+0.3-0.6** | **59, 58** |

- The overlapping notes are phase-aligned (sync), so they add up and nothing gets quieter.
- Crossfading consonant onsets smears them (one run 39/95).
- The partial-depth starts barely help: the gravel comes from the abrupt handover between notes, which a long, matched overlap removes.
- UI: Smoothness = Soft joins (default) / Phase-locked / Smooth / Raw.

## Speech: diphthongs as one gliding note (2026-10-01, `speech/diphthongs.py`, `test_peg_sweep.py`, recorded on the FM-1)
- **Staggered operator onsets alone do not glide.** One voice where the start vowel's formant carriers fade and the end vowel's
  carriers start late (or two overlapping notes doing the same) swaps two fixed tones: the F2 track jumps (1060 -> 1510 Hz in "bite").
- **The FM-1's pitch EG moves ratio-mode operators but NOT fixed-frequency ones** (fixed op held -0.14 st while PEG 40 -> 60 moved a
  ratio op +5 st). So `diph: 'sweep'` (algorithm 22) uses:
  - op4 = F2 in ratio mode, swept by the pitch EG in two stages (slow drift for 35 % of the vowel, the main glide by 80 %);
  - op6/op2 fixed at f0, so the pitch holds; FF steps make f0 accurate to 2.3 %;
  - op5 = F3 fixed;
  - F1 by staggered onset: op3 (start) fades and op1 (end) starts late. When F1 holds, op1 is the body again.
  - Example: "toy" on the FM-1 glides F2 940 -> 1870 Hz in one note (TTS 830 -> 1890).
- **Slow pitch-EG sweeps on the FM-1 are 1.37x slower than the DX7 figure** (90 % of a 20-step glide: PR1 40 437 ms, 60 202, 80 98;
  software was 315/157/70). The software FM-1 now uses 29.2 instead of 21.3 (JS and Kotlin).
- Operator EG timing for the fades and late onsets is in `speech/eg_table.cjs` (software): a rise from 0 at rate 50 stays quiet,
  then reaches -6 dB at 104 ms.

| FM-1 | diphthongs (closed set) | 50 Hz line, diphthong phrases | 24 phrases, words /95 (3 runs) |
|---|---|---|---|
| note chain | 3/13 | 7.4 dB | 68, 56, 58 (avg 60.7) |
| **one-note glide** (default) | **5/13** | 6.3 dB | 63, 58, 65 (avg 62) |
| crossfade only (single voice) | 3/13 | 8.0 dB | 65 |
| every vowel one note | 3/13 | 5.6 dB | 49 |

The FM-1 phrase score varies by about ±6 words between runs (free-running LFO, Whisper). UI: Diphthongs = One-note glide /
All vowels / Note chain.

## Speech: the "gravel" (2026-10-01, `speech/smoothness.py`, `test_pitch_eg.py`, `test_mono_glide.py`, recorded on the FM-1)
Speech is one FM-1 note per 20 ms frame, so every frame restarts the operators at a random phase. That gives a 50 Hz click train,
heard as gravel. The metric is the 50 Hz frame-line level (dB above its neighbours in the envelope spectrum), 24 phrases, Whisper words /95:

| variant | 50 Hz line | words |
|---|---|---|
| TTS reference | 5.6 | - |
| old chain | 11.5 | 62 |
| **sync** (each voiced onset a whole number of pitch periods after the last; default) | **7.2** | **64** |
| sync + crossfade (attack 80, overlap 20 ms, release 75) | 3.3 | 55 |
| "smooth" = sync + crossfade + continuous pitch via pitch EG (fine) + glide | 2.9 | 61 |

- True portamento is not available over MIDI. FM-1+VA ignores the DX7 function parameters (mono 64, portamento 67/69) and CC 5/65/126/127.
  Mono and Glide exist only on the unit's own screens (preset Mono row, GLOBE→Glide). That route is untested: a legato voice would also
  have to accept per-frame parameter changes.
- The per-note pitch EG is the substitute for portamento. Levels are linear around 50 (1/32 octave per step, 27-85 ≈ ±1.4 oct).
  PR1 99 reaches the target in about 30 ms (soft about 25 ms); PR1 85 takes about 55 ms on the FM-1 vs 35 ms in soft.
  `fine` keys the note to the nearest semitone and puts the remainder in the PEG. `glide` starts each note at the previous note's pitch.
- UI: Smoothness = Phase-locked (sync, default) / Smooth / Raw, on the web and Android.

## Speech characters (2026-09-30, `speech/characters.py`, recorded on the FM-1)
18 presets (`Speech.CHARACTERS`, web + Android): pitch/formant shifts, whisper (every frame as aspiration noise on
its formants), robots (flat pitch, harmonic design, 30-40 ms frames, optional chopped notes), cyborg (op6 modulator
off the harmonic grid, ratio 1.41: bell-like), alien, singer (each word on the next melody note, long vowels,
vibrato), the FM-1's own effects (radio = band-pass + distortion, cathedral, chorus, echo; switched off again after),
and intonation styles (uptalk, sing-song, drawl, excited). Regional vowel accents are not modelled.
Whisper words /18 on the FM-1, "Hello there. I am the FM-1, and I can speak in many voices. Can you hear me?":
alien 18, drawl 16, natural/cyborg/excited 15, feminine/singer 14, giant/robot/uptalk/sing-song 13, child/radio/
cathedral 11, whisper 10 (software 4), glitch robot 6, chorus 4, echo 1 (the unit's echoes make Whisper loop).
Reel: dumps/characters/fm1_character_reel.wav.

## Speech: consonants, noise bands, voicing and unit selection (2026-09-30, `speech/consonants.py`)
- **Metallic fricatives, cause:** the FM-1's feedback is 1/4 of the DX7's, so the old fricative voice (feedback op
  modulating harmonic carriers) was a comb on the key's harmonics, not noise: fricative "tonality" (peak normalised
  autocorrelation) 0.45 vs 0.32 in the TTS; "s" centred at 3.3 kHz vs 5.0 kHz (the design clipped it at 3.4 kHz).
- **Noise bands** (FS1R-style voiced/unvoiced split), chosen per frame from the high-band share (energy above
  4 kHz, a new unit field; s 0 dB, sh -9, vowels -30, nasals -46):
  hiss band (fixed carriers across the band, low inharmonic modulators - wide cascades spread energy down to 500 Hz
  where a real "s" is 38 dB down), aspiration for h and post-stop breath (noise on the frame's own F1-F3), voiced
  fricative (voice bar + hiss), and a percussive envelope for a stop release after silence.
  Software: tonality 0.31-0.33 (TTS 0.33), flatness 0.13-0.15 (0.12), "s" at 4.5 kHz.
- **Voicing detection was wrong for this low voice:** the autocorrelation was normalised by the whole window, which
  under-reads long lags, so only ~42 % of vowel frames counted as voiced and the band mode turned vowels into hiss.
  Normalising by both overlapping parts (threshold 0.4) -> vowels 81-87 % voiced, s/sh 13 %; plus a planner rule that
  loud frames with a vowel-like spectrum are voiced. Fixed in all three analysers (Python units, JS, Kotlin).
- **Unit selection:** a larger corpus (284 lines), up to 4 candidates per diphone, Viterbi over join cost (formant /
  energy / voicing jumps at seams) + length target. Helps with noise bands on the software FM-1, neutral on the unit.

| 24 phrases, Whisper words /95 | software FM-1 | FM-1 |
|---|---|---|
| before this work (fixedfm) | 34-35 | 26, 29 |
| voicing fix, old fricatives, no selection | 65 | 52 |
| voicing fix, old fricatives, selection | 57 | 57 |
| voicing fix, noise bands, no selection | 52-54 | 66 |
| **voicing fix, noise bands, selection (default)** | **67** | **65** |

- Isolated single words (closed-set rhyme lists, Whisper prompted with the list) stay hard: 3-6/33 vs 32/33 for the
  TTS; stops 0/12 - bursts shorter than a 20 ms frame are the next target.

## Speech: voice designs from the FM/formant literature (2026-09-30, `speech/eval_designs.py`, 24 phrases)
Sweep of related work and what it suggested:
- Chowning's FM voice (carrier on the harmonic nearest the formant, modulator at f0) is what `harmonic` did. Chafe,
  *Glitch Free FM Vocal Synthesis* (CCRMA 2013): rounding to harmonics makes formants jump/click on pitch or phoneme
  changes; Le Brun's fix cross-fades the two bracketing harmonics (`lebrun`, DX7 algorithm 24).
- Sine-wave speech (Remez & Rubin; also C64 SAM): three tones at the *exact* formant frequencies are intelligible
  (`sine`, fixed-frequency operators, algorithm 32).
- Combination (`fixedfm`): fixed-frequency carriers at the exact F1-F3, modulated by op6 at f0 so the sidebands sit
  at F +- k*f0. FM-1 fixed-frequency operators are exact (302/1514/2512 Hz within 0.3 Hz at keys 45/57/69).
- Yamaha FS1R formant sequences (fs1r-wav2syx): 4-6 bands, ~28 ms frames, smoothed tracks -> tried 10/30 ms frames.

| design | software FM-1 (Whisper words /95) | FM-1 (2 runs) |
|---|---|---|
| harmonic (previous default) | 28, 31 | 24, 24 |
| lebrun | 31 | - |
| sine | 30 | - |
| **fixedfm (new default)** | **35, 34, 34** | **26, 29** |
| fixedfm + LFO vibrato 12 | 33 | - |
| fixedfm bright 46 / 62 | 31 / 31 | - |
| fixedfm 10 ms / 30 ms frames | 29 / 20 | - |

Exact formant frequencies help most (+15-20 % words, both engines); vibrato, brightness and frame-rate changes don't.
The planner and the Android port default to `fixedfm`; the Speech tabs offer all four as "Voice".

## Intonation from grammar (2026-09-29, `speech/eval_prosody.py`, software FM-1, no hardware)
25 sentences (statements, yes/no and wh-questions, exclamations, comma phrases, "It's raining." / "It's raining?").
Part-of-speech tagger: HMM from the Brown corpus (`speech/build_pos.py`), 94.4 % on held-out Brown text.

| intonation | ends the right way (plan) | (keys sent) | r vs TTS f0 | Whisper "?" right | words |
|---|---|---|---|---|---|
| flat | 0 % | 0 % | 0.00 | 68 % | 39/117 |
| function-word list | 92 % | 80 % | 0.21 | 72 % | 44/117 |
| part of speech + nuclear accent | **100 %** | **96 %** | **0.24** | **80 %** | 38/117 |

- The nuclear tune must turn *on* the nuclear vowel (a ramp), or one-syllable final words ("mat", "now", "show") never
  fall/rise: that took the POS model from 84 % to 100 %.
- Whisper's question mark is the only perceptual measure here; it improved. Word accuracy moved within Whisper's
  run-to-run noise (about ±4 words) - but it is not better, so intonation is not yet buying intelligibility.
- TTS contours are noisy references (the f0 tracker slips octaves on creaky endings): correlations stay low for all.

## FM-1 Controller plugin (2026-10-01, `vst/`, FM-1+VA 093, macOS)
Measured with `vst/hw_check.py` and the examples in `vst/examples/`, recording the USB audio.

- **FM-1+VA ignores a CC 76 that repeats the LFO speed the voice was just given by SysEx.** After param 137 = 31
  arrives, CC 76 = 40 (31 scaled to 0-127) changes nothing: the LFO keeps its previous rate. CC 76 = 41 is applied, and
  so is 39 followed by 40. CC 76 first and the SysEx afterwards also works. The same stream replayed through mido
  behaves the same, so it is the unit, not the sender.
  - The plugin therefore sends a neighbouring value and then the real one (`Engine::sync_lfo`).
  - **`app/engine.js` and the Kotlin port send param 137 and then CC 76 with exactly that scaled value**, so on FM-1+VA
    their LFO speed probably does not follow a voice switch. Not changed here: it needs the browser and the parity
    vectors. CC 78 (delay) was not tested separately; the plugin treats it the same way.
- **A whole voice in one burst is safe.** 156 parameter changes with no gaps and a note-on straight after: eight
  library voices matched the same voices loaded at 2 ms per parameter (spectral similarity 1.000, level within 0.1 dB;
  TAKE OFF 0.97, no worse than two slow loads of it against each other).
- **Timing from CoreMIDI timestamps**: eight notes 250 ms apart came out within 1 ms of each other (64-sample blocks).
- **Per-hit voice switching without lookahead** made some hits late by the time the differences take on the wire
  (FINDINGS above: about 14 ms for ~89 parameters). The plugin sends the differences when the hit arrives and the note
  20 ms later (adjustable), and reports that to the host as latency. 82 of 82 hits sounded across four kits; hit
  timing could not be scored reliably (drum voices differ too much in attack for one onset detector).
- Kit 0's open hat (carrier R4 27-31) rings for about ten seconds after its note-off: -51 dBFS in the first second,
  silent after eleven. It is a slow release, not a hanging note.
- **Speech through the plugin** (`app/speech.js` run unmodified in QuickJS; events identical to Node's for four test
  phrases): a 6.8 s sentence, 4,114 messages scheduled with CoreMIDI timestamps, none dropped; the unit sounded for
  6.70 s against 6.72 s in the Software FM-1 render of the same events, loudness contours correlating at 0.77, lag 0.
- CC 7 volume 100 -> 32: 9.9 dB (9.7 above). CC 76 = 40 / 80 / 127: 4.8 / 10.2 / 50.5 Hz (5.0 / 10.4 / 50.7 above).

### The plugin's built-in synth against the unit (2026-10-01, `vst/synth_check.py --va`)
The plugin has a Rust port of the Software FM-1 (`vst/src/synth.rs`); its samples equal `app/fm1-synth.js` in Node to
2e-8 for 14 test renders. Comparing that port with the unit's USB audio turned up three things about the unit:

- **The unit's output is a first-order high-pass at 20 Hz.** Init voice (a sine) on low notes, relative to note 60:
  -0.4 dB at 65 Hz, -0.7 at 46, -1.4 at 33, -2.4 at 23, -3.9 at 16.4, -6.0 at 11.6, -8.4 at 8.2. Every point fits
  -10 log10(1 + (20/f)^2) within 0.1 dB.
- **The Software FM-1 carries a wandering offset the unit does not.** A feedback operator modulating a carrier at the
  same pitch, a few detune steps apart, makes the wave lopsided by an amount that swells and fades at their beat rate
  (well under 1 Hz to a few Hz). SYN-LEAD 1 (ROM1A 14): offset -5 dB relative to the signal; FLUTE 1 -12 dB; BRASS 1
  -20 dB. The unit's 20 Hz high-pass removes it; with the same filter the port's is below -36 dB.
  `app/fm1-synth.js` and the Kotlin port have no such filter. Not changed here: it needs the parity vectors.
- **Level.** At its default volume (0.8) the Software FM-1 is 4.9 dB louder than the unit's USB audio with the
  unit's master volume (CC 7) at 127, over eight ROM1A voices at note 60 (4.3 to 5.5 dB). The plugin's synth uses
  0.456 instead and then sits within -1.4 to +0.8 dB of the unit over three runs (median -0.2), spectra alike to 0.93-1.00
  (sixth-octave bands, 60 Hz to 15 kHz; E.PIANO 1 and BRASS 1 lowest, as in the 12-voice comparison below).
- **The Software FM-1's sustain pedal does not hold.** `noteOff` under the pedal clears `down`, and the envelope code
  treats a voice that is not `down` as released, so the note fades while its voice lingers. The port holds the
  envelopes until the pedal lifts. The unit's own pedal behaviour was not measured.

## Baud Girl's FM-1+VA firmware (FM-1_093, installed 2026-09-30; `test_firmware_quirks.py`)
Installed from https://baudgirl.com/work/FM-1+VA/install (Web MIDI in Chrome). Rollback: M-VAVE V15 `FM-1.fwsc`
(699,956 bytes, sha256 db1642b2...edb8a) kept in the git-ignored `firmware/`, installed via "Install a file".

| | M-VAVE V15 | FM-1+VA 093 |
|---|---|---|
| DX7 param change applies to the next note | yes | yes (Workbench voice switching works unchanged) |
| detune beat at A4, DT 0 / DT 14 vs DT 7 | 4.8 / 5.2 Hz (~2.8 cents/step) | 1.6 / 1.6 Hz (~0.9 cents/step, symmetric) |
| patch LFO speed (DX7 param 137, by param change) | ignored, fixed ~5.8-6 Hz | still ignored (4.0 Hz) - **but CC 76 sets it** |
| CC 76 LFO speed, MIDI ch | - | 0 -> 0.4 Hz, 40 -> 5.0, 80 -> 10.4, 110 -> 33.7, 127 -> 50.7 Hz |
| CC 7 volume, MIDI ch (needs MIDI ch != FX ch) | ignored | 100 -2.3 dB, 64 -6.5, 32 -12, 0 silent |
| note-on velocity 0 | releases | releases |
| TRNP changed while a note is held | note hangs | note still hangs (engines keep TRNP 24) |
| single-voice VCED dump | loads the edit buffer | **overwrites the selected stored preset, no prompt** |

- FM-1+VA adds a fixed CC map on the MIDI channel (manual, "Controlling the FM-1 From a Controller"): 74 brightness,
  71 feedback, 73/75/70/72 envelope, 76/77/78 LFO speed/pitch depth/delay, 58 algorithm; changes apply to sounding
  notes and are kept only on SAVE. Presets can be backed up/restored on its Presets page (stock cannot dump them).
- The Software FM-1 (fitted on stock) matches FM-1+VA even closer: 12 ROM voices spectrum within 2.7 dB (stock 4.8),
  loudness contour 2.1 dB (3.2). Speech: FM-1+VA 11/46 words (stock 13-14, within Whisper's spread).
- Workbench: header "Firmware" selector. FM-1+VA mode sends CC 76/78 with every voice switch (LFO speed/delay) and
  enables the Volume control (CC 7); the Software FM-1 switches to the matching profile (detune, LFO, CC 7).
- Probe scripts now load voices by param changes only (`test_soft_parity.py`), never dumps.

## Engine details measured against the Software FM-1 (2026-09-30, `test_soft_probe.py`, `test_soft_parity.py`)
Each mechanism probed on its own (sustained A4, effects off), the software model then fitted:
- **Modulation depth**: DX7-exact (4π at full level); first sideband within 0.4 dB from OL 40 to 99.
- **Output treble roll-off**: -1 dB @3.5 kHz, -2.8 @5.3k, -4.7 @7-10.5k, -8 @13.6k (a one-pole 5.6 kHz low-pass
  fits within 0.5 dB). Ratio-14 sidebands read ~2 dB low for this reason, not because of the index.
- **Feedback** is 1/4 of the DX7-emulation strength: H2 at FB1 -38.9 dB (msfa-style formula gave -26). Scales with
  output level and envelope level exactly like the DX7 (checked at OL/L 99-74).
- **Velocity** never boosts above the stored level: same curve shape as the DX7 table (38 dB from vel 20 to 127 at
  KVS 7) but ~0.87 dB lower per KVS step at every velocity.
- **LFO: fixed ~5.8 Hz, near-sine, free-running - the patch's LFO speed, waveform and delay are ignored** (same result
  for speeds 0-99 by dump or param change, all six waveforms, delay 0-99). Depths follow the patch: vibrato = DX7
  sensitivity table x0.88 (±41/137/383/1069 cents at PMS 1/3/5/7, LPMD 99); tremolo 2.1 / 5.3 dB p-p at AMS 1 / 2,
  and **AMS 3 silences the operator at every trough**, even at LAMD 50.
- Algorithm 22 routing, attack rates (R1 20-80) and keyboard rate scaling (RS 0/4/7, notes 48-84) match the DX7.
- Result on 12 ROM1 voices: loudness contour within 3.2 dB, 1/3-octave spectrum within 4.8 dB, relative loudness
  within 0.6 dB (E.PIANO 1 spectrum 9.7 -> 1.2 dB after the velocity fix). Still off: BRASS 1 (~11 dB, its feedback
  modulator sounds ~4.5 dB stronger on the unit; not explained by any probe) and STEEL DRUM, whose AMS 3 tremolo
  makes every take differ with the free-running LFO phase.
- Speech through the identical MIDI: software 15/46 words, FM-1 14/46.

## The unit's effects colour everything
- With the unit's effects as the user had them, a pure 110 Hz sine came out with a 2nd harmonic as loud as the
  fundamental plus peaks at 1.3/2.0/3.0 kHz; switching filter/reverb/delay/distortion/chorus/phaser off (CCs on the FX
  channel) gave a clean sine but 30+ dB quieter - the distortion effect was adding most of the level (see the
  "blasting in FL" issue).

## Voice switching (basis of the drum sequencer / multi-voice player)
- Send only the params that differ between voices (~89 msgs / 14 ms for very different ROM voices).
  Alternating 4 ROM voices at 62–250 ms steps: 24/24 notes on time (test_diffswap.py); each hit sounds like a
  clean reference of its voice, 16/16 (test_voice_identity.py).
- **Sounding notes keep their voice** when the edit buffer changes → overlapping tails from different voices.
- Diffs can therefore be sent right after the previous note-on, ahead of the next note.
- Browser timing (test_e2e_sine.py, sine voices, every note a voice switch): our own just-in-time queue on a 2 ms
  worker tick: onset error median ~1 ms, p95 ~3.5 ms. Web MIDI future timestamps: median 2–4 ms, max ~9 ms.

## Output level (2026-09-28, test_level_controls.py)
- No MIDI master volume: CC7, CC11 and Universal SysEx Master Volume do nothing.
- Distortion on + gain 0 + Level (CC15, FX ch) is a usable output trim (level 20 ≈ −14 dB). Carrier OL and velocity work.
- USB capture has no hardware gain (Windows endpoint range 0..0 dB). USB playback to "Speakers (FM-1 Audio)" loops back
  into the USB capture (~−54 dB at low volume).
- Raw USB level is low (full sine ≈ −58…−61 dBFS peak −54) — loudness in FL comes from FL/Windows gain, not the unit.
  FL Studio ASIO here uses the Windows default devices: input FM-1, output the system's default output.
- MIDI port names changed overnight from "FM-1 Audio 0/1" to "FM-1 Midi 0/1": find ports by substring.

## MCP server (`mcp_server/fm1_mcp.py`, registered in `.mcp.json`, venv `.venv`)
17 tools: status, search/load/get/init voice, set_params, play_note, play_sequence (per-note voices), stop, panic, set_fx,
output_trim, program_change, measure, meter, record (WAV), export_voice. Smoke test: `.venv/bin/python mcp_server/test_client.py` (Windows: `.venv\Scripts\python`).

## Drum kits & macros (2026-09-28)
- `build_kits.py` picks drum voices with strict name rules, builds 9 kits (5 coherent drum banks + 4 themed), plays
  every voice on the FM-1 (param diffs), drops voices silent at their key (6 dropped), records level/decay, and writes a
  per-voice Level macro that balances each kit → `app/library/kits.json`. Re-run it to re-measure.
- Drum macros (`app/drums.js`) are offsets on the voice's own parameters. Verified on hardware (`test_macro_hw.py`):
  Level −20 → −15.2 dB (0.75 dB/step); Decay acts on carrier R2/R3 only (on all ops it made hits sound *shorter*);
  Tone = modulator levels; Punch/Sweep = pitch-EG L4/R1; Grit = feedback.
- The unit's delay/reverb echoes contaminate decay measurements: measure with 3 s gaps and energy ratios, not
  "time to −40 dB".

## Workbench app (`app/`, launch with `start-workbench.cmd` → http://localhost:8731)
Chrome/Edge only (Web MIDI + SysEx permission; the Claude browser pane denies Web MIDI). Library in IndexedDB
(per browser profile). DX7 pack/unpack tested: `node app/test_dx7.cjs`.
Tabs: Editor · Drums (multi-voice step sequencer) · Player (.mid with per-channel voices, ch10 → drum kit).
`build_library.py` indexes `sysexFinal/` → `app/library/index.json` (35,782 unique voices, 2,223 drum-tagged).

## Scripts
`fm1.py` helpers · `dx7.py` VCED builder · `probe.py` dump requests · `listen.py` passive capture ·
`test_velocity.py`, `test_patch.py`, `test_cc_scan.py`, `test_fx_cc.py`, `test_busy.py`, `test_rules.py`,
`test_diffswap.py`, `test_voice_identity.py` hardware measurements · `test_e2e.py`, `test_e2e_sine.py` drive the app in
a throwaway Chrome profile (Playwright) and verify against recorded audio · `make_demo_mid.py` → `app/samples/demo.mid`.
