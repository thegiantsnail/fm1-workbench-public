"""How long must we wait after SysEx before a note-on / note-off is reliably received?
Each run first calibrates the MIDI-send -> recorded-audio offset with plain notes (no SysEx),
then sweeps the gap after a VCED voice dump (163 B) or a single param change (7 B)."""
import time, sys, mido, numpy as np
from fm1 import out_port, recording, SR, sysex, dx7_param
from dx7 import init_voice, vced_sysex, param_index

V = {k: init_voice(f'SINE X{k}', ALG=31, ops={1: dict(OL=99, FC=k, R4=90)}) for k in (1, 2)}
HZ = {1: 440, 2: 880}
GAPS = [0, 2, 4, 6, 8, 12, 16, 24, 40]
REPS = 3
NOTE_LEN = 0.25
T = time.perf_counter

def panic(o):  # FM-1 ignores CC120/CC123; only explicit note-offs release voices
    for n in range(128):
        o.send(mido.Message('note_off', note=n))

def env(a):
    return np.sqrt(np.convolve(a ** 2, np.ones(88) / 88, 'same'))

def rms_db(x): return 20 * np.log10(np.sqrt(np.mean(x ** 2)) + 1e-9)

def pitch(x):
    m = np.abs(np.fft.rfft(x * np.hanning(len(x)))); m[:10] = 0
    return np.argmax(m) * SR / len(x)

def first_onset(e, t0, t1, thr=10 ** (-60 / 20)):
    i0, i1 = int(t0 * SR), int(t1 * SR)
    idx = np.flatnonzero(e[i0:i1] > thr)
    return (i0 + idx[0]) / SR if len(idx) else None

def run(mode):
    cal, trials = [], []
    with out_port() as o, recording(seconds_max=90) as rec:
        t0 = T(); now = lambda: T() - t0
        panic(o); sysex(o, vced_sysex(V[1])); time.sleep(0.5)
        for _ in range(5):                                        # calibration notes, clean
            tn = now(); o.send(mido.Message('note_on', note=69, velocity=110))
            time.sleep(NOTE_LEN); o.send(mido.Message('note_off', note=69)); time.sleep(0.35)
            cal.append(tn)
        for g in GAPS:
            for r in range(REPS):
                for k in (1, 2):
                    sysex(o, vced_sysex(V[3 - k])); time.sleep(0.15)       # known start patch
                    if mode == 'vced': sysex(o, vced_sysex(V[k]))
                    else: dx7_param(o, param_index(1, 'FC'), k)
                    time.sleep(g / 1000)
                    tn = now(); o.send(mido.Message('note_on', note=69, velocity=110))
                    time.sleep(NOTE_LEN)
                    if mode == 'vced': sysex(o, vced_sysex(V[k]))
                    else: dx7_param(o, param_index(1, 'FC'), k)
                    time.sleep(g / 1000)
                    toff = now(); o.send(mido.Message('note_off', note=69))
                    time.sleep(0.3)
                    trials.append((g, k, tn, toff))
                    panic(o); time.sleep(0.1)
        panic(o)
    a = rec['audio']; e = env(a)
    np.save(f'dumps/gap_{mode}.npy', a)
    offs = [first_onset(e, t - 0.05, t + 0.4) for t in cal]
    offs = [x - t for x, t in zip(offs, cal) if x is not None]
    off = float(np.median(offs))
    print(f'\n=== after {mode.upper()} ({"163" if mode == "vced" else "7"} bytes) ===')
    print(f'calibration: send->audio offset {off*1000:.1f} ms (spread {min(offs)*1000:.1f}..{max(offs)*1000:.1f}, n={len(offs)})')
    print(' gap  | note heard | onset delay vs clean | NEW patch | note-off honoured')
    for g in GAPS:
        ts = [t for t in trials if t[0] == g]
        heard, new, rel, delays = 0, 0, 0, []
        for _, k, tn, toff in ts:
            on = first_onset(e, tn + off - 0.03, tn + off + NOTE_LEN)
            if on is not None:
                heard += 1; delays.append((on - tn - off) * 1000)
                new += abs(pitch(a[int((on + 0.02) * SR):int((on + 0.15) * SR)]) - HZ[k]) < 60
            tail = a[int((toff + off + 0.12) * SR):int((toff + off + 0.28) * SR)]
            rel += rms_db(tail) < -70
        d = f'{np.median(delays):+6.1f} ms' if delays else '   —    '
        print(f' {g:>2} ms |   {heard}/{len(ts)}      |     {d}         |   {new}/{heard}     |   {rel}/{len(ts)}')

run(sys.argv[1] if len(sys.argv) > 1 else 'vced')
