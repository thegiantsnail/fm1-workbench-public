"""Speech resynthesis for the FM-1: natural speech (Windows TTS) -> pitch/energy/voicing/formant tracks -> a frame plan
of FM-1 notes (one short note per frame, each with its own formant voice) -> rendered by the mock DX7 (or, later, the
real unit via fm_speak.Speaker) -> scored by Whisper.

    python speech/resynth.py ["phrase" ...]          # mock only (no hardware)
    python speech/resynth.py --hw ["phrase" ...]     # also play the plan on the FM-1 and score the recording

Mock results (6 phrases, Whisper base.en word accuracy): TTS 20/21, ideal source-filter resynthesis of the same
tracks 14/21, FM mock 11-12/21. The frame plan works best on one key (monotone): following the pitch track changed
the key every frame and scored lower. Sweeps of frame length (10/20/30 ms), key, brightness and formant-level slope
showed 20 ms, key 45, bright 48-58, slope 0.8 on a plateau; the remaining gap is mostly the analysis (see 'ideal').
On the FM-1 (2026-09-29, effects off): 8/21 - 'one two three four' 4/4 and 'play it (again)' 2/3; the unit's output
follows the mock's loudness envelope, no stuck notes, but results vary run to run ('hello world': 'Hello!' / nothing).
"""
import pathlib, re, subprocess, sys, wave, difflib
import numpy as np
from scipy.signal import lfilter, resample_poly
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import fm_speak as fs
import mock_dx7 as mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / 'dumps' / 'speech'; OUT.mkdir(parents=True, exist_ok=True)
SR = mock.SR
HOP_MS = 10


# ---------------------------------------------------------------- speech source
def tts(text, path):
    """Windows SAPI (offline, built in) or macOS `say` -> 22.05 kHz mono WAV."""
    if sys.platform == 'darwin':
        subprocess.run(['say', '-r', '160', '-o', str(path), '--data-format=LEI16@22050', text], check=True, capture_output=True)
        return read_wav(path)
    ps = ("Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; $s.Rate = -2; "
          "$f = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(22050, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, "
          "[System.Speech.AudioFormat.AudioChannel]::Mono); $s.SetOutputToWaveFile('" + str(path) + "', $f); $s.Speak('" + text.replace("'", "''") + "'); $s.Dispose()")
    subprocess.run(['powershell.exe', '-NoProfile', '-Command', ps], check=True, capture_output=True)
    return read_wav(path)


def read_wav(path):
    with wave.open(str(path)) as w:
        a = np.frombuffer(w.readframes(w.getnframes()), '<i2').astype(float) / 32768
        return a if w.getframerate() == SR else resample_poly(a, SR, w.getframerate())


def write_wav(path, y, sr=SR):
    y = y / (np.abs(y).max() + 1e-9) * 0.9
    with wave.open(str(path), 'wb') as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr); w.writeframes((y * 32767).astype('<i2').tobytes())


# ---------------------------------------------------------------- analysis
def lpc(x, order):
    r = np.correlate(x, x, 'full')[len(x) - 1:len(x) + order]
    if r[0] <= 0: return np.zeros(order + 1)
    a = np.zeros(order + 1); a[0] = 1; e = r[0]
    for i in range(1, order + 1):                    # Levinson-Durbin
        k = -(r[i] + np.dot(a[1:i], r[i - 1:0:-1])) / e
        a[1:i + 1] = a[1:i + 1] + k * a[i - 1::-1][:i]
        e *= 1 - k * k
        if e <= 0: break
    return a


