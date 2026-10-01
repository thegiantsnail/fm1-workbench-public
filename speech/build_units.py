"""Build the diphone unit library for typed-text speech (app/speech/units.json) and the compact pronouncing dictionary
(app/speech/cmudict.txt).

Windows SAPI speaks a corpus chosen to cover English diphones and reports phoneme timings; each recording is analysed
(resynth.analyse: energy, voicing, F1-F3 + levels, centroid per 10 ms) and cut into diphones (middle of one phone to
the middle of the next). For every diphone the instance closest to the median length is kept, plus half-phones as a
fallback for diphones the corpus never produced.

    python speech/build_units.py
"""
import collections, json, pathlib, re, sys
import numpy as np
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import resynth as R, sapi
import pronouncing

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / 'app' / 'speech'; OUT.mkdir(parents=True, exist_ok=True)
WORK = ROOT / 'dumps' / 'units'; WORK.mkdir(parents=True, exist_ok=True)
PH = ['_', 'aa', 'ae', 'ah', 'ao', 'aw', 'ax', 'ay', 'b', 'ch', 'd', 'dh', 'eh', 'er', 'ey', 'f', 'g', 'hh', 'ih', 'iy', 'jh', 'k',
      'l', 'm', 'n', 'ng', 'ow', 'oy', 'p', 'r', 's', 'sh', 't', 'th', 'uh', 'uw', 'v', 'w', 'y', 'z', 'zh']

# Everyday sentences first (natural co-articulation for common words), then greedy diphone coverage.
SENTENCES = """hello world. one two three four five six seven eight nine ten.
the quick brown fox jumps over the lazy dog. i am a synthesizer. play it again. turn up the bass.
what time is it? how are you today? thank you very much. good morning, good evening, good night.
please say that again slowly. this is my little music machine. can you hear me now? yes, no, maybe.
she sells sea shells by the sea shore. zero eleven twelve twenty thirty forty fifty hundred thousand million.
the weather is nice and warm. we should go outside and play. my voice is made of sine waves.
where is the nearest train station? it was a dark and stormy night. welcome to the future of sound.
do you want to dance with me? everything is going to be fine. listen to the rhythm of the drums.
the red car stopped at the corner. put the book on the table. which way did they go? january february march april may june july august september october november december.
monday tuesday wednesday thursday friday saturday sunday. a b c d e f g h i j k l m n o p q r s t u v w x y z.""".replace('\n', ' ')


def cmu():
    pronouncing.init_cmu()
    d = {}
    for w, p in pronouncing.pronunciations:
        p = p.split('#')[0].strip()                  # some entries carry comments ("# name", "# foreign")
        if re.fullmatch(r"[a-z']+", w) and w not in d and p:
            d[w] = p
    return d


def arpa(p):
    """CMU phones -> our phone set (AH0 -> ax, stress dropped)."""
    return ['ax' if x == 'AH0' else re.sub(r'\d', '', x).lower() for x in p.split()]


def corpus(d, n_words=3200, want=4):
    have = collections.Counter()
    for s in re.split(r'(?<=[.?!])\s+', SENTENCES):
        for w in re.findall(r"[a-z']+", s.lower()):
            if w in d:
                ph = ['_'] + arpa(d[w]) + ['_']
                have.update(zip(ph, ph[1:]))
    cands = [w for w in d if 3 <= len(w) <= 8 and "'" not in w]
    picked = []
    # prefer words whose spelling looks ordinary (SAPI pronounces them like the dictionary)
    for _ in range(n_words):
        best, gain = None, 0
        for w in cands[::3] if len(picked) < 300 else cands[::7]:
            ph = arpa(d[w])
            g = sum(1 for dp in zip(ph, ph[1:]) if have[dp] < want)
            if g > gain: best, gain = w, g
        if not best: break
        picked.append(best); cands.remove(best)
        ph = arpa(d[best]); have.update(zip(ph, ph[1:]))
    lines = [s.strip() for s in re.split(r'(?<=[.?!])\s+', SENTENCES) if s.strip()]
    lines += [', '.join(picked[i:i + 6]) + '.' for i in range(0, len(picked), 6)]
    return lines


def align(ev, fr, gap_frames=12, pad=6):
    """Phone segments [(phone, first frame, end frame)] for one recording.
    SAPI's phoneme *positions* drift against the audio (up to ~250 ms late, measured), but its phone order, pauses and
    relative durations are right. So: find the speech islands in the audio (energy within 45 dB of the loudest frame;
    gaps shorter than 120 ms, e.g. stop closures, are bridged), match them one-to-one with the phone groups between
    SAPI's pauses, and spread each group's phones over its island in proportion to their reported durations."""
    if not ev: return None
    top = max(f['e'] for f in fr)
    on = [f['e'] > top - 45 for f in fr]
    isl, k = [], 0
    while k < len(on):
        if on[k]:
            j = k
            while j < len(on) and on[j]: j += 1
            if isl and k - isl[-1][1] < gap_frames: isl[-1][1] = j
            else: isl.append([k, j])
            k = j
        else:
            k += 1
    isl = [x for x in isl if x[1] - x[0] >= 5]
    groups, cur = [], []
    for p, s, d in ev:
        if p == '_':
            if cur: groups.append(cur); cur = []
        else:
            cur.append((p, d))
    if cur: groups.append(cur)
    if len(groups) != len(isl): return None
    segs = []
    for gi, (g, (a, b)) in enumerate(zip(groups, isl)):
        before = a - (isl[gi - 1][1] if gi else 0)
        segs.append(('_', a - min(pad, before), a))              # a little pause either side, as context
        tot, t = sum(d for _, d in g), float(a)
        for p, d in g:
            n = d / tot * (b - a)
            segs.append((p, int(round(t)), max(int(round(t)) + 1, int(round(t + n))))); t += n
        after = (isl[gi + 1][0] if gi + 1 < len(isl) else len(fr)) - b
        segs.append(('_', b, b + min(pad, after)))
    return [s for s in segs if s[2] > s[1]]


