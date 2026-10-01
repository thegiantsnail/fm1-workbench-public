"""Check the FM-1 Controller against the real unit by recording the FM-1's USB audio.

    cd vst && cargo build --release --examples && cd ..
    python vst/hw_check.py            # add --va if the unit runs the FM-1+VA firmware

Needs mido, python-rtmidi, sounddevice and numpy (the workbench's requirements.txt). Nothing
else may be driving the FM-1. The run changes the FM-1's voice and sounds it for a few seconds.

1. The unit is made silent with plain parameter changes (every operator level to 0).
2. The plugin's standalone build starts; it must reprogram the voice, or step 3 stays silent.
3. Eight notes go in through a virtual MIDI port; their onsets are timed from the recording.
4. A note is held, then released and the output dropped at once: it must stop.
5. A drum kit from the locally built library is played with a different voice on every hit:
   every hit must sound and the unit must be silent afterwards. Skipped when no kits have been
   built. (Hit timing is not scored: drum voices differ too much in attack for one detector.)
6. A phrase is spoken through the workbench's speech engine: it must sound for as long as the
   engine planned, leave nothing sounding, and stop at once when cut short. Needs app/speech.js
   and its data. Whether the words can be understood is not something this can score. The
   phrase is then spoken by the standalone plugin itself (FM1_SPEAK), after which a note must
   play the plugin's own voice again.
7. --va only: the master volume (CC 7) and LFO speed (CC 76) the plugin sends take effect.
"""

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import mido
import numpy as np
import sounddevice as sd

SR = 44100
BUILD = Path(__file__).parent / "target" / "release"
PORT = "FM1 VST Test"
NOTES, STEP, LENGTH = 8, 0.25, 0.06
# Kit 6 ("Acoustic") has short releases; kit 0's open hat rings for about ten seconds.
KIT, KIT_HITS, KIT_STEP_MS, KIT_LEAD_MS = 6, 20, 125, 20
PHRASE = "Hello there. Can you hear me?"
SILENT_DBFS, MAX_TIMING_MS = -80.0, 3.0


def midi_out():
    name = next((n for n in mido.get_output_names() if "FM-1" in n), None)
    if name is None:
        sys.exit("No MIDI output containing 'FM-1'; is the unit plugged in?")
    return mido.open_output(name)


def audio_in():
    found = [
        (i, sd.query_hostapis(d["hostapi"])["name"])
        for i, d in enumerate(sd.query_devices())
        if "FM-1" in d["name"] and d["max_input_channels"] > 0
    ]
    if not found:
        sys.exit("No audio input containing 'FM-1'.")
    return next((i for i, api in found if api == "MME"), found[0][0])


def record(device, seconds, during=lambda: None):
    chunks = []

    def callback(data, frames, when, status):
        chunks.append(data.copy())

    with sd.InputStream(
        samplerate=SR, channels=2, device=device, dtype="float32", callback=callback
    ):
        start = time.monotonic()
        during()
        time.sleep(max(0.0, seconds - (time.monotonic() - start)))
    return np.concatenate(chunks).mean(axis=1)


def rms_db(audio):
    return round(float(20 * np.log10(np.sqrt(np.mean(audio**2)) + 1e-12)), 1)


def envelope(audio, seconds=0.002):
    window = int(SR * seconds)
    return np.convolve(np.abs(audio), np.ones(window) / window, mode="same")


def onsets(audio):
    level = envelope(audio)
    loud = level > max(level.max() * 0.2, 1e-5)
    quiet_before = int(SR * 0.05)
    starts = np.flatnonzero(loud[1:] & ~loud[:-1]) + 1
    return [i / SR for i in starts if not loud[max(0, i - quiet_before) : i].any()]


def run_while_recording(device, command, tail):
    chunks = []

    def callback(data, frames, when, status):
        chunks.append(data.copy())

    with sd.InputStream(
        samplerate=SR, channels=2, device=device, dtype="float32", callback=callback
    ):
        done = subprocess.run(command, capture_output=True, text=True)
        time.sleep(tail)
    return np.concatenate(chunks).mean(axis=1), done.stdout


