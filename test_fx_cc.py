"""Verify effect CCs (Effect channel 2) and Program Change against the community chart, by audio measurement."""
import time, mido
from fm1 import out_port, recording, seg, rms_db, centroid_hz

FX_CH = 1           # mido 0-based -> MIDI channel 2
NOTE, ON, TAIL = 60, 0.5, 0.6

def cc(out, n, v): out.send(mido.Message('control_change', channel=FX_CH, control=n, value=v)); time.sleep(0.05)

with out_port() as out, recording() as rec:
    t0, marks = time.time(), []
    def play(label):
        t = time.time() - t0
        out.send(mido.Message('note_on', note=NOTE, velocity=100)); time.sleep(ON)
        out.send(mido.Message('note_off', note=NOTE)); time.sleep(TAIL)
        marks.append((label, t))
    time.sleep(0.3)
    cc(out, 4, 0); cc(out, 8, 0); cc(out, 0, 0)                  # reverb, delay, filter off
    play('dry')
    cc(out, 0, 1); cc(out, 1, 0); cc(out, 3, 3)                    # filter on, low-pass, Q 3
    cc(out, 2, 15); play('LPF cutoff 15')
    cc(out, 2, 60); play('LPF cutoff 60')
    cc(out, 2, 107); play('LPF cutoff 107')
    cc(out, 1, 2); cc(out, 2, 60); play('HPF cutoff 60')
    cc(out, 0, 0)
    cc(out, 4, 1); cc(out, 5, 1); cc(out, 6, 80); cc(out, 7, 80); play('reverb hall mix 80')
    cc(out, 7, 0); play('reverb mix 0')
    cc(out, 4, 0)
    for p in (0, 1, 2):
        out.send(mido.Message('program_change', program=p)); time.sleep(0.3)
        play(f'program change {p}')

a = rec['audio']
for label, t in marks:
    body, tail = seg(a, t + 0.05, t + ON), seg(a, t + ON + 0.15, t + ON + TAIL)
    print(f'{label:22s} body {rms_db(body):6.1f} dB  brightness {centroid_hz(body):6.0f} Hz  tail {rms_db(tail):6.1f} dB')
