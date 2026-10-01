"""FM formant speech on the M-VAVE FM-1 (experiment).

text -> phonemes (CMUdict) -> one note per phoneme, each with its own DX7 voice:
  vowels/sonorants: DX7 algorithm 22 - op6 (ratio 1) modulates carriers op3/op4/op5 placed on the harmonics nearest
                    F1/F2/F3 (Chowning-style FM formants); op1 (+op2) adds the fundamental/voicing body
  fricatives:      high carriers + op6 feedback noise (voiced ones keep some F1 body)
  plosives:        silence (closure) + a short noise burst in the place-of-articulation band
Parameters apply only to the NEXT note on the FM-1 (measured), so every phoneme is a new note; notes overlap so
transitions blend. Consecutive notes alternate between two keys an octave apart; the upper key's voice uses halved
ratios (0.5 = DX7 coarse 0), so the pitch is the same and a note can overlap the previous one without a retrigger.
Never change TRNP for this: the FM-1 matches note-offs with the TRNP in effect at note-off, so the notes would hang.
"""
import re, sys, time, pathlib
import numpy as np, mido
import pronouncing

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import dx7
from fm1 import out_port, recording, SR

# Formants (Hz) - adult male averages (Peterson & Barney / Hillenbrand); diphthongs as (start, end)
VOWELS = {
    'AA': (730, 1090, 2440), 'AE': (660, 1720, 2410), 'AH': (640, 1190, 2390), 'AO': (570, 840, 2410),
    'EH': (530, 1840, 2480), 'ER': (490, 1350, 1690), 'IH': (390, 1990, 2550), 'IY': (270, 2290, 3010),
    'UH': (440, 1020, 2240), 'UW': (300, 870, 2240),
}
DIPHTHONGS = {'EY': ('EH', 'IY'), 'AY': ('AA', 'IY'), 'AW': ('AA', 'UW'), 'OW': ('AO', 'UW'), 'OY': ('AO', 'IY')}
SONORANTS = {'L': (360, 1300, 2800), 'R': (420, 1300, 1600), 'W': (300, 610, 2200), 'Y': (270, 2200, 3000),
             'M': (250, 1000, 2200), 'N': (250, 1600, 2600), 'NG': (250, 2000, 2600)}
FRICATIVES = {  # (noise centre Hz, voiced)
    'S': (3400, False), 'Z': (3400, True), 'SH': (2600, False), 'ZH': (2600, True), 'F': (3000, False),
    'V': (3000, True), 'TH': (3000, False), 'DH': (3000, True), 'HH': (1500, False), 'CH': (2600, False), 'JH': (2600, True)}
PLOSIVES = {'P': (800, False), 'B': (800, True), 'T': (3400, False), 'D': (3400, True), 'K': (1800, False), 'G': (1800, True)}

A22 = 21                        # algorithm 22 (0-based): 2>1, 6>3, 6>4, 6>5; carriers 1,3,4,5; feedback on op6


def midi_hz(n): return 440 * 2 ** ((n - 69) / 12)


def ratio_for(f, f0):
    """Nearest harmonic of f0 (DX7 coarse ratio 1..31)."""
    return int(min(31, max(1, round(f / f0))))


def base_voice(name):
    v = dx7.init_voice(name, ALG=A22, FB=0, OKS=1, TRNP=24, LFS=40, LFD=0, LPMD=0, LAMD=0)
    for n in range(1, 7):
        v['ops'][n].update(OL=0, R1=95, R2=60, R3=60, R4=72, L1=99, L2=99, L3=99, L4=0, KVS=0, DT=7, FC=1, FF=0)
    return v


def voiced(name, f0, formants, levels=(99, 92, 84), body=78, bright=58, noise=0):
    v = base_voice(name)
    f1, f2, f3 = formants
    o = v['ops']
    o[3].update(FC=ratio_for(f1, f0), OL=levels[0])      # F1
    o[4].update(FC=ratio_for(f2, f0), OL=levels[1])      # F2
    o[5].update(FC=ratio_for(f3, f0), OL=levels[2])      # F3
    o[6].update(FC=1, OL=bright)                          # shared modulator at f0: spreads each carrier into a formant band
    o[1].update(FC=1, OL=body); o[2].update(FC=1, OL=40)  # fundamental body for voicing
    if noise:
        v['global']['FB'] = 7; o[6]['OL'] = min(99, bright + noise)
    return v


def fricative(name, f0, centre, is_voiced):
    v = base_voice(name)
    o = v['ops']
    r = ratio_for(centre, f0)
    o[4].update(FC=r, OL=90); o[5].update(FC=min(31, r + 6), OL=86); o[3].update(FC=max(1, r - 5), OL=70)
    o[6].update(FC=1, OL=96); v['global']['FB'] = 7            # feedback noise smears the carriers into a hiss
    if is_voiced:
        o[1].update(FC=1, OL=82); o[2].update(FC=1, OL=40)
    return v


