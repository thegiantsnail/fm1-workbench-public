"""What does one VCED voice dump do to the audio engine? Plots envelopes around the dump for several scenarios."""
import time, mido, numpy as np
import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt
from fm1 import out_port, recording, SR, sysex, dx7_param
from dx7 import init_voice, vced_sysex, param_index

V1 = init_voice('SINE X1', ALG=31, ops={1: dict(OL=99, FC=1, R4=90)})
V2 = init_voice('SINE X2', ALG=31, ops={1: dict(OL=99, FC=2, R4=90)})
T = time.perf_counter

def panic(o):
    for n in range(128): o.send(mido.Message('note_off', note=n))

def scenario(o, name, body):
    panic(o); sysex(o, vced_sysex(V1)); time.sleep(1.2)   # long settle
    with recording(seconds_max=5) as rec:
        t0 = T(); time.sleep(0.3)
        ev = body(o, lambda: T() - t0)
        time.sleep(0.6)
    panic(o)
    return name, rec['audio'], ev

def cal_note(o, now):              # reference note, then the scenario
    ev = [('ref note', now())]
    o.send(mido.Message('note_on', note=69, velocity=110)); time.sleep(0.2); o.send(mido.Message('note_off', note=69)); time.sleep(0.4)
    return ev

def s_dump_silent(o, now):
    ev = cal_note(o, now); ev.append(('VCED', now())); sysex(o, vced_sysex(V2)); time.sleep(1.0); return ev

def s_dump_held(o, now):
    ev = cal_note(o, now); ev.append(('note on', now())); o.send(mido.Message('note_on', note=69, velocity=110)); time.sleep(0.4)
    ev.append(('VCED', now())); sysex(o, vced_sysex(V2)); time.sleep(0.6)
    ev.append(('note off', now())); o.send(mido.Message('note_off', note=69)); return ev

def s_param_held(o, now):
    ev = cal_note(o, now); ev.append(('note on', now())); o.send(mido.Message('note_on', note=69, velocity=110)); time.sleep(0.4)
    ev.append(('param', now())); dx7_param(o, param_index(1, 'FC'), 2); time.sleep(0.6)
    ev.append(('note off', now())); o.send(mido.Message('note_off', note=69)); return ev

def s_dump_then_notes(o, now):
    ev = cal_note(o, now); ev.append(('VCED', now())); sysex(o, vced_sysex(V2))
    t = now()
    for d in (0.03, 0.1, 0.2, 0.35, 0.55, 0.8):
        while now() - t < d: time.sleep(0.001)
        ev.append((f'+{int(d*1000)}', now())); o.send(mido.Message('note_on', note=69, velocity=110)); time.sleep(0.05); o.send(mido.Message('note_off', note=69))
    return ev

with out_port() as o:
    res = [scenario(o, n, f) for n, f in [('VCED into silence', s_dump_silent), ('VCED on held note', s_dump_held),
                                            ('param change on held note', s_param_held), ('notes after VCED', s_dump_then_notes)]]

fig, axs = plt.subplots(len(res), 1, figsize=(12, 2.6 * len(res)))
for ax, (name, a, ev) in zip(axs, res):
    e = np.sqrt(np.convolve(a ** 2, np.ones(88) / 88, 'same'))
    env_db = 20 * np.log10(e + 1e-6)
    # align using the reference note's onset
    ref = ev[0][1]; idx = np.flatnonzero(e[int(ref * SR):] > 10 ** (-60 / 20))
    off = idx[0] / SR if len(idx) else 0
    t = np.arange(len(a)) / SR - off
    ax.plot(t, env_db, lw=0.8); ax.set_xlim(-0.1, 1.9); ax.set_ylim(-100, -20); ax.set_title(f'{name}  (audio offset {off*1000:.1f} ms)', fontsize=9)
    for lab, te in ev:
        ax.axvline(te - ref, color='r', lw=0.6); ax.text(te - ref, -25, lab, fontsize=7, color='r')
    # pitch track (dominant freq) on twin axis
    hop, win = 256, 1024
    fr = [(i / SR - off, np.argmax(np.abs(np.fft.rfft(a[i:i + win] * np.hanning(win)))[5:]) * SR / win + 5 * SR / win)
          for i in range(0, len(a) - win, hop) if np.sqrt(np.mean(a[i:i + win] ** 2)) > 0.001]
    if fr:
        ax2 = ax.twinx(); ax2.scatter(*zip(*fr), s=2, color='g'); ax2.set_ylim(0, 1500); ax2.set_ylabel('Hz', fontsize=7)
    print(name, [(lab, round(te, 3)) for lab, te in ev])
plt.tight_layout(); plt.savefig('dumps/vced_effect.png', dpi=90)
print('saved dumps/vced_effect.png')
