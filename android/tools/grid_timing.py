"""Timing of the starter drum pattern in a recording, split into time ranges (e.g. screen on vs off).
Usage: python grid_timing.py file.wav [bpm] [split_s ...]"""
import sys, wave, numpy as np

path = sys.argv[1]; bpm = float(sys.argv[2]) if len(sys.argv) > 2 else 112
splits = [float(x) for x in sys.argv[3:]]
w = wave.open(path); sr = w.getframerate()
a = np.frombuffer(w.readframes(w.getnframes()), '<i2').reshape(-1, 2).mean(1) / 32768
env = np.sqrt(np.convolve(a ** 2, np.ones(48) / 48, 'same'))
sd = 60 / bpm / 4
n = int(len(a) / sr / sd) - 2


def expected(k): return (k % 16) in (0, 2, 4, 6, 7, 8, 10, 12, 14, 15) or (k % 7) == 3


def rises(phase):
    r = []
    for j in range(n):
        s = int((phase + j * sd) * sr)
        w0 = env[max(0, s - int(0.012 * sr)):s + int(0.025 * sr)]; pre = env[max(0, s - int(0.03 * sr)):max(1, s - int(0.012 * sr))]
        r.append(w0.max() / (pre.mean() + 1e-6) if len(w0) and len(pre) else 0)
    return np.array(r)


phase = max(np.arange(0, sd, 0.0005), key=lambda p: np.sum(np.log(rises(p) + 1e-3)))
r = rises(phase); hit = r > 2.0
m, K = max((sum(hit[j] == expected(j + K) for j in range(n)), K) for K in range(112))
exp = np.array([expected(j + K) for j in range(n)])
errs = []
for j in np.flatnonzero(hit & exp):
    t = phase + j * sd; s = int((t - 0.02) * sr); seg = env[s:s + int(0.06 * sr)]
    on = np.flatnonzero(seg > 0.5 * seg.max())
    if len(on): errs.append((t, (s + on[0]) / sr - t))
t_arr = np.array([e[0] for e in errs]); e_arr = np.array([e[1] for e in errs]); e_arr = (e_arr - np.median(e_arr)) * 1000
print(f'{path}: {n} steps, pattern agreement {m}/{n}; expected hits {exp.sum()}, clear attacks {(hit & exp).sum()}, extra {(hit & ~exp).sum()}')
edges = [0.0] + splits + [len(a) / sr]
for lo, hi in zip(edges, edges[1:]):
    sel = (t_arr >= lo) & (t_arr < hi)
    if sel.sum() > 3:
        e = e_arr[sel]
        print(f'  {lo:5.1f}-{hi:5.1f} s: {sel.sum():3d} hits, timing sd {e.std():.1f} ms, p5..p95 {np.percentile(e, 5):+.1f}..{np.percentile(e, 95):+.1f} ms')
