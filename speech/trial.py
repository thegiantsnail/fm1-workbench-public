"""Say phrases on the FM-1, record them, and let Whisper transcribe - an objective intelligibility score.
Usage: python speech/trial.py ["phrase" ...]"""
import sys, pathlib, re, time, difflib
import numpy as np
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from fm_speak import speak
from faster_whisper import WhisperModel

OUT = pathlib.Path(__file__).resolve().parent.parent / 'dumps' / 'speech'; OUT.mkdir(parents=True, exist_ok=True)
phrases = sys.argv[1:] or ['hello world', 'one two three four', 'the quick brown fox', 'I am a synthesizer']
model = WhisperModel('base.en', device='cpu', compute_type='int8')
from resynth import words as norm          # lower-case words, digits spelled out ("1, 2" -> one two)
total = hit = 0
for ph in phrases:
    wav = OUT / (re.sub(r'\W+', '_', ph) + '.wav')
    a, nseg, sent = speak(ph, wav=wav)
    segs, _ = model.transcribe(str(wav), beam_size=5, language='en', condition_on_previous_text=False,
                               initial_prompt=None, vad_filter=False)
    heard = ' '.join(s.text for s in segs).strip()
    want, got = norm(ph), norm(heard)
    m = difflib.SequenceMatcher(a=want, b=got)
    matched = sum(b.size for b in m.get_matching_blocks())
    total += len(want); hit += matched
    print(f'{ph!r:28} -> heard {heard!r:40}  words {matched}/{len(want)}  '
          f'({nseg} notes, {sent} params, peak {20 * np.log10(np.abs(a).max() + 1e-9):.0f} dBFS)')
print(f'word accuracy: {hit}/{total}')
