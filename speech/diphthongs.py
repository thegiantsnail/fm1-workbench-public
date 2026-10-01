"""Diphthongs as single notes with staggered operator envelopes (speech.js plan option `diph`).
  closed set  each word alone, Whisper prompted with its set (b_t and t_/h_ vowel sets): vowel identification
  50 Hz line  gravel on diphthong-rich phrases (smoothness.frame_line)
  words       the 24 phrases of eval_designs (--words)
    python speech/diphthongs.py [--hw] [--words] [variant ...]
"""
import json, pathlib, re, sys
import numpy as np
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import resynth as R, eval_web as W, eval_designs as D, sapi
from smoothness import frame_line

OUT = W.OUT.parent / 'diph'; OUT.mkdir(parents=True, exist_ok=True)
DIPH = {'ay', 'aw', 'oy', 'ey', 'ow'}
SETS = {
    'b_t': {'bite': 'ay', 'bait': 'ey', 'boat': 'ow', 'bout': 'aw', 'beat': 'iy', 'bit': 'ih', 'bet': 'eh', 'bat': 'ae', 'but': 'ah', 'boot': 'uw', 'bought': 'ao'},
    't_': {'tie': 'ay', 'toe': 'ow', 'toy': 'oy', 'tea': 'iy', 'two': 'uw', 'day': 'ey', 'how': 'aw', 'high': 'ay', 'hoe': 'ow', 'who': 'uw', 'boy': 'oy', 'bay': 'ey'},
}
TEXTS = ['I like to ride my bike by the white house.', 'How now, brown cow? Go slowly down the road.',
         'The boy enjoys his noisy toy.', 'They say the rain in May stays mainly away.']
VARIANTS = {
    'off': {},
    'single': {'diph': 'single'},
    'pair': {'diph': 'pair'},
    'sweep': {'diph': 'sweep'},
    'sweep_all': {'diph': 'sweep', 'diphAll': True},
    'sweep_early': {'diph': 'sweep', 'diphOnset': 0.2, 'diphGlide': 0.65},
    'sweep_edge': {'diph': 'sweep', 'diphEdge': 0.15},
    'sweep_all_edge': {'diph': 'sweep', 'diphAll': True, 'diphEdge': 0.15},
    'sweep_late': {'diph': 'sweep', 'diphOnset': 0.45, 'diphGlide': 0.95},
    'single_fine': {'diph': 'single', 'fine': True},
    'pair_all': {'diph': 'pair', 'diphAll': True},
    'single_all': {'diph': 'single', 'diphAll': True},
    'pair_fast': {'diph': 'pair', 'diphFall': 0.7, 'diphRise': 0.4},
    'pair_slow': {'diph': 'pair', 'diphFall': 1.4, 'diphRise': 0.7},
}
_model = None


def closed_set(opts, hw=False, tag=''):
    """Each word alone; Whisper prompted with its set (a listening test). Plus the F2 glide of the diphthong words:
    F2 at the first vs last quarter of the voiced stretch, synth vs the TTS original (error in Hz), and the F2
    track's jumpiness (mean |dF2| per 10 ms)."""
    R.whisper(str(OUT.parent / 'smooth' / 'tts_0.wav')); model = R._model
    hits = {'diph': [], 'mono': []}; wrong = []; err, jump = [], []
    for name, words in SETS.items():
        jobs = [{'text': w + '.', 'opts': opts, 'wav': str(OUT / f'i{tag}_{w}.wav'), 'events': str(OUT / f'iev{tag}_{w}.json')} for w in words]
        W.node(jobs)
        for w, j in zip(words, jobs):
            wav = j['wav']
            if hw:
                wav = wav.replace(f'i{tag}_', f'ifm1{tag}_'); W.play_events(json.loads(pathlib.Path(j['events']).read_text()), wav)
            segs, _ = model.transcribe(wav, language='en', beam_size=5, condition_on_previous_text=False,
                                       initial_prompt='Words: ' + ', '.join(words) + '.')
            heard = R.words(' '.join(sg.text for sg in segs))
            got = next((h for h in heard if h in words), heard[0] if heard else '')
            hits['diph' if words[w] in DIPH else 'mono'].append(got == w)
            if got != w: wrong.append(f'{w}->{got or "?"}')
            if words[w] in DIPH:
                ref = OUT / f'tts_{w}.wav'
                if not ref.exists(): sapi.say_batch([(w + '.', str(ref))])
                (a0, a1, _), (b0, b1, jb) = f2_glide(R.read_wav(str(ref))), f2_glide(R.read_wav(wav))
                err.append(abs((b1 - b0) - (a1 - a0))); jump.append(jb)
    return {k: (sum(v), len(v)) for k, v in hits.items()}, wrong, float(np.mean(err)), float(np.nanmean(jump))


def f2_glide(x):
    fr = [f for f in R.analyse(x) if f['f0'] > 0]
    if len(fr) < 6: return 0, 0, float('nan')
    F2 = np.array([f['F'][1] for f in fr])
    q = len(F2) // 4
    return float(np.median(F2[:max(2, q)])), float(np.median(F2[-max(2, q):])), float(np.mean(np.abs(np.diff(F2))))


def line(opts, hw=False, tag=''):
    jobs = [{'text': t, 'opts': opts, 'wav': str(OUT / f'l{tag}_{k}.wav'), 'events': str(OUT / f'lev{tag}_{k}.json')} for k, t in enumerate(TEXTS)]
    W.node(jobs)
    r = []
    for j in jobs:
        wav = j['wav']
        if hw:
            wav = wav.replace(f'l{tag}_', f'lfm1{tag}_'); W.play_events(json.loads(pathlib.Path(j['events']).read_text()), wav)
        r.append(frame_line(R.read_wav(wav)))
    return float(np.nanmean(r))


if __name__ == '__main__':
    hw = '--hw' in sys.argv; words = '--words' in sys.argv
    for n in [a for a in sys.argv[1:] if not a.startswith('--')] or list(VARIANTS):
        tag = re.sub(r'\W', '', n) + ('h' if hw else '')
        (cs, wrong, err, jump), ln = closed_set(VARIANTS[n], hw, tag), line(VARIANTS[n], hw, tag)
        out = (f"{n:12} closed diph {cs['diph'][0]}/{cs['diph'][1]} mono {cs['mono'][0]}/{cs['mono'][1]}   F2 glide err {err:4.0f} Hz"
               f"  F2 jump {jump:4.0f} Hz/10ms   50Hz line {ln:5.1f} dB")
        if words:
            t = W.run(VARIANTS[n], hw=hw, phrases=D.PHRASES, tag='_dp' + tag)
            out += '   words ' + '  '.join(f'{k} {a}/{b}' for k, (a, b) in t.items() if b)
        print(out + '   | ' + ' '.join(wrong), flush=True)
