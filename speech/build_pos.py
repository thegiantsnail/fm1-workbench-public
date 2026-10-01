"""Part-of-speech data for the Speech tab (app/speech/pos.json): a small HMM tagger learned from the Brown corpus
(universal tagset, via nltk) plus noun/verb stress pairs from CMUdict (REcord / reCORD).

    python speech/build_pos.py          # needs: pip install nltk; nltk.download('brown'); nltk.download('universal_tagset')

pos.json = {tags, start[t], trans[t][u] (log-probs x100, rounded), lex {word: "N7V3"...}, hetero {word: [noun, verb]}}
lex holds P(tag | word) in tenths for words seen at least twice; the app divides by P(tag) to get emissions.
"""
import collections, json, math, pathlib, re, random
import pronouncing
from nltk.corpus import brown

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / 'app' / 'speech' / 'pos.json'
TAGS = ['NOUN', 'VERB', 'ADJ', 'ADV', 'PRON', 'DET', 'ADP', 'CONJ', 'PRT', 'NUM', '.', 'X']
CODE = dict(zip(TAGS, 'NVARPDICTM.X'))


def train(sents):
    wt = collections.defaultdict(collections.Counter)
    tr = collections.defaultdict(collections.Counter)
    st = collections.Counter()
    for s in sents:
        prev = None
        for w, t in s:
            w = w.lower()
            wt[w][t] += 1
            if prev is None: st[t] += 1
            else: tr[prev][t] += 1
            prev = t
    return wt, tr, st


def model(wt, tr, st):
    lg = lambda c, tot: round(100 * math.log((c + 0.5) / (tot + 0.5 * len(TAGS))))
    start = [lg(st[t], sum(st.values())) for t in TAGS]
    trans = [[lg(tr[a][b], sum(tr[a].values())) for b in TAGS] for a in TAGS]
    lex = {}
    for w, c in wt.items():
        n = sum(c.values())
        if n < 2 or not re.fullmatch(r"[a-z][a-z'\-]*", w): continue
        parts = [(t, round(10 * k / n)) for t, k in c.most_common() if round(10 * k / n) >= 1]
        lex[w] = ''.join(f'{CODE[t]}{min(9, p)}' for t, p in parts)
    tagp = collections.Counter()
    for c in wt.values(): tagp.update(c)
    prior = [round(100 * math.log(tagp[t] / sum(tagp.values()))) for t in TAGS]
    return {'tags': TAGS, 'codes': [CODE[t] for t in TAGS], 'start': start, 'trans': trans, 'prior': prior, 'lex': lex}


def heteronyms(lex):
    pronouncing.init_cmu()
    prons = collections.defaultdict(list)
    for w, p in pronouncing.pronunciations:
        p = p.split('#')[0].strip()
        if p and p not in prons[w]: prons[w].append(p)
    out = {}
    for w, ps in prons.items():
        tags = lex.get(w, '')
        if len(ps) < 2 or 'N' not in tags or 'V' not in tags: continue
        def stress_at(p):
            vs = [x for x in p.split() if x[-1].isdigit()]
            return next((i for i, x in enumerate(vs) if x.endswith('1')), 99), len(vs)
        sa = sorted(ps, key=lambda p: stress_at(p)[0])
        (i0, n0), (i1, n1) = stress_at(sa[0]), stress_at(sa[-1])
        if i0 < i1 and n0 == n1 and n0 >= 2:           # same syllables, stress moves: noun early, verb late
            out[w] = [sa[0].lower(), sa[-1].lower()]
    return out


def guess_tags(w):
    """Unknown words by suffix (same rules as app/speech.js)."""
    w = w.lower()
    if re.search(r'ly$', w): return ('ADV',)
    if re.search(r'(ing|ed|ize|ise|ate|en)$', w): return ('VERB', 'ADJ', 'NOUN')
    if re.search(r'(ous|ful|ive|able|ible|al|ic|ish|less|ary|ent|ant)$', w): return ('ADJ',)
    if re.search(r'\d', w): return ('NUM',)
    return ('NOUN',)


def viterbi(m, words):
    """Same decoder as app/speech.js (for the held-out accuracy check)."""
    T = len(m['tags'])
    def emit(w):
        c = m['lex'].get(w.lower())
        if not c:
            g = guess_tags(w)
            return [0 if m['tags'][t] in g else -300 for t in range(T)]
        d = {c[i]: int(c[i + 1]) for i in range(0, len(c), 2)}
        return [100 * math.log(d[m['codes'][t]] / 10) - m['prior'][t] if m['codes'][t] in d else -900 for t in range(T)]
    V = [[m['start'][t] + e for t, e in enumerate(emit(words[0]))]]; B = []
    for w in words[1:]:
        e = emit(w); row, back = [], []
        for t in range(T):
            k = max(range(T), key=lambda u: V[-1][u] + m['trans'][u][t])
            row.append(V[-1][k] + m['trans'][k][t] + e[t]); back.append(k)
        V.append(row); B.append(back)
    t = max(range(T), key=lambda u: V[-1][u]); path = [t]
    for back in reversed(B): t = back[t]; path.append(t)
    return [m['tags'][t] for t in reversed(path)]


if __name__ == '__main__':
    sents = list(brown.tagged_sents(tagset='universal'))
    random.Random(1).shuffle(sents)
    test, train_s = sents[:1000], sents[1000:]
    m = model(*train(train_s))
    ok = tot = 0
    for s in test[:400]:
        s = [(w, t) for w, t in s if t != '.']            # the app tags words only (punctuation is prosody)
        if not s: continue
        pred = viterbi(m, [w for w, _ in s])
        ok += sum(p == t for p, (_, t) in zip(pred, s)); tot += len(s)
    print(f'held-out tagging accuracy {ok / tot:.1%} ({tot} words)')
    m = model(*train(sents))
    m['hetero'] = heteronyms(m['lex'])
    OUT.write_text(json.dumps(m, separators=(',', ':')))
    print(f"{len(m['lex'])} words, {len(m['hetero'])} noun/verb stress pairs, {OUT.stat().st_size // 1024} KB",
          'e.g.', {k: m['hetero'][k] for k in list(m['hetero'])[:3]}, 'record' in m['hetero'])
