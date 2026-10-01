"""Does fricative noise 'stick'? Measure 4-8 kHz hiss inside a vowel played after an /s/ (vs. the vowel alone),
at several times after the /s/ note ends - separates a slow release from feedback state leaking into later notes."""
import sys, pathlib, time
import numpy as np, mido
from scipy.signal import butter, sosfilt
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import fm_speak as fs
from fm1 import out_port, recording, SR

HP = butter(6, [4000, 8000], btype='bandpass', fs=SR, output='sos')
def hiss_db(x): y = sosfilt(HP, x); return 20 * np.log10(np.sqrt(np.mean(y ** 2)) + 1e-9)

f0 = fs.midi_hz(45)
def run(o, sp, fric_R4=None, gap_ms=0, vowel_ms=900):
    s = fs.fricative('S', f0, 3400, False)
    if fric_R4 is not None:
        for n in range(1, 7): s['ops'][n]['R4'] = fric_R4
    a = fs.voiced('AA', f0, fs.VOWELS['AA'])
    with recording(seconds_max=5) as rec:
        t0 = time.perf_counter(); time.sleep(0.2)
        sp.load(s); time.sleep(0.01); o.send(mido.Message('note_on', note=45, velocity=110)); t_s = time.perf_counter() - t0
        time.sleep(0.15); o.send(mido.Message('note_off', note=45)); t_off = time.perf_counter() - t0
        time.sleep(gap_ms / 1000)
        sp.load(a); time.sleep(0.005); o.send(mido.Message('note_on', note=57 - 12 + 12, velocity=110)); t_v = time.perf_counter() - t0
        time.sleep(vowel_ms / 1000); o.send(mido.Message('note_off', note=57)); time.sleep(0.3)
    x = rec['audio']
    return [round(float(hiss_db(x[int((t_v + w) * SR):int((t_v + w + 0.1) * SR)])), 1) for w in (0.05, 0.2, 0.4, 0.7)]

with out_port() as o:
    for n in range(128): o.send(mido.Message('note_off', note=n))
    sp = fs.Speaker(o)
    # vowel alone (reference), played the same way
    a = fs.voiced('AA', f0, fs.VOWELS['AA']); sp.load(a)
    with recording() as rec:
        time.sleep(0.2); t = 0.2; o.send(mido.Message('note_on', note=57, velocity=110)); time.sleep(0.9); o.send(mido.Message('note_off', note=57)); time.sleep(0.3)
    ref = [round(float(hiss_db(rec['audio'][int((0.2 + w) * SR):int((0.3 + w) * SR)])), 1) for w in (0.05, 0.2, 0.4, 0.7)]
    print('hiss 4-8 kHz inside /a/ at +50, +200, +400, +700 ms (dB):')
    print('  /a/ alone                      ', ref)
    print('  /s/ (R4=72) then /a/           ', run(o, sp))
    print('  /s/ (R4=99) then /a/           ', run(o, sp, fric_R4=99))
    print('  /s/ (R4=99), 400 ms gap, /a/   ', run(o, sp, fric_R4=99, gap_ms=400))
    for n in range(128): o.send(mido.Message('note_off', note=n))
