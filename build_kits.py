"""Build factory drum kits from the sysexFinal library -> app/library/kits.json.

1. Pick voices per role with strict name rules (no melodic percussion, no 'BD&SNR' combo patches).
2. Assemble kits: one per coherent drum bank, plus themed kits across the library.
3. (default) Play every chosen voice on the FM-1 and record it: drop voices that are silent at their key, record
   level/tail/pitch, and compute a per-voice 'level' macro (carrier OL steps, ~0.75 dB each) that balances each kit.
   Voices are loaded with parameter diffs (full VCED dumps stall the unit). Run with --no-measure to skip hardware.
"""
import base64, json, pathlib, re, sys, time, collections
import numpy as np

ROOT = pathlib.Path(__file__).parent
sys.path.insert(0, str(ROOT))
import dx7

IDX = json.loads((ROOT / 'app' / 'library' / 'index.json').read_text())
BANKS, VOICES = IDX['banks'], IDX['voices']

MELODIC = re.compile(r'MARIMB|LIMBA|GLOC|XYLO|VIBE|GLOCK|PIANO|PNO|BELLS?\b|CHIME|FLUTE|ORGAN|STRING|BRASS|BASS\s*$|BASS [0-9]|GUITAR|HARP|CELEST|KALIMB|STEEL ?DR|EP\b|VOICE|CHOIR')
COMBO = re.compile(r'&|/|\+|BD-?SD|KICK-?SN|BS&SN|KICK ?& ?|DRUMS\b|KIT\b|E\. ?DRUMS|ELEC\.? ?DRUMS|5BD')
ROLE_RX = {
    'kick':  re.compile(r'\b(KICK|KIK|BD|B\.? ?DRUM|BASS ?DR(UM)?\.?|BASSDR)'),
    'snare': re.compile(r'(SNARE|SNR|\bSD\b|SN\.? ?DR|RIM ?SHOT)'),
    'hat':   re.compile(r'(HI-?HAT|HIHAT|\bHH\b|\bHAT\b|CL\.? ?HI|CLOSED)'),
    'open':  re.compile(r'(OPEN|CYMB|CRASH|RIDE|SIZ|SPLASH|CHINA)'),
    'tom':   re.compile(r'(\bTOMS?\b|TOM-|TOM ?[0-9]|ROTO|TIMBAL|CONGA|BONGO|TABLA|HAND ?DR|LOG ?DR|TIMPANI|SYN ?DR|SYNDRUM)'),
    'perc':  re.compile(r'(CLAP|COW ?BEL|COWB|CLAVE|WOOD ?BL|W\.? ?BLOCK|BLOCK|SHAK|TAMB|AGOGO|CABASA|GUIRO|TRIANG|RIM\b|METAL|PERC|ZAP|NOISE)'),
}
DEFAULT_NOTE = {'kick': 36, 'snare': 38, 'hat': 54, 'open': 54, 'tom': 45, 'perc': 60}
TRACKS = [('Kick', 'kick', 120), ('Snare', 'snare', 140), ('Hat', 'hat', 50), ('Open hat', 'open', 400), ('Tom', 'tom', 250), ('Perc', 'perc', 150)]
NOTE_RX = re.compile(r'([A-G])(#?)(-?[0-7])\s*$')


def role_of(name):
    u = name.upper()
    if MELODIC.search(u) or COMBO.search(u):
        return None
    for r in ('open', 'hat', 'kick', 'snare', 'tom', 'perc'):      # open before hat: 'OPEN HIHAT'
        if ROLE_RX[r].search(u):
            return r
    return None


def note_hint(name):
    m = NOTE_RX.search(name.strip().upper())
    if not m:
        return None
    n = 'C C# D D# E F F# G G# A A# B'.split().index(m.group(1) + m.group(2))
    return n + 12 * (int(m.group(3)) + 1)


def cand(i):
    n, b, s, v, t = VOICES[i]
    return {'lib': i, 'name': n.strip(), 'bank': BANKS[b], 'role': role_of(n), 'note': note_hint(n), 'b64': v}


def pick(pool, role, rng=None, prefer=None):
    c = [x for x in pool if x['role'] == role]
    if prefer:
        c.sort(key=lambda x: -bool(prefer.search(x['name'].upper())))
    return c[0] if c else None


