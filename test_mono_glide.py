"""Can speech run as ONE gliding voice instead of a new note every 20 ms?  Probes on the FM-1 (FX off):
  1. DX7 function parameters over MIDI (F0 43 1n 08 pp dd F7): mono (64), portamento mode (67), time (69).
     Two overlapping notes, A3 then A4: poly = both pitches sound together; mono = the second replaces the first.
  2. Retrigger: does the second (legato) note restart the envelope (level dip) or continue?
  3. Glide: does the pitch slide from A3 to A4, and how long does it take?
  4. Legato + parameter change: change OP1's ratio (x1 -> x2) between the notes - does the legato note use it?
    python test_mono_glide.py
"""
import time
import numpy as np, mido
from scipy.signal import hilbert
import dx7
from fm1 import out_port, recording, SR


def sine_voice():
    v = dx7.init_voice('GLIDE', ALG=31, FB=0, OKS=1, LFD=0, LPMD=0, LAMD=0)
    for n in range(1, 7): v['ops'][n].update(OL=0, R1=99, R2=99, R3=99, R4=60, L1=99, L2=99, L3=99, L4=0, DT=7, FC=1, FF=0, KVS=0)
    v['ops'][1]['OL'] = 99
    return v


def load(o, v):
    d = dx7.vced_list(v)[:155] + [63]
    for p in list(range(145)) + [155]:
        o.send(mido.Message('sysex', data=[0x43, 0x10, (p >> 7) & 3, p & 127, d[p]]))
    time.sleep(0.05)


def func(o, p, v):                                   # DX7 function parameter change (group 2)
    o.send(mido.Message('sysex', data=[0x43, 0x10, 0x08, p, v]))


def track(x):
    """Per 10 ms: level dB and the frequencies of the two strongest peaks."""
    hop, win = int(0.01 * SR), int(0.04 * SR)
    rows = []
    for i in range(0, len(x) - win, hop):
        seg = x[i:i + win] * np.hanning(win)
        s = np.abs(np.fft.rfft(seg, 8192)); f = np.fft.rfftfreq(8192, 1 / SR)
        lv = 20 * np.log10(np.sqrt(np.mean(seg ** 2)) + 1e-12)
        k1 = int(np.argmax(s)); s2 = s.copy(); s2[max(0, k1 - 40):k1 + 40] = 0; k2 = int(np.argmax(s2))
        rows.append((lv, f[k1], f[k2] if s2[k2] > 0.25 * s[k1] else 0))
    return rows


def run(o, name, setup, between=None):
    load(o, sine_voice()); setup()
    with recording(seconds_max=2.0) as rec:
        time.sleep(0.2)
        o.send(mido.Message('note_on', note=57, velocity=100)); time.sleep(0.4)
        if between: between()
        o.send(mido.Message('note_on', note=69, velocity=100)); time.sleep(0.02)
        o.send(mido.Message('note_off', note=57)); time.sleep(0.5)
        o.send(mido.Message('note_off', note=69)); time.sleep(0.4)
    for n in range(128): o.send(mido.Message('note_off', note=n))
    x = rec['audio']; t = track(x)
    lv = np.array([r[0] for r in t]); on = int(np.argmax(lv > lv.max() - 30))
    second = on + 40                                       # the second note-on, 400 ms after the first
    both = sum(1 for r in t[second + 3:second + 30] if r[2] > 0)
    dip = lv[second - 3:second].mean() - lv[second:second + 4].min()
    pitch = [round(r[1]) for r in t[second - 2:second + 25:3]]
    print(f'{name:28} two pitches at once: {both:2} frames | level dip at the new note {dip:5.1f} dB | pitch {pitch}', flush=True)


if __name__ == '__main__':
    with out_port() as o:
        for c in (0, 4, 8, 12, 16, 20): o.send(mido.Message('control_change', channel=1, control=c, value=0))
        run(o, 'poly (default)', lambda: func(o, 64, 0))
        run(o, 'mono, no glide', lambda: (func(o, 64, 1), func(o, 69, 0)))
        run(o, 'mono, glide 40', lambda: (func(o, 64, 1), func(o, 67, 0), func(o, 69, 40)))
        run(o, 'mono, glide 70', lambda: (func(o, 64, 1), func(o, 67, 0), func(o, 69, 70)))
        run(o, 'mono, legato + OP1 x2 change', lambda: (func(o, 64, 1), func(o, 69, 0)),
            between=lambda: o.send(mido.Message('sysex', data=[0x43, 0x10, 0, 105 + 18, 2])))
        func(o, 64, 0); func(o, 69, 0)                     # back to poly, no glide
