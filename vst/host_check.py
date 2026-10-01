"""Load the built FM-1 Controller in a real VST3 host and measure what its built-in synth renders.

    cd vst && cargo xtask bundle fm1_vst --release && cd ..
    python vst/host_check.py            # or: python vst/host_check.py "path/to/FM-1 Controller.vst3"

Needs pedalboard (a JUCE-based plugin host for Python), mido and numpy: pip install pedalboard mido numpy.
No hardware is used or touched: the plugin is pointed at a MIDI output that does not exist, so
"Auto" plays the built-in synth, as it does for anyone without the unit.
"""
import os
import sys
from pathlib import Path

os.environ["FM1_MIDI_OUT"] = "no-such-port"   # never touch the real unit from this check

import mido
import numpy as np
from pedalboard import load_plugin

BUNDLE = sys.argv[1] if len(sys.argv) > 1 else str(Path(__file__).parent / "target" / "bundled" / "FM-1 Controller.vst3")
plugin = load_plugin(BUNDLE)
print("loaded:", plugin.name, "| instrument:", plugin.is_instrument, "| parameters:", len(plugin.parameters))
print("sound param:", plugin.sound, "| valid:", plugin.parameters["sound"].valid_values)

def note(n, t0, t1, v=100):
    return [mido.Message("note_on", note=n, velocity=v, time=t0), mido.Message("note_off", note=n, time=t1)]

def render(msgs, seconds=2.0, sr=44100, reset=True):
    out = plugin(msgs, duration=seconds, sample_rate=sr, num_channels=2, buffer_size=512, reset=reset)
    assert out.shape[0] == 2 and np.array_equal(out[0], out[1]), "channels differ"
    assert np.all(np.isfinite(out)) and np.abs(out).max() <= 1.0
    return out[0]

def rms(x): return float(np.sqrt(np.mean(x * x)))
def peak_hz(x, sr):
    w = x * np.hanning(len(x)); s = np.abs(np.fft.rfft(w)); k = int(s.argmax())
    return k * sr / len(x)
def spectrum_db(x, sr, hz):
    w = x * np.hanning(len(x)); s = np.abs(np.fft.rfft(w)); k = int(round(hz * len(x) / sr))
    return 20 * np.log10(s[k - 2:k + 3].max() / s.max() + 1e-12)

ok = True
def check(name, cond, detail=""):
    global ok; ok &= bool(cond)
    print(("PASS" if cond else "FAIL"), name, detail)

# 1. Auto, no unit: the init voice sounds at the note's pitch and stops after the release.
x = render(note(69, 0.1, 1.0))
sr = 44100
held, tail = x[int(0.3 * sr):int(0.9 * sr)], x[int(1.6 * sr):]
check("auto without the unit makes sound", rms(held) > 0.05, f"rms {rms(held):.3f}")
check("pitch is A4", abs(peak_hz(held, sr) - 440) < 3, f"{peak_hz(held, sr):.1f} Hz")
check("silent before the note", rms(x[:int(0.09 * sr)]) == 0.0)
check("silent after the release", rms(tail) < 1e-4, f"rms {rms(tail):.2e}")

# 2. FM-1 only: the plugin itself is silent.
plugin.sound = "FM-1 only"
x = render(note(69, 0.1, 1.0))
check("FM-1 only is silent", rms(x) == 0.0, f"rms {rms(x):.2e}")

# 3. Built-in synth at 48 kHz: same pitch.
plugin.sound = "Built-in synth"
x = render(note(57, 0.1, 1.0), sr=48000)
check("48 kHz keeps the pitch", abs(peak_hz(x[14400:43200], 48000) - 220) < 3, f"{peak_hz(x[14400:43200], 48000):.1f} Hz")

# 4. Voice parameters from the host change the sound: bring in operator 2 as a modulator.
before = render(note(57, 0.1, 1.0))[13230:39690]
plugin.op2_level = 85
after = render(note(57, 0.1, 1.0))[13230:39690]
h2b, h2a = spectrum_db(before, sr, 440), spectrum_db(after, sr, 440)
check("operator 2 adds harmonics", h2a > h2b + 30, f"2nd harmonic {h2b:.0f} dB -> {h2a:.0f} dB")
plugin.op2_level = 0

# 5. A chord: three pitches at once.
x = render(note(60, 0.1, 1.0) + note(64, 0.1, 1.0) + note(67, 0.1, 1.0))[13230:39690]
got = [spectrum_db(x, sr, hz) > -12 for hz in (261.63, 329.63, 392.0)]
check("three-note chord", all(got), str(got))

# 6. Effects: reverb leaves a tail once the plugin controls the effects.
names = [n for n in plugin.parameters if "reverb" in n or n == "control_effects"]
plugin.control_effects = True; plugin.reverb_on = True; plugin.reverb_mix = 100; plugin.reverb_decay = 90
x = render(note(69, 0.1, 0.5), seconds=2.5)
check("reverb tail", rms(x[int(1.0 * sr):int(1.5 * sr)]) > 1e-4, f"rms {rms(x[int(1.0*sr):int(1.5*sr)]):.2e}  ({names})")
plugin.reverb_on = False; plugin.control_effects = False

# 7. Timing: a note at 0.5 s starts within a block of 0.5 s.
x = render(note(69, 0.5, 1.0))
first = int(np.argmax(np.abs(x) > 1e-4))
check("note timing", abs(first / sr - 0.5) < 0.004, f"first sound at {first / sr * 1000:.1f} ms")
print("ALL PASS" if ok else "SOME FAILED")
sys.exit(0 if ok else 1)
