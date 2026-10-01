"""Gravel / staircase measurements for the speech voice (software FM-1, or the unit with --hw).
  roughness  loudness flicker at 30-150 Hz (where the 50 notes/s frame rate sits) relative to the slow 0-20 Hz
             syllable envelope, over the loud voiced stretches (dB; lower = smoother)
  steps      mean absolute pitch change per 10 ms in voiced frames (semitones; the key staircase)
Also Whisper words on the 24 phrases (eval_designs) with --words.
    python speech/smoothness.py [--hw] [--words] [variant ...]
"""
import json, pathlib, re, sys
import numpy as np
from scipy.signal import hilbert, resample_poly
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import resynth as R, eval_web as W, eval_designs as D, sapi

OUT = W.OUT.parent / 'smooth'; OUT.mkdir(parents=True, exist_ok=True)
TEXTS = ['Hello world. I am a synthesizer, made of sine waves.', 'Can you hear me now? The weather is nice and warm.',
         'My voice is made of many little notes.', 'Please say that again, slowly.']
VARIANTS = {
    'current': {},
    'fine': {'fine': True},
    'glide': {'fine': True, 'glide': 99},
    'xfade': {'attack': 80, 'overlap': 20, 'releaseRate': 75},
    'all': {'fine': True, 'glide': 99, 'attack': 80, 'overlap': 20, 'releaseRate': 75},
    'all_soft': {'fine': True, 'glide': 99, 'attack': 72, 'overlap': 30, 'releaseRate': 70},
    'sync': {'sync': True},
    'sync_xfade': {'sync': True, 'attack': 80, 'overlap': 20, 'releaseRate': 75},
    'sync_xfade_fine': {'sync': True, 'fine': True, 'glide': 99, 'attack': 80, 'overlap': 20, 'releaseRate': 75},
    'sync_harm_xfade': {'sync': True, 'design': 'harmonic', 'attack': 80, 'overlap': 20, 'releaseRate': 75},
    'sync_xfade_light': {'sync': True, 'attack': 88, 'overlap': 14, 'releaseRate': 80},
}


def roughness(x, sr=R.SR):
    env = np.abs(hilbert(x))
    env = resample_poly(env, 1, sr // 1000)                    # ~1 kHz envelope
    lv = 20 * np.log10(np.convolve(env, np.ones(20) / 20, 'same') + 1e-9)
    win, vals = 200, []
    for i in range(0, len(env) - win, win // 2):
        if lv[i:i + win].min() < lv.max() - 20: continue          # loud stretches only
        e = env[i:i + win] * np.hanning(win)
        s = np.abs(np.fft.rfft(e)) ** 2; f = np.fft.rfftfreq(win, 1 / 1000)
        vals.append(10 * np.log10(s[(f >= 30) & (f <= 150)].sum() / (s[(f > 0) & (f <= 20)].sum() + 1e-12)))
    return float(np.mean(vals)) if vals else float('nan')


def frame_line(x, sr=R.SR, rate=50.0):
    """Strength of the note-rate line (50 Hz and harmonics) in the loudness envelope of the loud stretches, dB above
    the surrounding envelope spectrum. Natural speech has no such line; a chain of 20 ms notes does."""
    env = resample_poly(np.abs(hilbert(x)), 1, sr // 1000)
    lv = 20 * np.log10(np.convolve(env, np.ones(20) / 20, 'same') + 1e-9)
    keep = env[lv > lv.max() - 20]
    if len(keep) < 500: return float('nan')
    keep = keep - keep.mean()
    n = 1 << int(np.log2(len(keep))); s = np.abs(np.fft.rfft(keep[:n] * np.hanning(n))) ** 2; f = np.fft.rfftfreq(n, 1 / 1000)
    out = []
    for h in (1, 2, 3):
        c = rate * h
        line = s[(f > c - 1.5) & (f < c + 1.5)].max()
        bg = np.median(s[((f > c - 12) & (f < c - 4)) | ((f > c + 4) & (f < c + 12))])
        out.append(10 * np.log10(line / (bg + 1e-18)))
    return float(np.mean(out))


def steps(x):
    fr = R.analyse(x)
    p = np.array([12 * np.log2(f['f0']) if f['f0'] > 0 else np.nan for f in fr])
    d = np.abs(np.diff(p)); d = d[~np.isnan(d) & (d < 3)]        # ignore octave slips
    return float(d.mean()) if len(d) else float('nan')


def measure(opts, hw=False, tag=''):
    jobs = [{'text': t, 'opts': opts, 'wav': str(OUT / f'{tag}_{k}.wav'), 'events': str(OUT / f'ev{tag}_{k}.json')} for k, t in enumerate(TEXTS)]
    W.node(jobs)
    r, s = [], []
    for j in jobs:
        wav = j['wav']
        if hw:
            wav = wav.replace(f'{tag}_', f'fm1{tag}_'); W.play_events(json.loads(pathlib.Path(j['events']).read_text()), wav)
        x = R.read_wav(wav); r.append(frame_line(x)); s.append(steps(x))
    return np.nanmean(r), np.nanmean(s)


if __name__ == '__main__':
    hw = '--hw' in sys.argv; words = '--words' in sys.argv
    names = [a for a in sys.argv[1:] if not a.startswith('--')] or list(VARIANTS)
    tts = [str(OUT / f'tts_{k}.wav') for k in range(len(TEXTS))]
    if not all(pathlib.Path(t).exists() for t in tts): sapi.say_batch(list(zip(TEXTS, tts)))
    xs = [R.read_wav(t) for t in tts]
    print(f"{'TTS original':14} 50Hz line {np.nanmean([frame_line(x) for x in xs]):6.1f} dB   steps {np.nanmean([steps(x) for x in xs]):.3f} st")
    for n in names:
        rr, ss = measure(VARIANTS[n], hw, tag=re.sub(r'\W', '', n))
        line = f"{n:14} 50Hz line {rr:6.1f} dB   steps {ss:.3f} st"
        if words:
            t = W.run(VARIANTS[n], hw=hw, phrases=D.PHRASES, tag='_sm' + re.sub(r'\W', '', n))
            line += '   words ' + '  '.join(f'{k} {a}/{b}' for k, (a, b) in t.items() if b)
        print(line, flush=True)
