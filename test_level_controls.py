"""Which MIDI messages change the FM-1's output level? Plays a reference sine and measures each candidate."""
import time, mido
from fm1 import out_port, recording, rms_db, seg, sysex, dx7_param
from dx7 import init_voice, vced_sysex, param_index

KEY, FX = 0, 1
sine = init_voice('LEVEL REF', ALG=31, ops={1: dict(OL=99, KVS=0)})
CASES = [
    ('baseline', []),
    ('CC7=20 (volume, key ch)', [('cc', KEY, 7, 20)]),
    ('CC11=20 (expression, key ch)', [('cc', KEY, 11, 20)]),
    ('Universal SysEx master vol 25%', [('sx', [0x7F, 0x7F, 0x04, 0x01, 0x00, 0x20])]),
    ('Distortion on, gain 0, level 20', [('cc', FX, 12, 1), ('cc', FX, 13, 0), ('cc', FX, 15, 20)]),
    ('Distortion on, gain 0, level 100', [('cc', FX, 12, 1), ('cc', FX, 13, 0), ('cc', FX, 15, 100)]),
    ('OP1 output level 99 -> 80 (param)', [('p', param_index(1, 'OL'), 80)]),
    ('velocity 40 (KVS 7 patch)', [('p', param_index(1, 'KVS'), 7), ('vel', 40)]),
]
RESET = [('cc', KEY, 7, 127), ('cc', KEY, 11, 127), ('sx', [0x7F, 0x7F, 0x04, 0x01, 0x7F, 0x7F]), ('cc', FX, 12, 0)]

def apply(o, acts):
    vel = 100
    for a in acts:
        if a[0] == 'cc': o.send(mido.Message('control_change', channel=a[1], control=a[2], value=a[3]))
        elif a[0] == 'sx': sysex(o, a[1])
        elif a[0] == 'p': dx7_param(o, a[1], a[2])
        elif a[0] == 'vel': vel = a[1]
    return vel

with out_port() as o:
    for name, acts in CASES:
        sysex(o, vced_sysex(sine)); apply(o, RESET); time.sleep(1.0)
        vel = apply(o, acts); time.sleep(0.2)
        with recording() as rec:
            time.sleep(0.2); o.send(mido.Message('note_on', note=69, velocity=vel)); time.sleep(0.7); o.send(mido.Message('note_off', note=69)); time.sleep(0.3)
        print(f'{name:36s} {rms_db(seg(rec["audio"], 0.35, 0.85)):6.1f} dBFS')
    sysex(o, vced_sysex(sine)); apply(o, RESET)
