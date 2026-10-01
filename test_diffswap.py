"""Can we switch whole voices per step by sending only the differing params (7-byte msgs)?
Alternates between two sine voices + two real library voices at 16th-note rates; measures onset jitter,
correct sound (for sine pair), and ringing-note preservation."""
import time, mido, numpy as np, pathlib
from fm1 import out_port, recording, SR, sysex, dx7_param
from dx7 import init_voice, vced_sysex, param_index
import sys; sys.path.insert(0, str(pathlib.Path(__file__).parent))

def load_bank(path):
    b = pathlib.Path(path).read_bytes()[6:6 + 4096]
    return [b[i * 128:(i + 1) * 128] for i in range(32)]

def vmem_to_vced(p):
    out = []
    for op in range(6):                     # OP6 first, same as VMEM
        q = p[op * 17:(op + 1) * 17]
        out += list(q[0:11]) + [q[11] & 3, (q[11] >> 2) & 3, q[12] & 7, q[13] & 3, (q[13] >> 2) & 7, q[14], q[15] & 1, (q[15] >> 1) & 31, q[16], (q[12] >> 3) & 15]
    out += list(p[102:110]) + [p[110] & 31, p[111] & 7, (p[111] >> 3) & 1] + list(p[112:116]) + [p[116] & 1, (p[116] >> 1) & 7, (p[116] >> 4) & 7, p[117]] + list(p[118:128])
    return out

def vced_of(v): return vced_sysex(v)[5:5 + 155]

T = time.perf_counter
def panic(o):
    for n in range(128): o.send(mido.Message('note_off', note=n))
def env(a): return np.sqrt(np.convolve(a ** 2, np.ones(44) / 44, 'same'))
def onset(e, t0, t1, thr=10 ** (-60 / 20)):
    i = np.flatnonzero(e[int(t0 * SR):int(t1 * SR)] > thr)
    return t0 + i[0] / SR if len(i) else None
def pitch(x):
    m = np.abs(np.fft.rfft(x * np.hanning(len(x)))); m[:10] = 0
    return np.argmax(m) * SR / len(x)

def send_diff(o, cur, new):
    n = 0
    for i, (x, y) in enumerate(zip(cur, new)):
        if x != y:
            o.send(mido.Message('sysex', data=[0x43, 0x10, (i >> 7) & 3, i & 127, y])); n += 1
    return n

def run(label, voices, step, hz=None, steps=24):
    with out_port() as o:
        panic(o); sysex(o, vced_sysex(init_voice('X')))
        time.sleep(0.2)
        cur = list(voices[0]); o.send(mido.Message('sysex', data=[0x43, 0x00, 0x00, 0x01, 0x1B] + cur + [(-sum(cur)) & 0x7F])); time.sleep(0.8)
        with recording(seconds_max=30) as rec:
            t0 = T(); now = lambda: T() - t0
            time.sleep(0.2)
            clean = []
            for _ in range(4):                       # latency reference, no swaps
                clean.append(now()); o.send(mido.Message('note_on', note=69, velocity=110)); time.sleep(0.06); o.send(mido.Message('note_off', note=69)); time.sleep(0.25)
            evs, sent, send_ms = [], [], []
            tstart = now() + 0.1
            for s in range(steps):
                target = tstart + s * step
                v = voices[s % len(voices)]
                while now() < target - 0.012: time.sleep(0.001)
                ts = now(); sent.append(send_diff(o, cur, v)); send_ms.append((now() - ts) * 1000); cur = list(v)
                while now() < target: pass
                evs.append((s % len(voices), now())); o.send(mido.Message('note_on', note=69, velocity=110))
                time.sleep(step * 0.4); o.send(mido.Message('note_off', note=69))
            time.sleep(0.5)
        panic(o)
    a = rec['audio']; e = env(a)
    off = float(np.median([onset(e, t, t + 0.5) - t for t in clean]))
    d, right = [], 0
    for k, tn in evs:
        on = onset(e, tn + off - 0.01, tn + off + step)
        d.append(None if on is None else (on - tn - off) * 1000)
        if hz and on is not None: right += abs(pitch(a[int((on + 0.005) * SR):int((on + 0.035) * SR)]) - hz[k]) < 80
    ok = [x for x in d if x is not None]
    print(f'{label}: {len(ok)}/{len(d)} notes, onset error median {np.median(ok):+.1f} ms, max {max(ok):+.1f} ms'
          f', params/step {np.mean(sent):.0f} (send {np.mean(send_ms):.1f} ms)' + (f', correct sound {right}/{len(ok)}' if hz else ''))

if __name__ == '__main__':
    S1 = vced_of(init_voice('SINE X1', ALG=31, ops={1: dict(OL=99, FC=1, R4=90)}))
    S2 = vced_of(init_voice('SINE X2', ALG=31, ops={1: dict(OL=99, FC=2, R4=90)}))
    for step in (0.25, 0.125, 0.0625):
        run(f'sine pair  step {int(step*1000):>3} ms', [S1, S2], step, hz={0: 440, 1: 880})
    bank = load_bank(pathlib.Path(__file__).parent / 'sysexFinal/0_Original_Yamaha/0_DX7/ROM1A.syx')
    real = [vmem_to_vced(bank[i]) for i in (0, 10, 18, 27)]          # 4 very different factory voices
    print('real voices:', [bytes(v[145:155]).decode() for v in real])
    for step in (0.25, 0.125, 0.0625):
        run(f'4 ROM voices step {int(step*1000):>3} ms', real, step)
