"""Compare the plugin's built-in synth with the real FM-1: level, spectrum and low end.

    cd vst && cargo build --release --examples && cd ..
    python vst/synth_check.py [--va] [library index ...]

Needs mido, python-rtmidi, sounddevice and numpy (the workbench's requirements.txt), a built
voice library, and the FM-1 connected with nothing else driving it. The run changes the FM-1's
voice and sounds it for about a minute. Voices reach the unit as single-parameter changes only.

1. Each library voice is played on the unit (examples/play_voice) and rendered on the synth
   (examples/render) at the same note. Reported: level of each, and how alike the spectra are
   (1.0 = the same energy in every sixth-octave band from 60 Hz to 15 kHz).
2. The init voice, a plain sine, is played on low notes on both: the unit's bass roll-off.

--va: the unit runs Baud Girl's FM-1+VA firmware. The synth is then rendered as that firmware
and the unit's master volume (CC 7) is set to full for the run and back to 100 afterwards.
"""

import subprocess
import sys
import time
from pathlib import Path

import mido
import numpy as np
import sounddevice as sd

SR = 44100
BUILD = Path(__file__).parent / "target" / "release" / "examples"
OUT = Path(__file__).parent / "target" / "synth_check.wav"
VOICES = [0, 10, 13, 21, 26, 15, 5, 23]
LOW_NOTES = [0, 6, 12, 18, 24, 30, 36, 48, 60]


def synth(index, note, held, va):
    command = [BUILD / "render", OUT, str(index), str(note), str(held)] + (["va"] if va else [])
    subprocess.run(command, capture_output=True, check=True)
    return np.frombuffer(OUT.read_bytes()[44:], dtype="<i2").astype(np.float64) / 32768


def onset(x):
    return int(np.argmax(np.abs(x) > 0.02 * np.abs(x).max()))


def rms_db(x):
    x = x - x.mean()
    return 20 * np.log10(np.sqrt(np.mean(x * x)) + 1e-12)


def bands(x):
    power = np.abs(np.fft.rfft(x * np.hanning(len(x)))) ** 2
    hz = np.fft.rfftfreq(len(x), 1 / SR)
    edges = 60 * 2 ** (np.arange(0, 49) / 6)
    return np.sqrt([power[(hz >= a) & (hz < b)].sum() for a, b in zip(edges[:-1], edges[1:])])


def tone_db(x, hz):
    window = np.hanning(len(x))
    return 20 * np.log10(2 * abs(np.sum(window * x * np.exp(-2j * np.pi * hz * np.arange(len(x)) / SR))) / window.sum() + 1e-12)


def main():
    args = [a for a in sys.argv[1:] if a != "--va"]
    va = "--va" in sys.argv
    voices = [int(a) for a in args] or VOICES
    device = next((i for i, d in enumerate(sd.query_devices()) if "FM-1" in d["name"] and d["max_input_channels"] > 0), None)
    name = next((n for n in mido.get_output_names() if "FM-1" in n), None)
    if device is None or name is None:
        sys.exit("FM-1 not found")
    port = mido.open_output(name)

    def record(seconds):
        return sd.rec(int(seconds * SR), samplerate=SR, channels=2, device=device, dtype="float32")

    if va:
        port.send(mido.Message("control_change", channel=0, control=7, value=127))
        time.sleep(0.1)

    print(f"{'voice':<12} {'unit dBFS':>9} {'synth dBFS':>10} {'synth louder by':>16} {'spectrum':>9}")
    gaps = []
    for index in voices:
        take = record(2.4)
        time.sleep(0.3)
        played = subprocess.run([BUILD / "play_voice", str(index), "60"], capture_output=True, text=True)
        sd.wait()
        unit, soft = take.mean(axis=1).astype(np.float64), synth(index, 60, 1.2, va)
        unit, soft = unit[onset(unit):], soft[onset(soft):]
        a, b = unit[int(0.05 * SR):int(0.6 * SR)], soft[int(0.05 * SR):int(0.6 * SR)]
        (x, y), gap = (bands(a), bands(b)), rms_db(b) - rms_db(a)
        alike = float(np.dot(x, y) / (np.linalg.norm(x) * np.linalg.norm(y)))
        gaps.append(gap)
        print(f"{played.stdout.split(' (')[0]:<12} {rms_db(a):9.1f} {rms_db(b):10.1f} {gap:13.1f} dB {alike:9.3f}")
        time.sleep(1.2)
    print(f"synth louder by: median {np.median(gaps):.2f} dB, from {min(gaps):.1f} to {max(gaps):.1f}\n")

    # The init voice with operator 1 audible, then low notes.
    init = []
    for op in range(6, 0, -1):
        init += [99, 99, 99, 99, 99, 99, 99, 0, 39, 0, 0, 0, 0, 0, 0, 0, 99 if op == 1 else 0, 0, 1, 0, 7]
    init += [99, 99, 99, 99, 50, 50, 50, 50, 0, 0, 1, 35, 0, 0, 0, 1, 0, 3, 24]
    for parameter, value in list(enumerate(init)) + [(155, 63)]:
        port.send(mido.Message("sysex", data=[0x43, 0x10, parameter >> 7, parameter & 127, value]))
        time.sleep(0.002)
    rows = []
    for note in LOW_NOTES:
        hz = 440 * 2 ** ((note - 69) / 12)
        take = record(3.0)
        time.sleep(0.3)
        port.send(mido.Message("note_on", note=note, velocity=100))
        time.sleep(2.2)
        port.send(mido.Message("note_off", note=note))
        sd.wait()
        unit = take.mean(axis=1).astype(np.float64)[int(0.9 * SR):int(2.3 * SR)]
        soft = synth("-", note, 2.2, va)[int(0.5 * SR):int(1.9 * SR)]
        rows.append((note, hz, tone_db(unit, hz), tone_db(soft, hz)))
        time.sleep(0.5)
    print(f"{'note':>4} {'Hz':>7}   relative to note 60: {'unit':>6} {'synth':>6}")
    for note, hz, unit, soft in rows:
        print(f"{note:4d} {hz:7.1f}   {'':21}{unit - rows[-1][2]:6.1f} {soft - rows[-1][3]:6.1f}")
    if va:
        port.send(mido.Message("control_change", channel=0, control=7, value=100))


if __name__ == "__main__":
    main()
