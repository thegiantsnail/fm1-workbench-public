"""Test 3: send a pure-sine DX7 voice to the edit buffer, verify by audio; then live param changes.
Edit buffer only - nothing is stored unless you press save on the unit."""
import time, mido
from fm1 import out_port, recording, seg, rms_db, pitch_hz, sine_purity, sysex, dx7_param
from dx7 import init_voice, vced_sysex, param_index

NOTE = 69  # A4 = 440 Hz

def play(out, marks, label, vel=100, dur=0.8):
    marks.append((label, time.time(), dur))
    out.send(mido.Message('note_on', note=NOTE, velocity=vel))
    time.sleep(dur)
    out.send(mido.Message('note_off', note=NOTE))
    time.sleep(0.5)

sine = init_voice('CLAUDESINE', ALG=31, ops={1: dict(OL=99, KVS=7)})

with out_port() as out, recording() as rec:
    t0 = time.time()
    marks = []
    time.sleep(0.4)
    play(out, marks, 'before patch (current preset)')
    sysex(out, vced_sysex(sine)); time.sleep(0.4)
    play(out, marks, 'after sine patch, vel 100')
    for v in (20, 60, 127):
        play(out, marks, f'sine vel {v}', vel=v)
    dx7_param(out, param_index(1, 'FC'), 2); time.sleep(0.2)        # coarse ratio 1 -> 2
    play(out, marks, 'param change: OP1 coarse=2')
    dx7_param(out, param_index(1, 'FC'), 1); time.sleep(0.2)
    dx7_param(out, param_index(1, 'OL'), 80); time.sleep(0.2)       # output level 99 -> 80
    play(out, marks, 'param change: OP1 level=80')
    dx7_param(out, param_index(1, 'OL'), 99)
    dx7_param(out, param_index(2, 'OL'), 99); dx7_param(out, param_index(None, 'ALG'), 0); time.sleep(0.2)
    play(out, marks, 'ALG1: OP2 modulates OP1')              # should turn sine into bright FM tone

a = rec['audio']
for label, t, dur in marks:
    x = seg(a, t - t0 + 0.08, t - t0 + dur - 0.05)
    print(f'{label:32s} {rms_db(x):6.1f} dBFS  peak {pitch_hz(x):7.1f} Hz  purity {sine_purity(x):.2f}')
