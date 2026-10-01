"""Soft note starts on the FM-1 vs the software FM-1: the first 30 ms of a sine (1 ms RMS steps, dB re the sustain).
One MIDI session, every note released and the unit checked silent before the next (an earlier version used the
parity helper and recorded over a still-sounding note).
  R1 n          plain attack rate
  2stage L1 x   R1 99 -> L1 x (a partial jump: starts at -(99-x)*0.75 dB), then R2 -> L2 99 (eases up to full)
    python test_eg_softstart.py
"""
import json, subprocess, time, pathlib
import numpy as np, mido
import dx7
from fm1 import out_port, recording, SR

ROOT = pathlib.Path(__file__).resolve().parent
CASES = {
    'R1 99': dict(R1=99), 'R1 85': dict(R1=85), 'R1 80': dict(R1=80), 'R1 75': dict(R1=75), 'R1 70': dict(R1=70),
    '2stage L1 91 R2 75': dict(R1=99, L1=91, R2=75, L2=99), '2stage L1 91 R2 65': dict(R1=99, L1=91, R2=65, L2=99),
    '2stage L1 86 R2 75': dict(R1=99, L1=86, R2=75, L2=99), '2stage L1 86 R2 65': dict(R1=99, L1=86, R2=65, L2=99),
}


def voice(eg):
    v = dx7.init_voice('SOFT', ALG=31, FB=0, OKS=1, LFD=0, LPMD=0, LAMD=0)
    for n in range(1, 7): v['ops'][n].update(OL=0, R1=99, R2=99, R3=99, R4=80, L1=99, L2=99, L3=99, L4=0, DT=7, FC=1, FF=0, KVS=0)
    v['ops'][1].update(OL=99, **eg)
    return v


def env(x):
    w = int(0.001 * SR)
    e = np.array([10 * np.log10(np.mean(x[i:i + w] ** 2) + 1e-12) for i in range(0, len(x) - w, w)])
    sus = np.median(e[np.argmax(e > e.max() - 30) + 100:][:200])
    k = int(np.argmax(e > sus - 45))
    return e[k - 1:k + 30] - sus, e[:150].max() - sus                      # onset, and level before the note


def soft(v):
    d = dx7.vced_list(v)[:155]; sx = [0xF0, 0x43, 0, 0, 1, 0x1B, *d, (128 - (sum(d) & 127)) & 127, 0xF7]
    ev = [{'t': 0, 'b': sx}, {'t': 300, 'b': [0x90, 69, 100]}, {'t': 800, 'b': [0x80, 69, 0]}, {'t': 1200, 'b': [0xB0, 123, 0]}]
    (ROOT / 'dumps' / 'ss_ev.json').write_text(json.dumps(ev))
    wav = ROOT / 'dumps' / 'ss.wav'
    subprocess.run(['node', str(ROOT / 'speech' / 'web_speak.cjs'), json.dumps({'eventsIn': str(ROOT / 'dumps' / 'ss_ev.json'), 'wav': str(wav), 'sr': SR, 'raw': True})], check=True, capture_output=True)
    import wave
    with wave.open(str(wav)) as w: return np.frombuffer(w.readframes(w.getnframes()), '<i2').astype(float) / 32768


if __name__ == '__main__':
    with out_port() as o:
        for c in (0, 4, 8, 12, 16, 20): o.send(mido.Message('control_change', channel=1, control=c, value=0))
        for name, eg in CASES.items():
            v = voice(eg); d = dx7.vced_list(v)[:155] + [63]
            for p in list(range(145)) + [155]: o.send(mido.Message('sysex', data=[0x43, 0x10, (p >> 7) & 3, p & 127, d[p]]))
            time.sleep(0.3)
            with recording(seconds_max=1.2) as rec:
                time.sleep(0.25); o.send(mido.Message('note_on', note=69, velocity=100)); time.sleep(0.5)
                o.send(mido.Message('note_off', note=69)); time.sleep(0.4)
            (h, before), (s, _) = env(rec['audio']), env(soft(v))
            fmt = lambda e: ' '.join(f'{x:4.0f}' for x in e[:24])
            print(f'{name:20} fm1  {fmt(h)}   (before the note {before:4.0f} dB)\n{"":20} soft {fmt(s)}', flush=True)
