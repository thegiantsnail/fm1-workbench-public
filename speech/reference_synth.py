"""Software reference: render the SAME phoneme plan as fm_speak.segments() with an ideal formant synthesizer
(glottal pulse train -> 3 resonators, noise for fricatives/bursts) and let Whisper score it. Separates "is the plan
intelligible?" from "is the FM-1 rendering intelligible?". Optionally render an FM-1-like model (see --fm)."""
import sys, pathlib, re, difflib, wave
import numpy as np
from scipy.signal import lfilter
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import fm_speak as fs

SR = 22050


def resonator(x, f, bw):
    r = np.exp(-np.pi * bw / SR); c = -r * r; b = 2 * r * np.cos(2 * np.pi * f / SR); a0 = 1 - b - c
    return lfilter([a0], [1, -b, -c], x)


def formants_of(v):
    """Recover what the plan asked for from the voice name + f0 (the plan stores phoneme names as voice names)."""
    return v['name']


def render(text, base_note=45, rate=1.0):
    segs = fs.segments(text, base_note, rate)
    total = int((segs[-1][2] + segs[-1][3] + 300) / 1000 * SR)
    out = np.zeros(total)
    rng = np.random.default_rng(0)
    for v, note, start, dur in segs:
        p = v['name'].strip()
        f0 = fs.midi_hz(note)
        n = int((dur + 30) / 1000 * SR); s = int(start / 1000 * SR)
        t = np.arange(n) / SR
        env = np.minimum(1, np.minimum(t / 0.012, (n / SR - t) / 0.02))
        if p in fs.VOWELS or p in fs.SONORANTS or p in fs.DIPHTHONGS:
            fm = fs.VOWELS.get(p) or fs.SONORANTS.get(p) or fs.VOWELS[fs.DIPHTHONGS[p][0]]
            src = (np.mod(t * f0, 1) < 0.5).astype(float) - 0.5                 # glottal-ish pulse train
            src = np.diff(src, prepend=0) * 0.5 + src * 0.05
            y = resonator(src, fm[0], 80) + 0.6 * resonator(src, fm[1], 100) + 0.3 * resonator(src, fm[2], 150)
            if p in ('M', 'N', 'NG'): y *= 0.5
        else:
            centre = (fs.FRICATIVES.get(p) or fs.PLOSIVES.get(p) or (3000, False))[0]
            y = resonator(rng.standard_normal(n), centre, 1200) * 0.3
        out[s:s + n] += y * env
    return out / (np.abs(out).max() + 1e-9) * 0.9


if __name__ == '__main__':
    from faster_whisper import WhisperModel
    model = WhisperModel('base.en', device='cpu', compute_type='int8')
    norm = lambda s: re.sub(r'[^a-z ]', '', s.lower()).split()
    out_dir = pathlib.Path(__file__).resolve().parent.parent / 'dumps' / 'speech'; out_dir.mkdir(parents=True, exist_ok=True)
    total = hit = 0
    for ph in sys.argv[1:] or ['hello world', 'one two three four', 'the quick brown fox', 'I am a synthesizer']:
        y = render(ph)
        path = out_dir / ('ref_' + re.sub(r'\W+', '_', ph) + '.wav')
        with wave.open(str(path), 'wb') as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR); w.writeframes((y * 32767).astype('<i2').tobytes())
        heard = ' '.join(s.text for s in model.transcribe(str(path), beam_size=5, language='en', condition_on_previous_text=False)[0]).strip()
        want, got = norm(ph), norm(heard)
        m = sum(b.size for b in difflib.SequenceMatcher(a=want, b=got).get_matching_blocks())
        total += len(want); hit += m
        print(f'{ph!r:28} -> heard {heard!r:40} words {m}/{len(want)}')
    print(f'reference word accuracy: {hit}/{total}')
