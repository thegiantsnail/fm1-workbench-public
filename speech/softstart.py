"""Soft note starts/stops against the per-note transient, without losing loudness. On the FM-1 with --hw.
  line    50 Hz note-rate line in the loudness envelope (dB; smoothness.frame_line)
  click   the same line on the >2.5 kHz band, where an onset click is broadband and speech is quiet
  loud    level of the loud stretches (dB re the 'base' variant, same playback gain; hardware only)
  words   24 phrases (--words)
    python speech/softstart.py [--hw] [--words] [variant ...]
"""
import json, pathlib, re, sys
import numpy as np
from scipy.signal import butter, sosfilt
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import resynth as R, eval_web as W, eval_designs as D
from smoothness import frame_line, TEXTS

OUT = W.OUT.parent / 'soft'; OUT.mkdir(parents=True, exist_ok=True)
BASE = {'diph': 'sweep', 'diphEdge': 0.15}
VARIANTS = {
    'base': {},
    'xfade': {'attack': 80, 'overlap': 20, 'releaseRate': 75},
    'rel': {'overlap': 14, 'releaseRate': 78},
    'soft4v': {'softStart': 4, 'softRise': 75, 'overlap': 10, 'releaseRate': 82},
    'soft6v': {'softStart': 6, 'softRise': 70, 'overlap': 12, 'releaseRate': 80},
    'soft9v': {'softStart': 9, 'softRise': 65, 'overlap': 14, 'releaseRate': 78},
    'soft6all': {'softStart': 6, 'softRise': 70, 'overlap': 12, 'releaseRate': 80, 'softVoiced': False},
    'xfade_v': {'softStart': 99, 'softRise': 80, 'overlap': 20, 'releaseRate': 75},
    'soft6v_nolap': {'softStart': 6, 'softRise': 70},
}
HP = butter(4, 2500, 'highpass', fs=R.SR, output='sos')


def click(x, rate=50.0):
    """50 Hz line of the >2.5 kHz envelope over the loud stretches of the FULL signal (dB above the neighbours)."""
    from scipy.signal import hilbert, resample_poly
    k = R.SR // 1000
    full = resample_poly(np.abs(hilbert(x)), 1, k); hf = resample_poly(np.abs(hilbert(sosfilt(HP, x))), 1, k)
    lv = 20 * np.log10(np.convolve(full, np.ones(20) / 20, 'same') + 1e-9)
    keep = hf[:len(lv)][lv > lv.max() - 20]
    if len(keep) < 500: return float('nan')
    keep = keep - keep.mean(); n = 1 << int(np.log2(len(keep)))
    sp = np.abs(np.fft.rfft(keep[:n] * np.hanning(n))) ** 2; f = np.fft.rfftfreq(n, 1 / 1000)
    out = []
    for h in (1, 2, 3):
        c = rate * h; line = sp[(f > c - 1.5) & (f < c + 1.5)].max()
        bg = np.median(sp[((f > c - 12) & (f < c - 4)) | ((f > c + 4) & (f < c + 12))]); out.append(10 * np.log10(line / (bg + 1e-18)))
    return float(np.mean(out))


def loud(x):
    w = int(0.02 * R.SR); e = np.array([10 * np.log10(np.mean(x[i:i + w] ** 2) + 1e-12) for i in range(0, len(x) - w, w)])
    return float(np.mean(e[e > e.max() - 20]))


def measure(opts, hw=False, tag=''):
    jobs = [{'text': t, 'opts': {**BASE, **opts}, 'wav': str(OUT / f'{tag}_{k}.wav'), 'events': str(OUT / f'ev{tag}_{k}.json')} for k, t in enumerate(TEXTS)]
    W.node(jobs)
    rows = []
    for j in jobs:
        wav = j['wav']
        if hw:
            wav = wav.replace(f'{tag}_', f'fm1{tag}_'); W.play_events(json.loads(pathlib.Path(j['events']).read_text()), wav)
        x = R.read_wav(wav)
        rows.append((frame_line(x), click(x), loud(x)))
    return np.nanmean(np.array(rows), axis=0)


if __name__ == '__main__':
    hw = '--hw' in sys.argv; words = '--words' in sys.argv
    names = [a for a in sys.argv[1:] if not a.startswith('--')] or list(VARIANTS)
    ref = None
    for n in names:
        tag = re.sub(r'\W', '', n) + ('h' if hw else '')
        ln, ck, ld = measure(VARIANTS[n], hw, tag)
        ref = ld if ref is None else ref
        out = f'{n:13} line {ln:5.1f} dB   click {ck:5.1f} dB   loud {ld - ref:+5.1f} dB'
        if words:
            t = W.run({**BASE, **VARIANTS[n]}, hw=hw, phrases=D.PHRASES, tag='_ss' + tag)
            out += '   words ' + '  '.join(f'{k} {a}/{b}' for k, (a, b) in t.items() if b)
        print(out, flush=True)
