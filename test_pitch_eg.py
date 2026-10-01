"""Pitch envelope as a per-note glide: how fast does the FM-1's pitch EG move, and is it linear near the centre?
A sine at A4 starts at pitch level L4 (below centre) and rises to L1-L3 = target at rate PR1; the pitch is tracked
every 2.5 ms. Prints: start/end offset (semitones) and the time to cover 90 % of the glide, FM-1 vs software.
    python test_pitch_eg.py
"""
import json, subprocess, time
import numpy as np, mido
from scipy.signal import hilbert
import dx7
from fm1 import out_port, recording, SR
from test_soft_parity import software, onset


def voice(start, target, rate):
    v = dx7.init_voice('PEG', ALG=31, FB=0, OKS=1, LFD=0, LPMD=0, LAMD=0, PR1=rate, PR2=99, PR3=99, PR4=99,
                       PL1=target, PL2=target, PL3=target, PL4=start)
    for n in range(1, 7): v['ops'][n].update(OL=0, R1=99, R2=99, R3=99, R4=80, L1=99, L2=99, L3=99, L4=0, DT=7, FC=1, FF=0, KVS=0)
    v['ops'][1]['OL'] = 99
    return v


def pitch_track(x):
    a = hilbert(x); inst = np.diff(np.unwrap(np.angle(a))) * SR / (2 * np.pi)
    k = int(0.0025 * SR); inst = np.convolve(inst, np.ones(k) / k, 'valid')[::k]
    return 12 * np.log2(np.clip(inst, 20, None) / 440.0)


def glide(semis):
    s = semis[2:int(0.35 / 0.0025)]
    a, b = np.median(s[:3]), np.median(s[-20:])
    t90 = np.argmax(np.abs(s - a) >= 0.9 * abs(b - a)) * 2.5 if abs(b - a) > 0.2 else 0
    return a, b, t90


if __name__ == '__main__':
    rows = []
    with out_port() as o:
        for c in (0, 4, 8, 12, 16, 20): o.send(mido.Message('control_change', channel=1, control=c, value=0))
        for start, target, rate in [(40, 50, 99), (40, 50, 90), (40, 50, 80), (40, 50, 70), (45, 50, 85), (50, 55, 85), (50, 44, 85)]:
            d = dx7.vced_list(voice(start, target, rate))[:155] + [63]
            for p in list(range(145)) + [155]:
                o.send(mido.Message('sysex', data=[0x43, 0x10, (p >> 7) & 3, p & 127, d[p]]))
            time.sleep(0.05)
            with recording(seconds_max=1.2) as rec:
                time.sleep(0.2); o.send(mido.Message('note_on', note=69, velocity=100)); time.sleep(0.6)
                o.send(mido.Message('note_off', note=69)); time.sleep(0.2)
            hw = rec['audio']; hw = hw[onset(hw):]
            sx = [0xF0, 0x43, 0, 0, 1, 0x1B, *d[:155], (128 - (sum(d[:155]) & 127)) & 127, 0xF7]
            sw = software(sx, f'peg{start}_{target}_{rate}'); sw = sw[onset(sw):]
            (ha, hb, ht), (sa, sb, st) = glide(pitch_track(hw)), glide(pitch_track(sw))
            print(f'L4 {start} -> L {target} at PR1 {rate}:  fm1 {ha:+5.2f} -> {hb:+5.2f} st in {ht:5.1f} ms | soft {sa:+5.2f} -> {sb:+5.2f} st in {st:5.1f} ms', flush=True)
            rows.append((start, target, rate, ha, hb, ht, sa, sb, st))
    json.dump(rows, open('dumps/pitch_eg.json', 'w'))
