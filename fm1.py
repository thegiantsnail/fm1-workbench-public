"""Shared helpers: MIDI I/O to the FM-1 and recording its USB audio for measurement."""
import time, threading, contextlib
import numpy as np, sounddevice as sd, mido

def _port(names):
    return next((n for n in names if 'FM-1' in n), None)

MIDI_IN = _port(mido.get_input_names())      # port names vary ('FM-1 Audio 0' / 'FM-1 Midi 0')
MIDI_OUT = _port(mido.get_output_names())


def _audio_in():
    """FM-1 USB audio input, looked up by name (device indices change when devices come and go).
    Windows: "Microphone (FM-1 Audio)" via MME. Elsewhere (Core Audio, ALSA): the first FM-1 input."""
    found = [(i, sd.query_hostapis(d['hostapi'])['name']) for i, d in enumerate(sd.query_devices())
             if 'FM-1' in d['name'] and d['max_input_channels'] > 0]
    return next((i for i, api in found if api == 'MME'), found[0][0] if found else None)


AUDIO_IN, SR = _audio_in(), 44100


def out_port():
    # mido.open_output(None) would silently open the *default* port and send DX7 SysEx to some other device.
    if MIDI_OUT is None:
        raise RuntimeError('FM-1 MIDI output not found - is the FM-1 plugged in?')
    return mido.open_output(MIDI_OUT)


def in_port():
    if MIDI_IN is None:
        raise RuntimeError('FM-1 MIDI input not found - is the FM-1 plugged in?')
    return mido.open_input(MIDI_IN)


@contextlib.contextmanager
def recording(seconds_max=60):
    """Record FM-1 audio while the with-block runs; yields a dict whose 'audio' key is filled on exit.
    Only frames the driver actually delivered are kept (a pre-allocated sd.rec buffer sliced by wall-clock time can
    include never-written memory)."""
    res, chunks = {}, []
    limit = int(seconds_max * SR)

    def cb(indata, frames, t, status):
        if sum(len(c) for c in chunks) < limit:
            chunks.append(indata.copy())
    with sd.InputStream(samplerate=SR, channels=2, device=AUDIO_IN, dtype='float32', callback=cb):
        try:
            yield res
        finally:
            pass
    a = np.concatenate(chunks) if chunks else np.zeros((0, 2), 'float32')
    res['audio'] = a[:limit].mean(axis=1)


def seg(audio, t0, t1):
    return audio[int(t0 * SR):int(t1 * SR)]


def rms_db(x):
    return 20 * np.log10(np.sqrt(np.mean(x ** 2)) + 1e-9)


def spectrum(x):
    w = x * np.hanning(len(x))
    mag = np.abs(np.fft.rfft(w))
    freqs = np.fft.rfftfreq(len(x), 1 / SR)
    return freqs, mag


def pitch_hz(x):
    f, m = spectrum(x)
    m[f < 40] = 0
    return f[np.argmax(m)]


def centroid_hz(x):
    f, m = spectrum(x)
    return float((f * m).sum() / (m.sum() + 1e-9))


def sine_purity(x):
    """Fraction of spectral energy within +-3 bins of the strongest peak (1.0 = pure sine)."""
    f, m = spectrum(x)
    p = m ** 2
    k = np.argmax(p)
    return float(p[max(0, k - 3):k + 4].sum() / (p.sum() + 1e-12))


def sysex(port, data):
    port.send(mido.Message('sysex', data=list(data)))


def dx7_param(port, param, value, ch=0):
    """DX7 single parameter change: F0 43 1n gg pp dd F7 (group 0 = voice params 0-155)."""
    sysex(port, [0x43, 0x10 | ch, (param >> 7) & 0x03, param & 0x7F, value & 0x7F])
