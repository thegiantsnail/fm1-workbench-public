"""Software FM-1 (app/fm1-synth.js) vs the real unit, voice by voice.

Each voice (classic DX7 ROM1 patches from the library) is loaded with a single-voice dump, then C4 is held for 1 s.
The FM-1's USB audio is recorded; the software FM-1 renders the identical MIDI in Node. Compared, after aligning onsets:
  env   mean |dB difference| of the loudness contour (10 ms frames, each normalised to its own peak)
  spec  mean |dB difference| of the 1/3-octave spectrum 0.1-0.5 s after onset (bands within 40 dB of the peak)
  rel   release time after note-off to -30 dB (hardware / software, ms)
  lvl   loudness relative to the other voices (hardware minus software, dB, after removing the average offset)
The unit's effects are switched off first. WAV pairs go to dumps/parity/.

    python test_soft_parity.py [voice index ...]
"""
import base64, json, pathlib, subprocess, sys, time
import numpy as np, mido
import dx7
from fm1 import out_port, recording, SR

ROOT = pathlib.Path(__file__).resolve().parent
OUT = ROOT / 'dumps' / 'parity'; OUT.mkdir(parents=True, exist_ok=True)
LIB = json.loads((ROOT / 'app' / 'library' / 'index.json').read_text())['voices']
DEFAULT = [0, 3, 7, 10, 13, 14, 16, 18, 21, 23, 25, 26]
NOTE, VEL, ON_MS, HOLD_MS, TAIL_MS = 60, 100, 300, 1000, 2000


def vced_sysex(entry):
    d = dx7.vmem_to_vced(list(base64.b64decode(entry[3])))[:155]
    return [0xF0, 0x43, 0x00, 0x00, 0x01, 0x1B, *d, (128 - (sum(d) & 127)) & 127, 0xF7]


def hardware(sx):
    with out_port() as o:
        for c in (0, 4, 8, 12, 16, 20): o.send(mido.Message('control_change', channel=1, control=c, value=0))
        # The voice goes to the unit as parameter changes, never as a dump: on Baud Girl's FM-1+VA firmware a
        # single-voice dump permanently overwrites the selected preset. (The software side still gets the dump.)
        d = sx[6:6 + 155]
        for p in list(range(145)) + [155]:
            o.send(mido.Message('sysex', data=[0x43, 0x10, (p >> 7) & 3, p & 127, 63 if p == 155 else d[p]]))
        time.sleep(0.1)
        with recording(seconds_max=(ON_MS + HOLD_MS + TAIL_MS) / 1000 + 0.5) as rec:
            time.sleep(ON_MS / 1000)
            o.send(mido.Message('note_on', note=NOTE, velocity=VEL))
            time.sleep(HOLD_MS / 1000)
            o.send(mido.Message('note_off', note=NOTE))
            time.sleep(TAIL_MS / 1000)
        for n in range(128): o.send(mido.Message('note_off', note=n))
    return rec['audio']


def software(sx, tag):
    ev = [{'t': 0, 'b': sx}, {'t': ON_MS, 'b': [0x90, NOTE, VEL]}, {'t': ON_MS + HOLD_MS, 'b': [0x80, NOTE, 0]},
          {'t': ON_MS + HOLD_MS + TAIL_MS, 'b': [0xB0, 123, 0]}]           # last event only sets the render length
    evp = OUT / f'ev_{tag}.json'; evp.write_text(json.dumps(ev))
    wav = OUT / f'soft_{tag}.wav'
    subprocess.run(['node', str(ROOT / 'speech' / 'web_speak.cjs'), json.dumps({'eventsIn': str(evp), 'wav': str(wav), 'sr': SR, 'raw': True})],
                   check=True, capture_output=True)
    import wave
    with wave.open(str(wav)) as w:
        return np.frombuffer(w.readframes(w.getnframes()), '<i2').astype(float) / 32768


def save(path, x):
    import wave
    y = x / (np.abs(x).max() + 1e-12) * 0.9
    with wave.open(str(path), 'wb') as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR); w.writeframes((y * 32767).astype('<i2').tobytes())


def frames_db(x, hop=441):
    n = len(x) // hop
    return 10 * np.log10(np.array([np.mean(x[i * hop:(i + 1) * hop] ** 2) for i in range(n)]) + 1e-20)


def onset(x):
    e = frames_db(x)
    return int(np.argmax(e > e.max() - 40)) * 441


def bands(x):
    seg = x[int(0.1 * SR):int(0.5 * SR)]
    if len(seg) < 4096: return None
    spec = np.abs(np.fft.rfft(seg * np.hanning(len(seg)))) ** 2
    f = np.fft.rfftfreq(len(seg), 1 / SR)
    cs = 50 * 2 ** (np.arange(0, 28) / 3)
    return 10 * np.log10(np.array([spec[(f >= c / 2 ** (1 / 6)) & (f < c * 2 ** (1 / 6))].sum() for c in cs]) + 1e-20)


def release_ms(x):
    e = frames_db(x)
    k0 = HOLD_MS // 10
    ref = e[max(0, k0 - 5):k0].mean()
    after = np.where(e[k0:] < ref - 30)[0]
    return after[0] * 10 if len(after) else np.nan


def main(idx):
    rows = []
    for i in idx:
        entry = LIB[i]; name = entry[0].strip(); tag = f'{i:03d}_{name.replace(" ", "_").replace(".", "")}'
        sx = vced_sysex(entry)
        hw = hardware(sx); sw = software(sx, tag)
        hw = hw[onset(hw):]; sw = sw[onset(sw):]
        n = min(len(hw), len(sw)); hw, sw = hw[:n], sw[:n]
        save(OUT / f'fm1_{tag}.wav', hw); save(OUT / f'soft_{tag}.wav', sw)
        eh, es = frames_db(hw), frames_db(sw)
        eh -= eh.max(); es -= es.max()
        m = (eh > -50) | (es > -50)
        env = float(np.mean(np.abs(np.clip(eh[m], -60, 0) - np.clip(es[m], -60, 0))))
        bh, bs = bands(hw), bands(sw)
        bh -= bh.max(); bs -= bs.max(); mb = (bh > -40) | (bs > -40)
        spec = float(np.mean(np.abs(np.clip(bh[mb], -50, 0) - np.clip(bs[mb], -50, 0))))
        rows.append(dict(i=i, name=name, env=env, spec=spec, rel_hw=release_ms(hw), rel_sw=release_ms(sw),
                         lvl_hw=float(frames_db(hw).max()), lvl_sw=float(frames_db(sw).max()), bh=bh.round(1).tolist(), bs=bs.round(1).tolist()))
        print(f"{name:11} env {env:5.1f} dB  spec {spec:5.1f} dB  release {rows[-1]['rel_hw']:6.0f} / {rows[-1]['rel_sw']:6.0f} ms", flush=True)
    off = np.mean([r['lvl_hw'] - r['lvl_sw'] for r in rows])
    for r in rows: r['lvl'] = r['lvl_hw'] - r['lvl_sw'] - off
    print('relative loudness (hw - sw, dB):', ', '.join(f"{r['name']} {r['lvl']:+.1f}" for r in rows))
    print(f"mean: env {np.mean([r['env'] for r in rows]):.1f} dB, spec {np.mean([r['spec'] for r in rows]):.1f} dB, "
          f"|loudness| {np.mean([abs(r['lvl']) for r in rows]):.1f} dB")
    (OUT / 'results.json').write_text(json.dumps(rows, indent=1, default=float))


if __name__ == '__main__':
    main([int(a) for a in sys.argv[1:]] or DEFAULT)