def build_kits():
    by_bank = collections.defaultdict(list)
    for i in range(len(VOICES)):
        c = cand(i)
        if c['role']:
            by_bank[c['bank']].append(c)
    kits = []
    # 1) coherent bank kits
    for bank, pool in by_bank.items():
        roles = {r for r in (x['role'] for x in pool)}
        if not {'kick', 'snare'} <= roles or not roles & {'hat', 'open'}:
            continue
        tr = []
        for label, role, gate in TRACKS:
            v = pick(pool, role) or (pick(pool, 'hat') if role == 'open' else None)
            if v:
                tr.append((label, role, gate, v))
        kits.append({'name': bank.split('/')[-1], 'source': bank, 'tracks': tr})
    # 2) themed kits across the library (first matches in library order; deterministic)
    allc = [c for pool in by_bank.values() for c in pool]
    themes = {
        'Syndrum Electro': re.compile(r'SYN|ELEC|E\.|SIM|707|808|SDS|ZAP|NOISE'),
        'Acoustic': re.compile(r'AC\.|ACOUS|REAL|NATURAL|STUDIO|JAZZ|BRUSH|ROCK'),
        'Metal & Industrial': re.compile(r'METAL|STL|STEEL|ANVIL|IRON|CLANK|IND'),
        'Latin & Hand': re.compile(r'CONGA|BONGO|TIMBAL|AGOGO|CABASA|GUIRO|CLAVE|TABLA|HAND|SHAK|TAMB|COW'),
    }
    for tname, rx in themes.items():
        tr = []
        for label, role, gate in TRACKS:
            pool = [c for c in allc if c['role'] == role and rx.search(c['name'].upper())] or \
                   [c for c in allc if c['role'] == role]
            used = {x[3]['lib'] for k in kits for x in k['tracks']}
            v = next((c for c in pool if c['lib'] not in used), pool[0] if pool else None)
            if v:
                tr.append((label, role, gate, v))
        kits.append({'name': tname, 'source': 'themed: ' + rx.pattern, 'tracks': tr})
    return kits


# ---------------- hardware measurement ----------------
def measure(kits):
    import mido
    from fm1 import out_port, recording, SR
    uniq = {}
    for k in kits:
        for label, role, gate, v in k['tracks']:
            uniq[v['lib']] = v
    items = list(uniq.values())
    print(f'measuring {len(items)} voices on the FM-1 (~{len(items) * 1.1:.0f} s)...', flush=True)
    dev = None
    marks = []
    with out_port() as o:
        for n in range(128):
            o.send(mido.Message('note_off', note=n))
        with recording(seconds_max=len(items) * 1.2 + 5) as rec:
            t0 = time.perf_counter()
            for it in items:
                vced = dx7.vmem_to_vced(base64.b64decode(it['b64'])) + [63]
                for i in list(range(145)) + [155]:
                    if dev is None or dev[i] != vced[i]:
                        o.send(mido.Message('sysex', data=[0x43, 0x10, (i >> 7) & 3, i & 127, vced[i]]))
                dev = vced
                note = it['note'] or DEFAULT_NOTE[it['role']]
                time.sleep(0.03)
                t = time.perf_counter() - t0
                o.send(mido.Message('note_on', note=note, velocity=100)); time.sleep(0.15)
                o.send(mido.Message('note_off', note=note)); time.sleep(0.92)
                marks.append((it, note, t))
        for n in range(128):
            o.send(mido.Message('note_off', note=n))
    a = rec['audio']                                  # fm1.recording already returns mono
    (ROOT / 'dumps').mkdir(exist_ok=True)            # git-ignored, so absent on a fresh clone
    np.save(ROOT / 'dumps' / 'kit_measure.npy', a)
    env = np.sqrt(np.convolve(a ** 2, np.ones(220) / 220, 'same'))
    # calibrate send->audio offset from the first clearly audible onsets
    offs = []
    for it, note, t in marks[:20]:
        i = np.flatnonzero(env[int(t * SR):int((t + 0.5) * SR)] > 10 ** (-65 / 20))
        if len(i): offs.append(i[0] / SR)
    off = float(np.median(offs)) if offs else 0.05
    for it, note, t in marks:
        s = int((t + off) * SR)
        body = a[s:s + int(0.12 * SR)]
        tail = env[s:s + int(1.0 * SR)]
        lvl = 20 * np.log10(np.sqrt(np.mean(body ** 2)) + 1e-9)
        floor = 10 ** (-92 / 20)                              # USB noise floor is ~-100 dBFS
        # decay = first drop to 20 dB below the peak: direct sound dominates it (the unit's delay/reverb echoes don't)
        pk = int(np.argmax(tail)); below = np.flatnonzero(tail[pk:] < max(tail[pk] * 0.1, floor))
        above = np.arange(pk + (below[0] if len(below) else len(tail) - pk))
        mag = np.abs(np.fft.rfft(a[s:s + 4096] * np.hanning(len(a[s:s + 4096]))))
        f = np.fft.rfftfreq(len(a[s:s + 4096]), 1 / SR); mag[f < 25] = 0
        it['measured'] = {'note': note, 'level_db': round(float(lvl), 1), 'decay_ms': int(len(above) / SR * 1000),
                          'pitch_hz': round(float(f[np.argmax(mag)]), 0), 'centroid_hz': round(float((f * mag).sum() / (mag.sum() + 1e-9)))}
    print(f'  audio offset {off*1000:.0f} ms')
    return uniq


