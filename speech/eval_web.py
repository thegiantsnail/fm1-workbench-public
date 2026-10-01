"""Score the web app's speech engine with Whisper, on the software FM-1 and/or the real unit.

    python speech/eval_web.py                         # software FM-1 only
    python speech/eval_web.py --hw                    # also play the identical MIDI events on the FM-1 and record
    python speech/eval_web.py --opts '{"tone": "flat"}'
"""
import json, pathlib, re, subprocess, sys, time
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent)); sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import resynth as R

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / 'dumps' / 'webspeech'; OUT.mkdir(parents=True, exist_ok=True)
PHRASES = ['hello world', 'one two three four', 'the quick brown fox', 'I am a synthesizer', 'play it again', 'turn up the bass',
           'what is your name', 'the cat sat on the mat', 'open the door please', 'it is a beautiful day',
           'see you tomorrow', 'good morning everyone']


def node(jobs):
    r = subprocess.run(['node', str(ROOT / 'speech' / 'web_speak.cjs'), json.dumps(jobs)], capture_output=True, text=True, check=True)
    return json.loads(r.stdout)


def play_events(events, wav):
    """Send the exact events on the FM-1 (busy-wait timing) while recording its USB audio."""
    import mido
    from fm1 import out_port, recording, SR as HW_SR
    msgs = [(e['t'] / 1000, mido.Message('sysex', data=e['b'][1:-1]) if e['b'][0] == 0xF0 else mido.Message.from_bytes(e['b'])) for e in events]
    with out_port() as o:
        with recording(seconds_max=msgs[-1][0] + 1.5) as rec:
            time.sleep(0.2)
            t0 = time.perf_counter()
            for t, m in msgs:
                while time.perf_counter() - t0 < t:
                    pass
                o.send(m)
            time.sleep(0.6)
        for n in range(128): o.send(mido.Message('note_off', note=n))
    from scipy.signal import resample_poly
    R.write_wav(wav, resample_poly(rec['audio'], R.SR, HW_SR))


def run(opts, hw=False, phrases=PHRASES, tag=''):
    jobs = []
    for ph in phrases:
        k = re.sub(r'\W+', '_', ph) + tag
        jobs.append({'text': ph + '.', 'opts': opts, 'wav': str(OUT / f'soft_{k}.wav'), 'events': str(OUT / f'ev_{k}.json')})
    node(jobs)
    tot = {'soft': [0, 0], 'fm1': [0, 0]}
    for ph, j in zip(phrases, jobs):
        h = R.whisper(j['wav']); m, n = R.score(ph, h); tot['soft'][0] += m; tot['soft'][1] += n
        row = f'{ph!r:26} soft {h!r:38} {m}/{n}'
        if hw:
            wav = j['wav'].replace('soft_', 'fm1_')
            play_events(json.loads(pathlib.Path(j['events']).read_text()), wav)
            h2 = R.whisper(wav); m2, n2 = R.score(ph, h2); tot['fm1'][0] += m2; tot['fm1'][1] += n2
            row += f' | fm1 {h2!r:38} {m2}/{n2}'
        print(row, flush=True)
    print('TOTAL', json.dumps(opts), ' '.join(f'{k} {a}/{b}' for k, (a, b) in tot.items() if b), flush=True)
    return tot


if __name__ == '__main__':
    hw = '--hw' in sys.argv
    opts = json.loads(sys.argv[sys.argv.index('--opts') + 1]) if '--opts' in sys.argv else {}
    run(opts, hw)
