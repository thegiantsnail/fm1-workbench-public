"""Pitch EG as a formant sweep: (1) does the FM-1's pitch EG move FIXED-frequency operators? (2) slow sweep timing:
a ratio-mode sine glides from pitch level 40 to 60 at PR1 = r; time to 50 % and 90 % of the glide, FM-1 vs software.
    python test_peg_sweep.py
"""
import json, time
import numpy as np, mido
import dx7
from fm1 import out_port, recording, SR
import test_soft_parity as P
from test_soft_parity import software, onset, hardware
from test_pitch_eg import pitch_track


def voice(start, target, rate, fixed=False):
    v = dx7.init_voice('PEGS', ALG=31, FB=0, OKS=1, LFD=0, LPMD=0, LAMD=0, PR1=rate, PR2=99, PR3=99, PR4=99,
                       PL1=target, PL2=target, PL3=target, PL4=start)
    for n in range(1, 7): v['ops'][n].update(OL=0, R1=99, R2=99, R3=99, R4=80, L1=99, L2=99, L3=99, L4=0, DT=7, FC=1, FF=0, KVS=0, MODE=0)
    v['ops'][1]['OL'] = 99
    if fixed: v['ops'][1].update(MODE=1, FC=2, FF=64)          # 10^2.64 = 437 Hz
    return v


def play(o, v, secs=1.0):
    """The voice by parameter changes (P.hardware) and in software; pitch track of the sounding part only."""
    P.NOTE, P.HOLD_MS = 69, int(secs * 1000)
    d = dx7.vced_list(v)[:155]
    sx = [0xF0, 0x43, 0, 0, 1, 0x1B, *d, (128 - (sum(d) & 127)) & 127, 0xF7]
    out = []
    for x in (hardware(sx), software(sx, 'pegs')):
        x = x[onset(x):][:int((secs - 0.05) * SR)]
        out.append(pitch_track(x))
    return out


def times(s, secs):
    s = s[4:]
    a, b = np.median(s[:3]), np.median(s[-40:])
    if abs(b - a) < 0.2: return a, b, 0, 0
    frac = (s - a) / (b - a)
    return a, b, np.argmax(frac >= 0.5) * 2.5, np.argmax(frac >= 0.9) * 2.5


if __name__ == '__main__':
    rows = []
    o = None
    if True:
        for fixed in (False, True):
            h, s = play(o, voice(40, 60, 99, fixed), 0.5)
            print(f"{'fixed' if fixed else 'ratio'} op, PEG 40 -> 60:  fm1 {np.median(h[4:12]):+5.2f} -> {np.median(h[-40:]):+5.2f} st | "
                  f"soft {np.median(s[4:12]):+5.2f} -> {np.median(s[-40:]):+5.2f} st", flush=True)
        for r in (40, 50, 55, 60, 65, 70, 75, 80, 85):
            h, s = play(o, voice(40, 60, r), 1.6)
            (ha, hb, h5, h9), (sa, sb, s5, s9) = times(h, 1.6), times(s, 1.6)
            print(f'PR1 {r}: fm1 {ha:+5.2f} -> {hb:+5.2f} st, 50% {h5:6.1f} 90% {h9:6.1f} ms | soft {sa:+5.2f} -> {sb:+5.2f}, 50% {s5:6.1f} 90% {s9:6.1f} ms', flush=True)
            rows.append((r, ha, hb, h5, h9, sa, sb, s5, s9))
    json.dump(rows, open('dumps/peg_sweep.json', 'w'))
