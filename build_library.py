"""Index a folder of DX7 .syx banks into app/library/index.json: deduplicated voices with bank, slot and category tags.
Dedup key = the 118 sound bytes of the packed voice (name ignored), so renamed copies collapse.

    python build_library.py [folder]        # default: sysexFinal/ (your own banks; not part of the repository)
"""
import json, re, base64, pathlib, collections, sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import dx7

ROOT = pathlib.Path(__file__).parent
SRC = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / 'sysexFinal'
OUT = ROOT / 'app' / 'library' / 'index.json'
if __name__ == '__main__' and not SRC.is_dir():
    sys.exit(f'{SRC} not found. Put your DX7 .syx banks there (or name another folder); see "Voices" in README.md.')

CATS = [  # (tag, regex on name) - first matches win for 'cat', all matches go to tags
    ('kick',  r'\b(KICK|KIK|BD|B\.?DRUM|BASSDR|BASS ?DRUM)'),
    ('snare', r'(SNARE|SNR|\bSD\b|SN\.?DR|RIMSHOT|\bRIM)'),
    ('hat',   r'(HAT|\bHH\b|HI-?H|CYMB|CRASH|RIDE|CHINA|SPLASH)'),
    ('tom',   r'(\bTOMS?\b|TOM-|TIMPANI|TIMBAL|TIMP\b|CONGA|BONGO|TABLA)'),
    ('perc',  r'(PERC|CLAP|COWB|COW ?BEL|CLAVE|WOOD|BLOCK|SHAK|TAMB|GUIRO|MARIMB|XYLO|VIBES?\b|GLOCK|STEEL ?DR|LOG ?DR|CABASA|AGOGO|TRIANG|GONG|ZAP|DRUM|KIT\b|NOISE)'),
    ('bass',  r'(BASS|\bBS\b|SUB)'),
    ('keys',  r'(PIANO|PNO|E\.?P\b|RHODE|WURL|CLAV|HARPS|CELEST|KEY)'),
    ('organ', r'(ORGAN|ORG\b|HAMMOND|PIPE)'),
    ('brass', r'(BRASS|BRS|HORN|TRUMP|TRMPT|TROMB|TUBA|SAX)'),
    ('strings', r'(STRING|STRNG|STR\b|VIOLIN|CELLO|VIOLA|ORCH)'),
    ('pad',   r'(PAD|CHOIR|VOICE|VOX|AAH|OOH|SWEEP|WARM|ATMOS|SPACE)'),
    ('bell',  r'(BELL|CHIME|TINE|GLASS|CRYSTAL)'),
    ('lead',  r'(LEAD|SOLO|SYNC|SQUARE|SAW|MOOG)'),
    ('pluck', r'(PLUCK|GUIT|GTR|HARP\b|KOTO|SITAR|BANJO|MANDOL|PIZZ)'),
    ('wind',  r'(FLUTE|FLT|CLARI|OBOE|BASSOON|PAN ?PIPE|WHISTLE|RECORD|OCARIN)'),
    ('fx',    r'(\bFX\b|EFFECT|LASER|WIND\b|STORM|RAIN|BIRD|HELICOP|EXPLO|SIREN|SFX|WEIRD)'),
]
CATS = [(t, re.compile(r)) for t, r in CATS]
DRUM_TAGS = {'kick', 'snare', 'hat', 'tom', 'perc'}

def voices_from(buf):
    """Yield (slot, 128-byte packed voice) from a VMEM bank file (tolerates short/odd files)."""
    i = buf.find(b'\xf0\x43')
    data = None
    if i >= 0 and len(buf) >= i + 6 + 4096 and buf[i + 3] == 0x09:
        data = buf[i + 6:i + 6 + 4096]
    elif len(buf) >= 4096:
        data = buf[-4096 - 2:-2] if buf[-1] == 0xF7 else buf[:4096]
    if data is None:
        return
    for k in range(32):
        v = bytes(b & 0x7F for b in data[k * 128:(k + 1) * 128])
        yield k, v

def sound_key(v):
    """Dedup key: the 145 sound parameters after unpacking and clamping to DX7 ranges, exactly as the app sees them.
    Packed bytes alone miss duplicates that differ only in unused bits or out-of-range values the app normalises."""
    vc = dx7.vmem_to_vced(v)
    return tuple(min(x, dx7.OP_MAX[dx7.OP_FIELDS[i % 21]]) if i < 126 else min(x, dx7.G_MAX[dx7.GLOBAL_FIELDS[i - 126]])
                 for i, x in enumerate(vc[:145]))


def remap_kits(rows):
    """Keep kits.json's library indices pointing at the right rows after a rebuild."""
    path = OUT.parent / 'kits.json'
    if not path.exists():
        return
    where = {sound_key(base64.b64decode(r[3])): i for i, r in enumerate(rows)}
    data = json.loads(path.read_text())
    for k in data['kits']:
        for t in k['tracks']:
            t['voice']['lib'] = where.get(sound_key(base64.b64decode(t['voice']['b64'])), -1)
    path.write_text(json.dumps(data, indent=1))


def main():
    seen, rows, banks = {}, [], []
    stats = collections.Counter()
    files = sorted(SRC.rglob('*.syx'), key=lambda p: str(p).lower())
    for f in files:
        bank = str(f.relative_to(SRC).with_suffix('')).replace('\\', '/')
        buf = f.read_bytes()
        got = list(voices_from(buf))
        if not got:
            stats['unreadable files'] += 1; continue
        banks.append(bank); bi = len(banks) - 1
        for slot, v in got:
            stats['voices'] += 1
            key = sound_key(v)
            if key in seen:
                stats['duplicates'] += 1
                continue
            name = v[118:128].decode('ascii', 'replace')
            if not name.strip() or all(x == 0 for x in v[:118]):
                stats['empty'] += 1; continue
            up = name.upper()
            tags = [t for t, rx in CATS if rx.search(up)]
            if 'bass' in tags and ('kick' in tags or 'BASS DR' in up or 'BASSDR' in up):
                tags.remove('bass')
            seen[key] = len(rows)
            rows.append([name, bi, slot, base64.b64encode(v).decode(), tags])
            for t in tags: stats['tag ' + t] += 1
            if DRUM_TAGS & set(tags): stats['drum voices'] += 1
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({'source': SRC.name, 'banks': banks, 'voices': rows}, separators=(',', ':')))
    print(f'{len(files)} files -> {stats["voices"]} voices, {stats["duplicates"]} duplicates, {stats["empty"]} empty, '
          f'{len(rows)} unique ({stats["unreadable files"]} unreadable files)')
    print('drum voices:', stats['drum voices'], '|', ', '.join(f'{k[4:]} {v}' for k, v in sorted(stats.items()) if k.startswith('tag ')))
    print(f'wrote {OUT} ({OUT.stat().st_size / 1e6:.1f} MB)')
    remap_kits(rows)

if __name__ == '__main__':
    main()