def analyse(a):
    """Per 10 ms frame: energy dB, f0 (0 = unvoiced), F1-F3 Hz, formant levels dB, spectral centroid."""
    lo = resample_poly(a, 1, 2); srl = SR / 2                       # 11 kHz for LPC
    win = int(0.03 * SR); hop = int(HOP_MS / 1000 * SR)
    frames = []
    prevF = (500, 1500, 2500)
    for i in range(0, len(a) - win, hop):
        x = a[i:i + win] * np.hamming(win)
        e = 20 * np.log10(np.sqrt(np.mean(x ** 2)) + 1e-9)
        # pitch: normalised autocorrelation in 70-320 Hz
        # pitch: autocorrelation normalised by the energy of the two overlapping parts (unbiased: the old ac[l]/ac[0]
        # under-read long lags, so a low voice's vowels came out "unvoiced"); voiced above 0.4
        ac = np.correlate(x, x, 'full')[win - 1:]
        lmin, lmax = int(SR / 320), int(SR / 70)
        c2 = np.concatenate([[0.0], np.cumsum(x * x)])
        lags = np.arange(lmin, lmax)
        r = ac[lags] / (np.sqrt((c2[win] - c2[lags]) * c2[win - lags]) + 1e-12)
        k = int(lags[np.argmax(r)]) if ac[0] > 0 else 0
        voiced = ac[0] > 0 and r.max() > 0.4
        f0 = SR / k if voiced else 0.0
        # formants: LPC order 12 at 11 kHz, pre-emphasised
        j = i // 2; xl = lo[j:j + win // 2]
        xl = np.append(xl[0], xl[1:] - 0.97 * xl[:-1]) * np.hamming(len(xl))
        A = lpc(xl, 12)
        roots = [r for r in np.roots(A) if np.imag(r) > 0.01]
        cand = sorted((np.angle(r) * srl / (2 * np.pi), -srl / np.pi * np.log(abs(r))) for r in roots)
        cand = [f for f, bw in cand if 150 < f < 4500 and bw < 500]
        F = list(prevF)
        for idx, (lo_f, hi_f) in enumerate(((200, 1000), (700, 2800), (1600, 3800))):
            ok = [f for f in cand if lo_f <= f <= hi_f and (idx == 0 or f > F[idx - 1] + 150)]
            if ok: F[idx] = ok[0]
        prevF = tuple(F)
        w, H = np.linspace(0, np.pi, 512), None
        H = 1 / np.abs(np.polyval(A[::-1], np.exp(-1j * w)) + 1e-9)
        lev = [20 * np.log10(H[min(511, int(f / srl * 2 * 512))] + 1e-9) for f in F]
        spec = np.abs(np.fft.rfft(a[i:i + win] * np.hanning(win))); fr = np.fft.rfftfreq(win, 1 / SR)
        cent = float((fr * spec).sum() / (spec.sum() + 1e-9))
        frames.append(dict(e=e, f0=f0, F=F, lev=lev, cent=cent))
    return frames


# ---------------------------------------------------------------- frames -> FM-1 note plan
def plan(frames, hop_ms=20, silence_db=-42, base_note=45, monotone=True, bright=58, slope=0.8):
    """One FM-1 note per synthesis frame (every hop_ms): [(voice, key, start_ms, dur_ms)]."""
    step = hop_ms // HOP_MS
    emax = max(f['e'] for f in frames)
    notes, last_key = [], base_note
    for n in range(0, len(frames) - step, step):
        grp = frames[n:n + step]
        e = max(f['e'] for f in grp)
        if e < emax + silence_db:
            continue
        voiced = [f for f in grp if f['f0'] > 0]
        gain = int(round((e - emax) / 0.75 * 0.7))                 # loudness -> carrier level offset (0.75 dB/step)
        if len(voiced) * 2 >= len(grp):
            f0 = np.median([f['f0'] for f in voiced])
            key = base_note if monotone else int(round(69 + 12 * np.log2(f0 / 440)))
            key = max(36, min(72, key)); last_key = key
            F = np.median([f['F'] for f in voiced], axis=0)
            lev = np.median([f['lev'] for f in voiced], axis=0)
            rel = [int(np.clip(99 + (l - lev.max()) / 0.75 * slope + gain, 20, 99)) for l in lev]
            v = fs.voiced('V', fs.midi_hz(key), tuple(F), levels=tuple(rel), body=int(np.clip(70 + gain, 0, 99)), bright=bright)
        else:
            cent = np.median([f['cent'] for f in grp])
            key = last_key
            v = fs.fricative('U', fs.midi_hz(key), float(np.clip(cent, 1500, 3400)), False)
            for o in (3, 4, 5): v['ops'][o]['OL'] = int(np.clip(v['ops'][o]['OL'] + gain, 0, 99))
        for o in range(1, 7): v['ops'][o]['R4'] = 85                # short release: frames overlap only a little
        notes.append((v, key, n * HOP_MS, hop_ms + 8))
    return notes


def param_budget(notes):
    """Parameters that change between consecutive frame voices (what the FM-1 would receive per frame)."""
    import dx7
    prev, counts = None, []
    for v, *_ in notes:
        cur = dx7.vced_list(v)
        counts.append(sum(1 for i in range(145) if prev is None or prev[i] != cur[i]) if prev else 145)
        prev = cur
    return counts


