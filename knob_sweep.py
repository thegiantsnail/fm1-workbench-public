"""Repeat a reference note (sine patch, vel 100) every 0.5 s while the user turns the FM-1 volume knob; log the USB level."""
import sys, time, threading, mido, numpy as np
from fm1 import out_port, recording, rms_db, seg, sysex
from dx7 import init_voice, vced_sysex

SECS = int(sys.argv[1]) if len(sys.argv) > 1 else 40
sine = init_voice('LEVEL REF', ALG=31, ops={1: dict(OL=99, KVS=0)})       # KVS 0: velocity-independent
with out_port() as o:
    sysex(o, vced_sysex(sine)); time.sleep(1.0)
    with recording(seconds_max=SECS + 5) as rec:
        t0 = time.time(); marks = []
        while time.time() - t0 < SECS:
            t = time.time() - t0; o.send(mido.Message('note_on', note=69, velocity=100)); time.sleep(0.35)
            o.send(mido.Message('note_off', note=69)); time.sleep(0.15); marks.append(t)
    for n in range(128): o.send(mido.Message('note_off', note=n))
a = rec['audio']
print(' time | level dBFS | meter (each # = 2 dB above -90)')
for t in marks[::2]:
    lv = rms_db(seg(a, t + 0.08, t + 0.33))
    print(f' {t:4.1f}s | {lv:7.1f}    | ' + '#' * max(0, int((lv + 90) / 2)))
lv = [rms_db(seg(a, t + 0.08, t + 0.33)) for t in marks]
print(f'range: {min(lv):.1f} .. {max(lv):.1f} dBFS  ({max(lv) - min(lv):.1f} dB of control)')