ROLE_TARGET = {'kick': 0, 'snare': -1, 'tom': -2, 'perc': -4, 'hat': -7, 'open': -8}   # dB relative to kit kick


def carrier_headroom(v):
    vced = dx7.vmem_to_vced(base64.b64decode(v['b64']))
    alg = vced[134]
    return min(99 - vced[(6 - c) * 21 + 16] for c in dx7.CARRIERS[alg])


def main():
    kits = build_kits()
    measured = measure(kits) if '--no-measure' not in sys.argv else {}
    out = []
    for k in kits:
        tracks = []
        for label, role, gate, v in k['tracks']:
            m = measured.get(v['lib'], {}).get('measured')
            if m and m['level_db'] < -85:
                print(f'  drop {v["name"]!r} ({k["name"]}): silent at note {m["note"]}')
                continue
            tracks.append({'name': label, 'role': 'hat' if role == 'open' else role, 'gate': gate,
                           'note': (m or {}).get('note') or v['note'] or DEFAULT_NOTE[role],
                           'voice': {'name': v['name'], 'lib': v['lib'], 'b64': v['b64'], 'measured': m}})
        if not any(t['role'] == 'kick' for t in tracks) or len(tracks) < 4:
            continue
        # balance: level macro (OL steps) so each role sits at its target relative to the kick
        if measured:
            # Reference = kick level, lowered (max 12 dB) until every voice can reach its role target, since quiet voices
            # can only be boosted by their carrier headroom.
            kick = next(t for t in tracks if t['role'] == 'kick')['voice']['measured']['level_db']
            reach = [t['voice']['measured']['level_db'] + carrier_headroom(t['voice']) * 0.75
                     - ROLE_TARGET['open' if t['name'] == 'Open hat' else t['role']] for t in tracks]
            ref = max(min([kick] + reach), kick - 12)
            for t in tracks:
                role = 'open' if t['name'] == 'Open hat' else t['role']
                want = ref + ROLE_TARGET[role]
                steps = round((want - t['voice']['measured']['level_db']) / 0.75)
                steps = max(-40, min(carrier_headroom(t['voice']), steps))
                t['macros'] = {'level': steps} if steps else {}
        out.append({'name': k['name'], 'source': k['source'], 'tracks': tracks})
    path = ROOT / 'app' / 'library' / 'kits.json'
    path.write_text(json.dumps({'kits': out, 'measured': bool(measured)}, indent=1))
    print(f'\n{len(out)} kits -> {path}')
    for k in out:
        print(f"  {k['name']:28s} " + ' | '.join(
            f"{t['name']}: {t['voice']['name']}" + (f" ({t['voice']['measured']['level_db']:.0f} dB, {t['voice']['measured']['decay_ms']} ms)" if t['voice'].get('measured') else '')
            for t in k['tracks']))


if __name__ == '__main__':
    main()