def burst(name, f0, centre, is_voiced):
    v = fricative(name, f0, centre, is_voiced)
    for n in (3, 4, 5):
        v['ops'][n].update(R1=99, R2=75, L2=0, L3=0)          # a quick decaying burst
    return v


def phonemes(text):
    out = []
    for w in re.findall(r"[a-zA-Z']+", text.lower()):
        ph = pronouncing.phones_for_word(w)
        if ph:
            out += [re.sub(r'\d', '', p) + ('*' if p.endswith('1') else '') for p in ph[0].split()]
        out.append('_')                                      # word gap
    return out


def segments(text, base_note=45, rate=1.0):
    """[(voice, note, start_ms, dur_ms)] - one note per phoneme (plosives: closure silence + burst)."""
    segs, t = [], 0.0
    for p in phonemes(text):
        stressed = p.endswith('*'); p = p.rstrip('*')
        note = base_note + (2 if stressed else 0)
        f0 = midi_hz(note)
        if p == '_':
            t += 60 * rate; continue
        if p in VOWELS or p in DIPHTHONGS:
            parts = [VOWELS[x] for x in DIPHTHONGS[p]] if p in DIPHTHONGS else [VOWELS[p]]
            dur = (150 if stressed else 100) * rate / len(parts)
            for k, f in enumerate(parts):
                segs.append((voiced(p, f0, f), note, t, dur)); t += dur
        elif p in SONORANTS:
            nasal = p in ('M', 'N', 'NG')
            segs.append((voiced(p, f0, SONORANTS[p], levels=(99, 80 if nasal else 88, 74 if nasal else 80), body=85 if nasal else 78), note, t, 70 * rate)); t += 70 * rate
        elif p in FRICATIVES:
            c, vo = FRICATIVES[p]
            d = (60 if p == 'HH' else 110) * rate
            segs.append((fricative(p, f0, c, vo), note, t, d)); t += d
        elif p in PLOSIVES:
            c, vo = PLOSIVES[p]
            t += 45 * rate                                     # closure
            segs.append((burst(p, f0, c, vo), note, t, 35 * rate)); t += 35 * rate
    return segs


def octave_up(v):
    """Same sound one key-octave higher: halve every ratio (1 -> 0.5, i.e. coarse 0)."""
    import copy
    w = copy.deepcopy(v)
    for n in range(1, 7):
        fc = w['ops'][n]['FC']
        w['ops'][n]['FC'] = 0 if fc <= 1 else max(1, round(fc / 2))
    return w


class Speaker:
    def __init__(self, port):
        self.o = port; self.dev = None; self.flip = 0

    def load(self, v):
        want = dx7.vced_list(v) + [63]
        n = 0
        for i in list(range(145)) + [155]:
            if self.dev is None or self.dev[i] != want[i]:
                self.o.send(mido.Message('sysex', data=[0x43, 0x10, (i >> 7) & 3, i & 127, want[i]])); n += 1
        self.dev = want
        return n

    def say(self, segs, overlap=25):
        t0 = time.perf_counter(); offs = []; sent = 0
        for v, note, start, dur in segs:
            # alternate two keys an octave apart; TRNP compensates so the pitch is the same and the notes can overlap
            self.flip ^= 1
            key = note + 12 * self.flip
            if self.flip:
                v = octave_up(v)
            while time.perf_counter() - t0 < start / 1000 - 0.004:
                self._offs(offs, t0)
                time.sleep(0.0005)
            sent += self.load(v)
            while time.perf_counter() - t0 < start / 1000:
                pass
            self.o.send(mido.Message('note_on', note=key, velocity=110))
            offs.append((start + dur + overlap, key))
        while offs:
            self._offs(offs, t0); time.sleep(0.001)
        return sent

    def _offs(self, offs, t0):
        now = (time.perf_counter() - t0) * 1000
        for x in [x for x in offs if x[0] <= now]:
            self.o.send(mido.Message('note_off', note=x[1])); offs.remove(x)


def speak(text, base_note=45, rate=1.0, wav=None):
    segs = segments(text, base_note, rate)
    with out_port() as o:
        for n in range(128): o.send(mido.Message('note_off', note=n))
        sp = Speaker(o)
        with recording(seconds_max=segs[-1][2] / 1000 + 3) as rec:
            time.sleep(0.2)
            sent = sp.say(segs)
            time.sleep(0.5)
        for n in range(128): o.send(mido.Message('note_off', note=n))
    a = rec['audio']
    if wav:
        import wave
        x = a / (np.abs(a).max() + 1e-9) * 0.9
        with wave.open(str(wav), 'wb') as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR); w.writeframes((x * 32767).astype('<i2').tobytes())
    return a, len(segs), sent


if __name__ == '__main__':
    print(phonemes(' '.join(sys.argv[1:]) or 'hello world'))