def play_on_fm1(notes, wav):
    """Hardware path (not yet verified on the unit): param diffs per frame + alternating keys via fm_speak.Speaker.
    Plan notes already overlap (dur = hop + 8 ms), so no extra overlap."""
    import mido
    from fm1 import out_port, recording
    with out_port() as o:
        for n in range(128): o.send(mido.Message('note_off', note=n))
        with recording(seconds_max=notes[-1][2] / 1000 + 2) as rec:
            import time; time.sleep(0.2)
            fs.Speaker(o).say(notes, overlap=0)
            time.sleep(0.5)
        for n in range(128): o.send(mido.Message('note_off', note=n))
    a = rec['audio']
    write_wav(wav, resample_poly(a, SR, 44100) if len(a) else a)


# ---------------------------------------------------------------- reference: ideal source-filter resynthesis
def ideal(frames):
    out = np.zeros(len(frames) * int(HOP_MS / 1000 * SR) + SR)
    hop = int(HOP_MS / 1000 * SR); rng = np.random.default_rng(0); ph = 0.0
    def reson(x, f, bw):
        r = np.exp(-np.pi * bw / SR); b = 2 * r * np.cos(2 * np.pi * f / SR); c = -r * r
        return lfilter([1 - r], [1, -b, -c], x)
    for i, f in enumerate(frames):
        g = 10 ** (f['e'] / 20)
        if f['f0'] > 0:
            t = np.arange(hop); src = np.zeros(hop)
            per = SR / f['f0']
            for k in range(hop):
                ph += 1
                if ph >= per: ph -= per; src[k] = 1
            y = sum(reson(src, F, bw) * 10 ** ((l - max(f['lev'])) / 20) for F, bw, l in zip(f['F'], (90, 110, 170), f['lev']))
        else:
            y = reson(rng.standard_normal(hop), max(1500, f['cent']), 2000) * 0.3
        y = y / (np.sqrt(np.mean(y ** 2)) + 1e-9) * g
        out[i * hop:(i + 1) * hop] += y
    return out


# ---------------------------------------------------------------- scoring
NUM = {'1': 'one', '2': 'two', '3': 'three', '4': 'four', '5': 'five', '6': 'six', '7': 'seven', '8': 'eight', '9': 'nine', '0': 'zero'}
def words(s):
    s = re.sub(r'\d', lambda m: ' ' + NUM[m.group()] + ' ', s.lower())
    return re.sub(r'[^a-z ]', ' ', s).split()


_model = None
def whisper(path):
    global _model
    if _model is None:
        from faster_whisper import WhisperModel
        _model = WhisperModel('base.en', device='cpu', compute_type='int8')
    return ' '.join(s.text for s in _model.transcribe(str(path), beam_size=5, language='en', condition_on_previous_text=False)[0]).strip()


def score(want, heard):
    w, h = words(want), words(heard)
    return sum(b.size for b in difflib.SequenceMatcher(a=w, b=h).get_matching_blocks()), len(w)


if __name__ == '__main__':
    hw = '--hw' in sys.argv
    phrases = [a for a in sys.argv[1:] if a != '--hw'] or ['hello world', 'one two three four', 'the quick brown fox', 'I am a synthesizer',
                               'play it again', 'turn up the bass']
    totals = {}
    for ph in phrases:
        tag = re.sub(r'\W+', '_', ph)
        src = tts(ph, OUT / f'tts_{tag}.wav')
        fr = analyse(src)
        notes = plan(fr)
        budget = param_budget(notes)
        versions = {
            'tts': OUT / f'tts_{tag}.wav',
            'ideal': OUT / f'ideal_{tag}.wav',
            'fm-mock': OUT / f'fmmock_{tag}.wav',
            'fm-mock-pitch': OUT / f'fmpitch_{tag}.wav',
        }
        write_wav(versions['ideal'], ideal(fr))
        write_wav(versions['fm-mock'], mock.render(notes))
        write_wav(versions['fm-mock-pitch'], mock.render(plan(fr, monotone=False)))
        if hw:
            versions['fm-1'] = OUT / f'fm1_{tag}.wav'
            play_on_fm1(notes, versions['fm-1'])
        print(f'{ph!r}: {len(notes)} notes, params per frame median {int(np.median(budget[1:]))} max {max(budget[1:])}')
        for k, p in versions.items():
            h = whisper(p); m, n = score(ph, h)
            totals.setdefault(k, [0, 0]); totals[k][0] += m; totals[k][1] += n
            print(f'   {k:13s} heard {h!r:45} {m}/{n}')
    print('word accuracy: ' + ', '.join(f'{k} {m}/{n}' for k, (m, n) in totals.items()))
