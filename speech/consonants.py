"""Consonant drill-down for the speech planner.

1. Spectra: for each fricative, the rendered consonant (software FM-1, or the unit with --hw) against the Windows TTS
   original of the same word: spectral flatness (noise-like = high, "metallic" line spectrum = low), centroid, and
   the 1/3-octave band shape.
2. Recognition: words that differ mainly in one consonant, each in a carrier sentence ("Now say ship again."),
   transcribed by Whisper; a hit = the target word is heard. Grouped by consonant class.

    python speech/consonants.py [--opts '{"fric": "cascade"}'] [--hw] [--spectra-only]
"""
import json, pathlib, re, sys
import numpy as np
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import resynth as R, sapi, eval_web as W

OUT = W.OUT / 'consonants'; OUT.mkdir(parents=True, exist_ok=True)
FRIC = {'s': 'see', 'sh': 'she', 'f': 'fee', 'th': 'thin', 'z': 'zoo', 'v': 'vee', 'hh': 'he', 'ch': 'cheese', 'jh': 'jeep'}
WORDS = {                      # target word -> consonant class
    'sip': 'fricative', 'ship': 'fricative', 'zip': 'fricative', 'fit': 'fricative', 'thin': 'fricative', 'van': 'fricative',
    'hat': 'fricative', 'chip': 'affricate', 'jet': 'affricate',
    'pit': 'stop', 'bit': 'stop', 'tip': 'stop', 'dip': 'stop', 'kit': 'stop', 'gap': 'stop',
    'map': 'nasal', 'nap': 'nasal', 'lip': 'liquid', 'rip': 'liquid', 'wet': 'glide', 'yes': 'glide',
    'sun': 'fricative', 'fun': 'fricative', 'shoe': 'fricative', 'zoo': 'fricative', 'cat': 'stop', 'dog': 'stop',
}
SR = 22050


def tonality(x):
    """Peak normalised autocorrelation at 1.5-20 ms lags: ~0 for noise, high for a pitched (metallic) comb."""
    x = x - x.mean()
    ac = np.correlate(x, x, 'full')[len(x) - 1:]
    if ac[0] <= 0: return 0.0
    lo, hi = int(0.0015 * SR), min(len(ac) - 1, int(0.020 * SR))
    return float(ac[lo:hi].max() / ac[0])


def spectrum_stats(x):
    """Flatness (0..1) and centroid over 1-8 kHz of a segment, and its 1/3-octave band levels 500 Hz-8 kHz."""
    if len(x) < 512: return None
    n = 1 << int(np.log2(len(x)))
    s = np.abs(np.fft.rfft(x[:n] * np.hanning(n))) ** 2 + 1e-18
    f = np.fft.rfftfreq(n, 1 / SR)
    m = (f >= 1000) & (f <= 8000)
    flat = float(np.exp(np.mean(np.log(s[m]))) / np.mean(s[m]))
    cent = float((f[m] * s[m]).sum() / s[m].sum())
    cs = 500 * 2 ** (np.arange(13) / 3)
    bands = 10 * np.log10(np.array([s[(f >= c / 2 ** (1 / 6)) & (f < c * 2 ** (1 / 6))].sum() for c in cs]))
    return flat, cent, bands - bands.max()


def fricative_segment(wav, word):
    """The consonant part of a single-word recording: frames before the first voiced frame, above the noise floor."""
    x = R.read_wav(wav); fr = R.analyse(x)
    e = np.array([f['e'] for f in fr]); v = np.array([f['f0'] > 0 for f in fr])
    on = np.where(e > e.max() - 45)[0]
    if not len(on): return None
    a = on[0]
    b = a + (np.argmax(v[a:]) if v[a:].any() else len(fr) - a)
    if word in ('thin',):                    # weak "th": take the 60 ms before voicing
        a = max(a, b - 6)
    if b - a < 3: return None
    hop = int(R.HOP_MS / 1000 * SR)
    return x[a * hop:b * hop]