def vibrato(audio):
    """Level (dBFS) and vibrato rate (Hz) of the steady part of a held note."""
    level = envelope(audio, 0.01)
    loud = np.flatnonzero(level > level.max() * 0.3)
    steady = audio[loud[0] + SR // 2 : loud[-1] - SR // 4]
    # Pitch over time from the spacing of upward zero crossings, resampled to a 1 kHz track.
    rising = np.flatnonzero((steady[:-1] < 0) & (steady[1:] >= 0))
    pitch = SR / np.diff(rising)
    grid = np.arange(rising[1], rising[-1], SR // 1000)
    track = np.interp(grid, rising[1:], pitch)
    track = track - track.mean()
    spectrum = np.abs(np.fft.rfft(track * np.hanning(len(track))))
    rates = np.fft.rfftfreq(len(track), 1 / 1000)
    band = (rates > 0.7) & (rates < 60.0)
    return rms_db(steady), round(float(rates[band][np.argmax(spectrum[band])]), 1)


def main():
    device = audio_in()
    results = {}

    # Silence the unit the way the plugin never would: every operator level to 0.
    unit = midi_out()
    for op in range(6):
        index = op * 21 + 16
        unit.send(mido.Message("sysex", data=[0x43, 0x10, index >> 7, index & 127, 0]))

    def direct_note():
        unit.send(mido.Message("note_on", note=69, velocity=100))
        time.sleep(0.4)
        unit.send(mido.Message("note_off", note=69))

    baseline = rms_db(record(device, 1.0, direct_note))
    unit.close()
    results["unit silenced before the plugin starts"] = (
        baseline < SILENT_DBFS,
        f"{baseline} dBFS",
    )

    out = mido.open_output(PORT, virtual=True)
    # The standalone wrapper needs the output device's own sample rate.
    plugin = subprocess.Popen(
        [
            BUILD / "fm1_vst",
            "--midi-input",
            PORT,
            "--sample-rate",
            "44100",
            "--period-size",
            "64",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(3.0)
    if plugin.poll() is not None:
        sys.exit("The standalone build exited; run it by hand to see why.")

    def play():
        start = time.monotonic() + 0.3
        for i in range(NOTES):
            for message, at in (("note_on", i * STEP), ("note_off", i * STEP + LENGTH)):
                while time.monotonic() < start + at:
                    time.sleep(0.0002)
                out.send(mido.Message(message, note=69, velocity=100))

    audio = record(device, 3.5, lambda: threading.Thread(target=play, daemon=True).start())
    plugin.terminate()
    plugin.wait(timeout=5)
    out.close()

    times = onsets(audio)
    peak = round(float(20 * np.log10(np.abs(audio).max() + 1e-12)), 1)
    results["plugin reprogrammed the voice"] = (
        peak > SILENT_DBFS,
        f"notes peak at {peak} dBFS",
    )
    results["every note sounded"] = (
        len(times) == NOTES,
        f"{len(times)} of {NOTES} onsets",
    )
    if len(times) == NOTES:
        errors = np.abs(np.diff(times) - STEP) * 1000
        worst = round(float(errors.max()), 2)
        results["note spacing"] = (worst < MAX_TIMING_MS, f"worst error {worst} ms")
    tail = rms_db(audio[int((0.3 + NOTES * STEP + 0.5) * SR) :])
    results["no note left hanging"] = (
        tail < SILENT_DBFS,
        f"{tail} dBFS after the last note-off",
    )

    time.sleep(0.3)
    holder = subprocess.Popen([BUILD / "examples" / "hold_and_drop"], stdout=subprocess.DEVNULL)
    time.sleep(0.7)
    held = rms_db(record(device, 0.5))
    holder.wait(timeout=10)
    time.sleep(0.3)
    after = rms_db(record(device, 0.5))
    results["held note stops when the output shuts down"] = (
        held > SILENT_DBFS and after < SILENT_DBFS,
        f"{held} dBFS held, {after} dBFS after",
    )

    time.sleep(0.3)
    kit_play = [
        BUILD / "examples" / "kit_play",
        *map(str, (KIT, KIT_HITS, KIT_STEP_MS, KIT_LEAD_MS)),
    ]
    audio, log = run_while_recording(device, kit_play, 2.0)
    level = envelope(audio)
    first = int(np.argmax(level > level.max() * 0.05))
    step = int(SR * KIT_STEP_MS / 1000)
    heard = sum(
        level[max(0, first + i * step - step // 4) : first + i * step + step // 2].max()
        > level.max() * 0.03
        for i in range(KIT_HITS)
    )
    if not log.strip():
        # No kits: they are built from the user's own patch banks and do not ship with the project.
        results["kit playback"] = (None, "no kits.json built on this machine")
    else:
        dropped = log.strip().splitlines()[-1].split()[-1]
        results["every kit hit sounded, a voice switch on each"] = (
            heard == KIT_HITS and dropped == "0",
            f"{heard} of {KIT_HITS} hits, {dropped} MIDI messages dropped",
        )
        kit_tail = rms_db(audio[-int(0.3 * SR) :])
        results["silent after the kit"] = (kit_tail < SILENT_DBFS, f"{kit_tail} dBFS")

    time.sleep(0.3)
    speak = BUILD / "examples" / "speak"
    audio, log = run_while_recording(device, [speak, PHRASE], 0.8)
    lines = log.strip().splitlines()
    if not lines or " s · " not in lines[0]:
        results["speech engine available"] = (
            False,
            "the speak example printed no phrase summary",
        )
    else:
        planned = float(lines[0].split(" · ")[1].split()[0])
        level = envelope(audio, 0.01)
        loud = np.flatnonzero(level > level.max() * 0.03)
        spoken = round((loud[-1] - loud[0]) / SR, 2)
        results["phrase spoken for as long as planned"] = (
            abs(spoken - planned) < 0.25 and lines[-1].endswith("dropped 0"),
            f"{spoken} s of sound for a {planned} s phrase; {lines[-1].split('; ')[-1]}",
        )
        speech_tail = rms_db(audio[-int(0.3 * SR) :])
        results["silent after the phrase"] = (
            speech_tail < SILENT_DBFS,
            f"{speech_tail} dBFS",
        )

        audio, _ = run_while_recording(device, [speak, PHRASE, "natural", "45", "700"], 0.8)
        level = envelope(audio, 0.01)
        loud = np.flatnonzero(level > level.max() * 0.03)
        cut = round((loud[-1] - loud[0]) / SR, 2)
        results["phrase stops when cut short"] = (
            cut < 1.2 and rms_db(audio[-int(0.3 * SR) :]) < SILENT_DBFS,
            f"{cut} s of sound after a stop at 0.7 s",
        )

        # The same phrase through the plugin's own audio callback, then a note on its voice.
        out = mido.open_output(PORT, virtual=True)
        chunks = []

        def speech_callback(data, frames, when, status):
            chunks.append(data.copy())

        with sd.InputStream(
            samplerate=SR, channels=2, device=device, dtype="float32", callback=speech_callback
        ):
            plugin = subprocess.Popen(
                [BUILD / "fm1_vst", "--midi-input", PORT, "--sample-rate", "44100"],
                env={**os.environ, "FM1_SPEAK": PHRASE},
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            time.sleep(planned + 2.5)
            spoken_samples = sum(len(c) for c in chunks)
            out.send(mido.Message("note_on", note=69, velocity=100))
            time.sleep(0.5)
            out.send(mido.Message("note_off", note=69))
            time.sleep(0.8)
            plugin.terminate()
            plugin.wait(timeout=5)
        out.close()
        audio = np.concatenate(chunks).mean(axis=1)
        level = envelope(audio[:spoken_samples], 0.01)
        loud = np.flatnonzero(level > level.max() * 0.03)
        spoken = round((loud[-1] - loud[0]) / SR, 2) if len(loud) else 0.0
        results["plugin speaks the phrase from its audio callback"] = (
            abs(spoken - planned) < 0.3,
            f"{spoken} s of sound for a {planned} s phrase",
        )
        note = audio[spoken_samples + int(0.15 * SR) : spoken_samples + int(0.45 * SR)]
        spectrum = np.abs(np.fft.rfft(note * np.hanning(len(note))))
        strongest = float(np.fft.rfftfreq(len(note), 1 / SR)[np.argmax(spectrum)])
        results["plugin's own voice is back after speech"] = (
            rms_db(note) > SILENT_DBFS and abs(strongest - 440) < 5,
            f"note at {rms_db(note)} dBFS, strongest partial {strongest:.0f} Hz (init voice: 440)",
        )

    if "--va" in sys.argv:
        # The workbench measured CC 7 = 100 at -2.3 dB and 32 at -12 dB, and CC 76 = 40 at
        # 5.0 Hz and 80 at 10.4 Hz. LFO speeds 31 and 62 scale to those controller values.
        va_check = BUILD / "examples" / "va_check"
        takes = {}
        for speed, volume in ((31, 100), (31, 32), (62, 100)):
            audio, _ = run_while_recording(device, [va_check, str(speed), str(volume)], 0.4)
            takes[speed, volume] = vibrato(audio)
        drop = round(takes[31, 100][0] - takes[31, 32][0], 1)
        results["FM-1+VA: volume 100 to 32 lowers the level"] = (
            6.0 < drop < 14.0,
            f"{drop} dB quieter (the workbench measured 9.7)",
        )
        slow, fast = takes[31, 100][1], takes[62, 100][1]
        results["FM-1+VA: LFO speed follows the voice"] = (
            abs(slow - 5.0) < 1.0 and abs(fast - 10.4) < 1.5,
            f"vibrato at {slow} Hz and {fast} Hz (the workbench measured 5.0 and 10.4)",
        )

    for name, (passed, detail) in results.items():
        verdict = "SKIP" if passed is None else "PASS" if passed else "FAIL"
        print(f"{verdict}  {name}: {detail}")
    return 0 if all(passed is not False for passed, _ in results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
