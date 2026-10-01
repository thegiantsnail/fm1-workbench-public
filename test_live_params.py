"""Does a parameter change reach a note that is ALREADY sounding (FM-1+VA)? Hold one note 1.2 s; at 400 ms change
  level   op1 output level 99 -> 75 (expect -18 dB if live)
  ratio   op1 coarse 1 -> 2 (pitch doubles if live)
  fixed   op1 fixed frequency 440 -> 880 Hz
Prints level (dB) and the dominant frequency every 50 ms.
    python test_live_params.py
"""
import time
import numpy as np, mido
import dx7
from fm1 import out_port, recording, SR

CHANGES = {'level': [(5 * 21 + 16, 75)], 'ratio': [(5 * 21 + 18, 2)], 'fixed': [(5 * 21 + 17, 1), (5 * 21 + 18, 2), (5 * 21 + 19, 94)]}


def sine(fixed=False):
    v = dx7.init_voice('LIVE', ALG=31, FB=0, OKS=1, LFD=0, LPMD=0, LAMD=0)
    for n in range(1, 7): v['ops'][n].update(OL=0, R1=99, R2=99, R3=99, R4=80, L1=99, L2=99, L3=99, L4=0, DT=7, FC=1, FF=0, KVS=0)
    v['ops'][1]['OL'] = 99
    if fixed: v['ops'][1].update(MODE=1, FC=2, FF=64)
    return v


if __name__ == '__main__':
    with out_port() as o:
        for c in (0, 4, 8, 12, 16, 20): o.send(mido.Message('control_change', channel=1, control=c, value=0))
        for name, ch in CHANGES.items():
            d = dx7.vced_list(sine(name == 'fixed'))[:155] + [63]
            for p in list(range(145)) + [155]:
                o.send(mido.Message('sysex', data=[0x43, 0x10, (p >> 7) & 3, p & 127, d[p]]))
            time.sleep(0.6)                                   # let every parameter land first
            with recording(seconds_max=2.0) as rec:
                time.sleep(0.2); o.send(mido.Message('note_on', note=69, velocity=100)); time.sleep(0.4)
                for p, val in ch: o.send(mido.Message('sysex', data=[0x43, 0x10, (p >> 7) & 3, p & 127, val]))
                time.sleep(0.8); o.send(mido.Message('note_off', note=69)); time.sleep(0.3)
            x = rec['audio']; w = int(0.05 * SR)
            e = [10 * np.log10(np.mean(x[i:i + w] ** 2) + 1e-12) for i in range(0, len(x) - w, w)]
            on = int(np.argmax(np.array(e) > max(e) - 30))
            f = [np.fft.rfftfreq(8192, 1 / SR)[np.argmax(np.abs(np.fft.rfft(x[i:i + w], 8192)))] for i in range(on * w, len(x) - w, w)]
            print(f'{name:6} dB ', ' '.join(f'{v - max(e):4.0f}' for v in e[on:on + 26]))
            print(f'{"":6} Hz ', ' '.join(f'{v:4.0f}' for v in f[:26]), flush=True)