def build():
    d = cmu()
    lines = corpus(d)
    print(len(lines), 'corpus lines')
    jobs = [(t, str(WORK / f'c{i:04d}.wav')) for i, t in enumerate(lines)]
    cache = WORK / 'events.json'                 # SAPI output is deterministic: reuse recordings + timings
    old = json.loads(cache.read_text()) if cache.exists() else {}
    events = []
    todo = [j for j in jobs if j[0] not in old or not pathlib.Path(j[1]).exists()]
    for k in range(0, len(todo), 60):
        for (t, _), ev in zip(todo[k:k + 60], sapi.say_batch(todo[k:k + 60])): old[t] = ev
        print(f'  spoke {min(k + 60, len(todo))}/{len(todo)}', flush=True)
    cache.write_text(json.dumps(old))
    events = [old[t] for t, _ in jobs]
    inst = collections.defaultdict(list)     # (a, b) -> list of frame arrays
    half = collections.defaultdict(list)     # phone -> list of (frames, mid index)
    emax = None
    all_frames = []
    for (t, wav), ev in zip(jobs, events):
        x = R.read_wav(wav)
        fr = R.analyse(x)
        # high-band share per frame (energy above 4 kHz vs all, dB): hiss in voiced fricatives (z, v, zh, dh)
        win, hop = int(0.03 * R.SR), int(R.HOP_MS / 1000 * R.SR)
        freqs = np.fft.rfftfreq(win, 1 / R.SR)
        for k, f in enumerate(fr):
            sp = np.abs(np.fft.rfft(x[k * hop:k * hop + win] * np.hanning(win))) ** 2 + 1e-18
            f['hf'] = 10 * np.log10(sp[freqs > 4000].sum() / sp.sum())
        all_frames.append(fr)
    emax = np.percentile([f['e'] for fr in all_frames for f in fr], 99.5)
    skipped = 0
    for fr, ev in zip(all_frames, events):
        segs = align(ev, fr)
        if not segs:
            skipped += 1; continue
        vec = lambda f: [round(f['e'] - emax), 1 if f['f0'] > 0 else 0, round(f['F'][0]), round(f['F'][1]), round(f['F'][2]),
                         round(f['lev'][0]), round(f['lev'][1]), round(f['lev'][2]), round(f['cent']), round(f['hf'])]
        for (p1, a1, b1), (p2, a2, b2) in zip(segs, segs[1:]):
            m1, m2 = (a1 + b1) // 2, (a2 + b2) // 2
            if m2 > m1:                      # s = frame where the second phone starts
                inst[(p1, p2)].append({'s': b1 - m1, 'f': [vec(f) for f in fr[m1:m2]]})
        for p, a, b in segs:
            if b > a:
                half[p].append({'s': (b - a) // 2, 'f': [vec(f) for f in fr[a:b]]})
    print(f'{skipped} of {len(events)} lines skipped (pauses did not match the phone groups)')
    # Up to K candidates per diphone for unit selection at synthesis time (join cost): the primary is the one closest
    # to the median length (what older readers use); 'alt' holds the next ones by length.
    K = 4
    units = {}
    for (a, b), L in inst.items():
        med = np.median([len(x['f']) for x in L])
        best = sorted(L, key=lambda x: abs(len(x['f']) - med))[:K]
        units[f'{a} {b}'] = dict(best[0], **({'alt': best[1:]} if len(best) > 1 else {}))
    phones = {}
    for p, L in half.items():
        med = np.median([len(x['f']) for x in L])
        phones[p] = min(L, key=lambda x: abs(len(x['f']) - med))
    missing = [f'{a} {b}' for a in PH for b in PH if f'{a} {b}' not in units and a != b]
    print(f'{len(units)} diphones, {len(phones)} phones; {len(missing)} of {len(PH) * (len(PH) - 1)} pairs missing')
    fields = ['e', 'voiced', 'F1', 'F2', 'F3', 'L1', 'L2', 'L3', 'cent', 'hf']
    (OUT / 'units.json').write_text(json.dumps({'hopMs': R.HOP_MS, 'fields': fields, 'source': 'Windows SAPI (Microsoft David), rate -2',
                                                'units': units, 'phones': phones}, separators=(',', ':')))
    # compact dictionary: header = phone symbols (with stress digits); then one line per word, front-coded against the
    # previous word (first char = shared prefix length), a space, and one character per phone (index into the header)
    syms = sorted({x for p in d.values() for x in p.split()})
    code = {x: chr(0x30 + i) for i, x in enumerate(syms)}          # '0' onwards: never a space or newline
    lines, prev = [','.join(syms)], ''
    for w, p in sorted(d.items()):
        k = 0
        while k < min(len(w), len(prev), 9) and w[k] == prev[k]: k += 1
        lines.append(f"{k}{w[k:]} {''.join(code[x] for x in p.split())}"); prev = w
    (OUT / 'cmudict.txt').write_bytes('\n'.join(lines).encode('utf-8'))      # LF only (the app splits on \n)
    print('wrote', OUT / 'units.json', (OUT / 'units.json').stat().st_size // 1024, 'KB;', 'cmudict.txt',
          (OUT / 'cmudict.txt').stat().st_size // 1024, 'KB')


if __name__ == '__main__':
    build()
