"""Offline intonation evaluation for the Speech tab (no hardware).

For a set of statements, yes/no questions, wh-questions, exclamations, comma phrases and minimal pairs
("It's raining." / "It's raining?"), compare three intonation models of app/speech.js - flat, the function-word
list rules (pos=false) and the part-of-speech rules (default) - on:
  r      correlation of the planned pitch contour with the TTS voice's own f0 contour (same sentence, time-normalised)
  end    final pitch movement (semitones, last ~12 % of voiced speech vs the stretch before): rise > 0, fall < 0
  ok     whether that end goes the intended way (yes/no question: rise; statement / wh / exclamation: fall)
  real   the same end movement in the keys the FM-1 is actually sent (whole semitones, one per 20 ms note)
  ?      whether Whisper puts a question mark on the render (it hears intonation, not only words)
  words  Whisper word accuracy on the render (intonation should not cost intelligibility)

    python speech/eval_prosody.py
"""
import json, pathlib, re, sys
import numpy as np
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import resynth as R, sapi, eval_web as W

OUT = W.OUT / 'prosody'; OUT.mkdir(parents=True, exist_ok=True)
SENTS = {
    'stmt': ['The cat sat on the mat.', 'I bought a new guitar yesterday.', 'She plays the drums in a band.',
             'We will record a new album.', 'The train leaves at nine.'],
    'yn': ['Can you hear me?', 'Is it raining?', 'Do you like this song?', 'Are we there yet?', 'Will you record the show?'],
    'wh': ['Where is the station?', 'What time is it?', 'Why did you leave?', 'Who wrote this song?', 'How does it work?'],
    'excl': ['That was amazing!', 'What a great idea!', 'Watch out for the car!'],
    'comma': ['When the music stops, everyone sits down.', 'If you want, we can go now.', 'I like red, green, and blue.'],
    'pair': ["It's raining.", "It's raining?", 'You finished the song.', 'You finished the song?'],
}
RISE = lambda s: s.endswith('?') and not re.match(r'(what|where|when|why|who|how|which)\b', s.lower())
VARIANTS = {'flat': {'tone': 'flat'}, 'list': {'pos': False}, 'pos': {}}


def f0_semis(frames):
    f = np.array([x['f0'] for x in frames], float)
    e = np.array([x['e'] for x in frames])
    f[e < e.max() - 40] = 0
    v = f > 0
    if v.sum() < 5: return np.full(len(f), np.nan)
    s = np.full(len(f), np.nan)
    s[v] = 12 * np.log2(f[v] / np.median(f[v]))
    for i in np.where(v)[0]:                                        # 5-frame median against octave slips
        w = s[max(0, i - 2):i + 3]; w = w[~np.isnan(w)]
        s[i] = np.clip(np.median(w), -12, 12)
    return s


def norm(track, n=100):
    """Voiced span -> n points (linear over voiced frames), centred."""
    t = np.asarray(track, float)
    idx = np.where(~np.isnan(t))[0]
    if len(idx) < 5: return None
    x = (idx - idx[0]) / max(1, idx[-1] - idx[0])
    y = np.interp(np.linspace(0, 1, n), x, t[idx])
    return y - y.mean()


def end_move(track):
    """Final 15 % of the voiced span against the rest: > 0 ends high (rise), < 0 ends low (fall)."""
    y = norm(track)
    if y is None: return np.nan
    return float(y[85:].mean() - y[:85].mean())


def key_track(keys):
    """Keys the FM-1 actually plays for voiced frames (one per 20 ms note), as semitones."""
    k = [float(key) for _, key, v in keys if v]
    return np.array(k) if len(k) >= 5 else np.full(5, np.nan)


def main():
    items = [(k, s) for k, ss in SENTS.items() for s in ss]
    tag = lambda s: re.sub(r'\W+', '_', s).strip('_') + ('_q' if s.endswith('?') else '')
    tts = {s: str(OUT / f'tts_{tag(s)}.wav') for _, s in items}
    if not all(pathlib.Path(p).exists() for p in tts.values()): sapi.say_batch([(s, p) for s, p in tts.items()])
    ref = {s: f0_semis(R.analyse(R.read_wav(p))) for s, p in tts.items()}
    tts_q = {s: R.whisper(p).strip().endswith('?') for s, p in tts.items()}
    rows = []
    for vname, opts in VARIANTS.items():
        jobs = [{'text': s, 'opts': opts, 'wav': str(OUT / f'{vname}_{tag(s)}.wav')} for _, s in items]
        res = W.node(jobs)
        for (kind, s), j, r in zip(items, jobs, res):
            plan = [np.nan if c is None else c for c in r['contour']]
            a, b = norm(plan), norm(ref[s])
            corr = float(np.corrcoef(a, b)[0, 1]) if a is not None and b is not None and a.std() > 1e-6 else 0.0
            end = end_move(plan)
            real = end_move(key_track(r['keys']))
            h = R.whisper(j['wav']).strip(); m, n = R.score(s, h)
            want_rise = RISE(s)
            rows.append(dict(v=vname, kind=kind, s=s, r=corr, end=end, ok=(end > 0.5) if want_rise else (end < -0.5),
                             real=real, real_ok=(real > 0.5) if want_rise else (real < -0.5), q=h.endswith('?'), want_q=s.endswith('?'),
                             hit=m, tot=n, heard=h, tts_end=end_move(ref[s]), words=r['words']))
    (OUT / 'results.json').write_text(json.dumps(rows, indent=1, default=float))
    # report
    print(f"{'':6} {'kind':6} {'r':>6} {'ok':>6} {'real':>6} {'?-hit':>6} {'words':>7}")
    for vname in VARIANTS:
        for kind in list(SENTS) + ['ALL']:
            rr = [x for x in rows if x['v'] == vname and (kind == 'ALL' or x['kind'] == kind)]
            qh = [x['q'] == x['want_q'] for x in rr]
            print(f"{vname:6} {kind:6} {np.mean([x['r'] for x in rr]):6.2f} {np.mean([x['ok'] for x in rr]):6.0%} "
                  f"{np.mean([x['real_ok'] for x in rr]):6.0%} {np.mean(qh):6.0%} {sum(x['hit'] for x in rr):3d}/{sum(x['tot'] for x in rr):<3d}")
        print()
    tts_rise = {s: end_move(ref[s]) for _, s in items}
    print('TTS voice itself: end movement right way',
          f"{np.mean([(tts_rise[s] > 0.5) if RISE(s) else (tts_rise[s] < -0.5) for _, s in items]):.0%},",
          f"Whisper '?' correct {np.mean([tts_q[s] == s.endswith('?') for _, s in items]):.0%}")
    for x in rows:
        if x['v'] == 'pos': print(f"  {x['kind']:5} {x['s']:42} end {x['end']:+5.1f} real {x['real']:+5.1f} tts {x['tts_end']:+5.1f}  r {x['r']:+.2f}  heard {x['heard']!r}")


if __name__ == '__main__':
    main()
