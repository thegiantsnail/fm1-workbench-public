"""Record the FM-1 USB audio for N seconds and print a per-second level log (peak/RMS dBFS, clipped samples)."""
import sys, numpy as np, sounddevice as sd
from fm1 import AUDIO_IN, SR

SECS = int(sys.argv[1]) if len(sys.argv) > 1 else 40
print(f'recording {SECS}s from FM-1 USB audio...', flush=True)
a = sd.rec(SECS * SR, samplerate=SR, channels=2, device=AUDIO_IN, dtype='float32'); sd.wait()
np.save('dumps/level_capture.npy', a)
db = lambda x: 20 * np.log10(x + 1e-9)
print(' sec | peak L  peak R | RMS dBFS | clipped | meter')
for s in range(SECS):
    x = a[s * SR:(s + 1) * SR]
    pk = np.abs(x).max(axis=0); rms = np.sqrt(np.mean(x ** 2)); clip = int((np.abs(x) >= 0.999).sum())
    bar = '#' * max(0, int((db(rms) + 60) / 2))
    print(f' {s:>3} | {db(pk[0]):6.1f} {db(pk[1]):6.1f} | {db(rms):7.1f}  | {clip:>6}  | {bar}')
pk = np.abs(a).max()
print(f'overall peak {db(pk):.1f} dBFS, clipped samples {(np.abs(a) >= 0.999).sum()}, L/R identical: {np.allclose(a[:, 0], a[:, 1])}')
