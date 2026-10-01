"""Test 1: does the FM-1 respond to incoming note velocity? Plays middle C at 5 velocities, measures loudness."""
import time, mido
from fm1 import out_port, recording, seg, rms_db, pitch_hz

VELS = [20, 50, 80, 110, 127]
NOTE, ON, GAP = 60, 0.8, 0.7

with out_port() as out, recording() as rec:
    time.sleep(0.5)
    marks = []
    for v in VELS:
        t = time.time()
        out.send(mido.Message('note_on', note=NOTE, velocity=v))
        time.sleep(ON)
        out.send(mido.Message('note_off', note=NOTE))
        time.sleep(GAP)
        marks.append((v, t))
    time.sleep(0.3)
    t_start = marks[0][1] - 0.5

a = rec['audio']
print(f'noise floor: {rms_db(seg(a, 0.05, 0.45)):.1f} dBFS')
for v, t in marks:
    s = t - t_start
    x = seg(a, s + 0.05, s + ON - 0.05)
    print(f'vel {v:>3}: {rms_db(x):6.1f} dBFS  pitch ~{pitch_hz(x):6.1f} Hz')
