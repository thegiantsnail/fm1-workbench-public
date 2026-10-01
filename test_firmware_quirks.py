"""Firmware quirks, before/after comparison (stock M-VAVE vs Baud Girl's FM-1+VA). Parameter messages only - no voice
dumps (on FM-1+VA a single-voice dump overwrites the selected preset).
  detune   two sine carriers, DT 7 vs DT 14 (and 0): beat rate in Hz at A4
  vel0     note ended with note-on velocity 0: level 300 ms later (dB re sounding)
  trnp     TRNP changed while a note is held, then note-off: level 400 ms later (dB re sounding)
  lfo      patch LFO speed 10 / 60 / 99: vibrato rate (Hz)
  pchange  a parameter change still applies to the next note (OL 99 -> 70 on the only carrier: level change dB)

    python test_firmware_quirks.py [label]      -> dumps/firmware_<label>.json
"""
import json, pathlib, sys, time
import numpy as np, mido
from scipy.signal import hilbert
import dx7
from fm1 import out_port, recording, SR

OUT = pathlib.Path(__file__).resolve().parent / 'dumps'


def load(o, v):
    """Whole voice as parameter changes (never a dump)."""
    d = dx7.vced_list(v)[:155] + [63]
    for p in list(range(155)) + [155]:
        if 145 <= p < 155: continue
        o.send(mido.Message('sysex', data=[0x43, 0x10, (p >> 7) & 3, p & 127, d[p]]))
    time.sleep(0.05)


def sine_voice(**g):
    v = dx7.init_voice('QUIRK', **{'ALG': 31, 'FB': 0, 'LFD': 0, 'LPMD': 0, 'LAMD': 0, **g})
    for n in range(1, 7): v['ops'][n].update(OL=0, R1=99, R2=99, R3=99, R4=99, L1=99, L2=99, L3=99, L4=0, DT=7, FC=1, FF=0, KVS=0, AMS=0)
    v['ops'][1]['OL'] = 99
    return v


def take(o, fn, secs):
    with recording(seconds_max=secs + 0.5) as rec:
        time.sleep(0.2); fn(); time.sleep(0.1)
    return rec['audio']


def db(x): return 10 * np.log10(np.mean(x ** 2) + 1e-20)


def main(label):
    res = {}
    with out_port() as o:
        for c in (0, 4, 8, 12, 16, 20): o.send(mido.Message('control_change', channel=1, control=c, value=0))
        # detune: beat between OP1 (DT 7) and OP2 (DT x)
        det = {}
        for dt in (0, 14):
            v = sine_voice(); v['ops'][2].update(OL=99, DT=dt); load(o, v)
            def play():
                o.send(mido.Message('note_on', note=69, velocity=100)); time.sleep(3.0); o.send(mido.Message('note_off', note=69))
            x = take(o, play, 3.3)
            e = np.abs(hilbert(x[int(0.5 * SR):int(3.0 * SR)]))
            k = int(0.005 * SR); e = np.convolve(e, np.ones(k) / k, 'valid')[::k]
            s = np.abs(np.fft.rfft((e - e.mean()) * np.hanning(len(e)))); f = np.fft.rfftfreq(len(e), 0.005)
            det[dt] = float(f[1 + np.argmax(s[1:])])
        res['detune_beat_hz'] = det
        print(f'detune: beat DT0 {det[0]:.2f} Hz, DT14 {det[14]:.2f} Hz (A4)', flush=True)
        # velocity-0 note-on as note-off
        load(o, sine_voice())
        def v0():
            o.send(mido.Message('note_on', note=69, velocity=100)); time.sleep(0.5)
            o.send(mido.Message('note_on', note=69, velocity=0)); time.sleep(0.6)
        x = take(o, v0, 1.3)
        res['vel0_db'] = float(db(x[int(1.0 * SR):int(1.2 * SR)]) - db(x[int(0.4 * SR):int(0.6 * SR)]))
        o.send(mido.Message('note_off', note=69)); time.sleep(0.3)
        print(f"vel-0 note-on: {res['vel0_db']:.1f} dB 300 ms later (stuck if > -40)", flush=True)
        # TRNP change while held
        def tr():
            o.send(mido.Message('note_on', note=69, velocity=100)); time.sleep(0.5)
            o.send(mido.Message('sysex', data=[0x43, 0x10, 1, 144 - 128, 36])); time.sleep(0.05)
            o.send(mido.Message('note_off', note=69)); time.sleep(0.6)
        x = take(o, tr, 1.3)
        res['trnp_db'] = float(db(x[int(1.05 * SR):int(1.25 * SR)]) - db(x[int(0.4 * SR):int(0.6 * SR)]))
        for n in range(128): o.send(mido.Message('note_off', note=n))
        o.send(mido.Message('sysex', data=[0x43, 0x10, 1, 144 - 128, 24])); time.sleep(0.3)
        for n in range(128): o.send(mido.Message('note_off', note=n))
        print(f"TRNP change while held: {res['trnp_db']:.1f} dB 400 ms after note-off (stuck if > -40)", flush=True)
        # LFO speed
        lfo = {}
        for spd in (10, 60, 99):
            load(o, sine_voice(LFS=spd, LPMD=99, LPMS=4, LFW=4))
            def play():
                o.send(mido.Message('note_on', note=69, velocity=100)); time.sleep(3.0); o.send(mido.Message('note_off', note=69))
            x = take(o, play, 3.3)
            a = hilbert(x[int(0.5 * SR):int(3.0 * SR)]); inst = np.diff(np.unwrap(np.angle(a))) * SR / (2 * np.pi)
            k = int(0.005 * SR); inst = np.convolve(inst, np.ones(k) / k, 'valid')[::k]
            s = np.abs(np.fft.rfft((inst - inst.mean()) * np.hanning(len(inst)))); f = np.fft.rfftfreq(len(inst), 0.005)
            lfo[spd] = float(f[1 + np.argmax(s[1:])])
        res['lfo_hz'] = lfo
        print('LFO speed 10/60/99 -> ' + ' / '.join(f'{lfo[s]:.2f}' for s in (10, 60, 99)) + ' Hz', flush=True)
        # parameter change applies to the next note
        load(o, sine_voice())
        def pc():
            o.send(mido.Message('note_on', note=69, velocity=100)); time.sleep(0.4); o.send(mido.Message('note_off', note=69)); time.sleep(0.3)
            o.send(mido.Message('sysex', data=[0x43, 0x10, 0, 105 + 16, 70])); time.sleep(0.02)
            o.send(mido.Message('note_on', note=69, velocity=100)); time.sleep(0.4); o.send(mido.Message('note_off', note=69))
        x = take(o, pc, 1.3)
        res['pchange_db'] = float(db(x[int(0.95 * SR):int(1.2 * SR)]) - db(x[int(0.25 * SR):int(0.5 * SR)]))
        print(f"param change OL 99->70: next note {res['pchange_db']:.1f} dB (expect about -21.75)", flush=True)
        for n in range(128): o.send(mido.Message('note_off', note=n))
    (OUT / f'firmware_{label}.json').write_text(json.dumps(res, indent=1))


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else 'stock')
