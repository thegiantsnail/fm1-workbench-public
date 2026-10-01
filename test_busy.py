"""How long is the FM-1 'busy' after VCED / param change / program change?
A note-on is sent G ms after the message; we measure its onset delay relative to clean notes."""
import time, sys, mido, numpy as np
from fm1 import out_port, recording, SR, sysex, dx7_param
from dx7 import init_voice, vced_sysex, param_index

V = {k: init_voice(f'SINE X{k}', ALG=31, ops={1: dict(OL=99, FC=k, R4=90)}) for k in (1, 2)}
HZ = {1: 440, 2: 880}
GAPS = [0, 25, 50, 75, 100, 150, 200, 300]
REPS = 5
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

def run(mode):
    ev, clean = [], []
    with out_port() as o:
        panic(o); sysex(o, vced_sysex(V[1])); time.sleep(0.8)
        with recording(seconds_max=80) as rec:
            t0 = T(); now = lambda: T() - t0
            def note(dur=0.1):
                t = now(); o.send(mido.Message('note_on', note=69, velocity=110)); time.sleep(dur); o.send(mido.Message('note_off', note=69)); return t
            time.sleep(0.2)
            for _ in range(6): clean.append(note()); time.sleep(0.3)
            i = 0
            for rep in range(REPS):
                for g in GAPS:
                    k = 1 + i % 2; i += 1
                    if mode == 'vced': sysex(o, vced_sysex(V[k]))
                    elif mode == 'param': dx7_param(o, param_index(1, 'FC'), k)
                    elif mode == 'pc': o.send(mido.Message('program_change', program=k - 1))
                    ts = now()
                    while now() - ts < g / 1000: pass
                    ev.append((g, k, note()))
                    time.sleep(0.45)
        panic(o)
    a = rec['audio']; e = env(a)
    base = [onset(e, t, t + 0.6) - t for t in clean]
    off = float(np.median(base))
    print(f'\n=== {mode.upper()}: clean-note latency jitter {(max(base) - min(base)) * 1000:.1f} ms ===')
    print('  gap  | extra delay per trial (ms)                 | median | new sound')
    for g in GAPS:
        rows = [x for x in ev if x[0] == g]; ds, new = [], 0
        for _, k, tn in rows:
            on = onset(e, tn + off - 0.02, tn + off + 0.45)
            if on is None: ds.append(None); continue
            ds.append((on - tn - off) * 1000)
            if mode != 'pc': new += abs(pitch(a[int((on + 0.01) * SR):int((on + 0.09) * SR)]) - HZ[k]) < 60
        ok = [d for d in ds if d is not None]
        cells = ' '.join(f'{d:6.0f}' if d is not None else '  miss' for d in ds)
        print(f'  {g:>3}ms | {cells:42s} | {np.median(ok) if ok else float("nan"):6.0f} | {new if mode != "pc" else "-"}/{len(ok)}')

for m in (sys.argv[1:] or ['vced', 'param', 'pc']):
    run(m)
