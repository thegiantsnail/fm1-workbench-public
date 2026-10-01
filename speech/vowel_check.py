"""Hold vowels on the FM-1 and compare the measured spectral envelope peaks with the formant targets."""
import sys, pathlib, time
import numpy as np, mido
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import fm_speak as fs
from fm1 import out_port, recording, SR

def envelope_peaks(x, f0):
    """Amplitude (dB) of each harmonic of f0 up to 4 kHz, and the 3 strongest regions."""
    m = np.abs(np.fft.rfft(x * np.hanning(len(x)))); f = np.fft.rfftfreq(len(x), 1 / SR)
    harm = []
    for k in range(1, int(4000 / f0) + 1):
        band = (f > (k - 0.3) * f0) & (f < (k + 0.3) * f0)
        harm.append((k * f0, 20 * np.log10(m[band].max() + 1e-9)))
    return harm

note = 45; f0 = fs.midi_hz(note)
with out_port() as o:
    sp = fs.Speaker(o)
    for vname in ('AA', 'IY', 'UW'):
        v = fs.voiced(vname, f0, fs.VOWELS[vname])
        sp.load(v); time.sleep(0.05)
        with recording() as rec:
            time.sleep(0.15); o.send(mido.Message('note_on', note=note, velocity=110)); time.sleep(0.8); o.send(mido.Message('note_off', note=note)); time.sleep(0.3)
        a = rec['audio']; x = a[int(0.35 * SR):int(0.85 * SR)]
        h = envelope_peaks(x, f0)
        top = max(d for _, d in h)
        prof = ' '.join(f'{int(fr)}:{int(d - top)}' for fr, d in h if d - top > -30)
        targets = fs.VOWELS[vname]
        print(f'{vname} targets {targets}  ratios {[fs.ratio_for(t, f0) for t in targets]}  f0 {f0:.0f} Hz')
        print(f'   harmonics within 30 dB of the peak (Hz:dB): {prof}')
