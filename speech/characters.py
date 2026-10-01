"""Render every speech character on the FM-1 (and the software FM-1), score intelligibility with Whisper, and build a
demo reel WAV.   python speech/characters.py [--soft-only] [names...]"""
import json, pathlib, sys, time
import numpy as np
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent)); sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import resynth as R, eval_web as W

OUT = W.OUT.parent / 'characters'; OUT.mkdir(parents=True, exist_ok=True)
TEXT = 'Hello there. I am the FM-1, and I can speak in many voices. Can you hear me?'
NAMES = ['natural', 'feminine', 'child', 'giant', 'whisper', 'robot', 'stutter', 'cyborg', 'alien', 'singer', 'radio',
         'cathedral', 'choir', 'echo', 'uptalk', 'singsong', 'drawl', 'excited']

if __name__ == '__main__':
    hw = '--soft-only' not in sys.argv
    names = [a for a in sys.argv[1:] if not a.startswith('--')] or NAMES
    jobs = [{'text': TEXT, 'character': n, 'wav': str(OUT / f'soft_{n}.wav'), 'events': str(OUT / f'ev_{n}.json')} for n in names]
    W.node(jobs)
    reel, rows = [], []
    for n, j in zip(names, jobs):
        h = R.whisper(j['wav']); m, tot = R.score(TEXT, h)
        row = f'{n:10} soft {m:2}/{tot}'
        if hw:
            wav = j['wav'].replace('soft_', 'fm1_')
            W.play_events(json.loads(pathlib.Path(j['events']).read_text()), wav)
            h2 = R.whisper(wav); m2, _ = R.score(TEXT, h2)
            row += f' | fm1 {m2:2}/{tot}  heard {h2!r}'
            x = R.read_wav(wav); reel += [x / (np.abs(x).max() + 1e-9) * 0.8, np.zeros(int(0.6 * R.SR))]
        print(row, flush=True)
        rows.append((n, m, m2 if hw else None))
    if reel:
        R.write_wav(OUT / 'fm1_character_reel.wav', np.concatenate(reel))
        (OUT / 'reel_order.txt').write_text('\n'.join(names))
    (OUT / 'scores.json').write_text(json.dumps(rows))