def spectra(opts, hw=False, tag=''):
    tts = {p: str(OUT / f'tts_{w}.wav') for p, w in FRIC.items()}
    if not all(pathlib.Path(t).exists() for t in tts.values()):
        sapi.say_batch([(FRIC[p] + '.', tts[p]) for p in FRIC])
    jobs = [{'text': w + '.', 'opts': opts, 'wav': str(OUT / f'soft{tag}_{w}.wav'), 'events': str(OUT / f'ev{tag}_{w}.json')} for w in FRIC.values()]
    W.node(jobs)
    rows = []
    print(f"{'':4} {'flatness':>18} {'centroid Hz':>18}  band shape distance (dB)")
    for (p, w), j in zip(FRIC.items(), jobs):
        wav = j['wav']
        if hw:
            wav = j['wav'].replace('soft', 'fm1'); W.play_events(json.loads(pathlib.Path(j['events']).read_text()), wav)
        a, b = fricative_segment(tts[p], w), fricative_segment(wav, w)
        if a is None or b is None: print(f'{p:4} (no segment)'); continue
        (fa, ca, ba), (fb, cb, bb) = spectrum_stats(a), spectrum_stats(b)
        d = float(np.mean(np.abs(np.clip(ba, -40, 0) - np.clip(bb, -40, 0))))
        ta, tb = tonality(a), tonality(b)
        rows.append((p, fa, fb, ca, cb, d, ta, tb))
        print(f'{p:4} tts {fa:5.3f} fm {fb:5.3f}   tts {ca:5.0f} fm {cb:5.0f}   {d:5.1f}     tonality tts {ta:4.2f} fm {tb:4.2f}')
    if rows:
        print(f"mean flatness tts {np.mean([r[1] for r in rows]):.3f} fm {np.mean([r[2] for r in rows]):.3f}; "
              f"mean band distance {np.mean([r[5] for r in rows]):.1f} dB; tonality tts {np.mean([r[6] for r in rows]):.2f} "
              f"fm {np.mean([r[7] for r in rows]):.2f}")
    return rows


SETS = {'ip': 'sip ship zip hip chip tip dip pip lip rip nip whip'.split(),
        'in': 'fin thin sin shin pin tin bin kin win gin din'.split(),
        'ee': 'see she fee he tea bee key knee me lee'.split()}
CLASS = {'s': 'fric', 'sh': 'fric', 'z': 'fric', 'h': 'fric', 'f': 'fric', 't': 'stop', 'd': 'stop', 'p': 'stop', 'b': 'stop',
         'k': 'stop', 'l': 'son', 'r': 'son', 'n': 'son', 'm': 'son', 'w': 'son', 'c': 'affr', 'g': 'affr'}


def closed_set(opts, hw=False, tag=''):
    """Each word alone; Whisper is prompted with the rhyme set, so it picks among them (like a listening test)."""
    from faster_whisper import WhisperModel
    R.whisper(str(OUT / 'tts_see.wav'))                     # load the shared model
    model = R._model
    hits, conf = {}, []
    for name, words in SETS.items():
        jobs = [{'text': w + '.', 'opts': opts, 'wav': str(OUT / f'c{tag}_{w}.wav'), 'events': str(OUT / f'cev{tag}_{w}.json')} for w in words]
        W.node(jobs)
        for w, j in zip(words, jobs):
            wav = j['wav']
            if hw:
                wav = j['wav'].replace(f'c{tag}_', f'cfm1{tag}_'); W.play_events(json.loads(pathlib.Path(j['events']).read_text()), wav)
            segs, _ = model.transcribe(wav, language='en', beam_size=5, condition_on_previous_text=False,
                                       initial_prompt='Words: ' + ', '.join(words) + '.')
            heard = R.words(' '.join(sg.text for sg in segs))
            got = next((h for h in heard if h in words), heard[0] if heard else '')
            cls = 'th/sh/ch' if w[:2] in ('th', 'sh', 'ch', 'wh') else CLASS.get(w[0], 'other')
            hits.setdefault(cls, []).append(got == w); hits.setdefault('ALL', []).append(got == w)
            conf.append((w, got))
    wrong = [f'{w}->{g or "?"}' for w, g in conf if w != g]
    print('closed-set: ' + ', '.join(f'{k} {sum(v)}/{len(v)}' for k, v in hits.items()) + '   confusions: ' + ' '.join(wrong))
    return {k: [sum(v), len(v)] for k, v in hits.items()}


def recognition(opts, hw=False, tag=''):
    jobs = [{'text': f'Now say {w} again.', 'opts': opts, 'wav': str(OUT / f'rsoft{tag}_{w}.wav'), 'events': str(OUT / f'rev{tag}_{w}.json')} for w in WORDS]
    W.node(jobs)
    hits = {}
    for (w, cls), j in zip(WORDS.items(), jobs):
        wav = j['wav']
        if hw:
            wav = j['wav'].replace('rsoft', 'rfm1'); W.play_events(json.loads(pathlib.Path(j['events']).read_text()), wav)
        h = R.words(R.whisper(wav))
        ok = w in h
        hits.setdefault(cls, []).append(ok)
        hits.setdefault('ALL', []).append(ok)
        print(f'  {w:7} {"OK " if ok else "-- "} {" ".join(h)}', flush=True)
    print('recognition: ' + ', '.join(f'{k} {sum(v)}/{len(v)}' for k, v in hits.items()))
    return {k: [sum(v), len(v)] for k, v in hits.items()}


if __name__ == '__main__':
    hw = '--hw' in sys.argv
    opts = json.loads(sys.argv[sys.argv.index('--opts') + 1]) if '--opts' in sys.argv else {}
    tag = '_' + re.sub(r'\W+', '', json.dumps(opts))[:40]
    spectra(opts, hw, tag)
    if '--spectra-only' not in sys.argv:
        closed_set(opts, hw, tag)
    if '--sentences' in sys.argv:
        recognition(opts, hw, tag)
