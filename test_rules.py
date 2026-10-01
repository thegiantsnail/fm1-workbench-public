"""Pin down sequencer rules:
 A) minimum gap after a VCED dump before a note-on plays with the new patch (0..30 ms)
 B) param change while a note is held -> is the note-off lost? does a second note-off free it?
Alignment: a reference note at the start of each recording gives the send->audio offset."""
import time, mido, numpy as np
from fm1 import out_port, recording, SR, sysex, dx7_param
from dx7 import init_voice, vced_sysex, param_index

V = {k: init_voice(f'SINE X{k}', ALG=31, ops={1: dict(OL=99, FC=k, R4=90)}) for k in (1, 2)}
HZ = {1: 440, 2: 880}
T = time.perf_counter
def panic(o):
    for n in range(128): o.send(mido.Message('note_off', note=n))
def env(a): return np.sqrt(np.convolve(a ** 2, np.ones(88) / 88, 'same'))
def db(x): return 20 * np.log10(np.sqrt(np.mean(x ** 2)) + 1e-9)
def pitch(x):
    m = np.abs(np.fft.rfft(x * np.hanning(len(x)))); m[:10] = 0
    return np.argmax(m) * SR / len(x)
def onset(e, t0, t1, thr=10 ** (-60 / 20)):
    i = np.flatnonzero(e[int(t0 * SR):int(t1 * SR)] > thr)
    return t0 + i[0] / SR if len(i) else None

def session(body, secs):
    with out_port() as o:
        panic(o); sysex(o, vced_sysex(V[1])); time.sleep(0.8)
        with recording(seconds_max=secs) as rec:
            t0 = T(); now = lambda: T() - t0
            time.sleep(0.2)
            ref = now(); o.send(mido.Message('note_on', note=69, velocity=110)); time.sleep(0.15); o.send(mido.Message('note_off', note=69)); time.sleep(0.5)
            ev = body(o, now)
            time.sleep(0.5)
        panic(o)
    a = rec['audio']; e = env(a)
    off = onset(e, 0, ref + 1.0) - ref
    return a, e, off, ev

# ---- A) gap sweep, VCED between notes only ----
GAPS = [0, 3, 6, 10, 15, 20, 30]
def body_a(o, now):
    ev = []
    for rep in range(4):
        for g in GAPS:
            k = 1 + (len(ev) % 2)                       # alternate target so every dump is a real change
            sysex(o, vced_sysex(V[k])); ts = now()
            while now() - ts < g / 1000: pass           # busy-wait for accurate short gaps
            tn = now(); o.send(mido.Message('note_on', note=69, velocity=110))
            time.sleep(0.15); o.send(mido.Message('note_off', note=69)); time.sleep(0.25)
            ev.append((g, k, tn))
    return ev
a, e, off, ev = session(body_a, 60)
print(f'A) note-on N ms after VCED (offset {off*1000:.0f} ms)')
print('   gap | played | new patch | extra delay vs clean note')
for g in GAPS:
    rows = [x for x in ev if x[0] == g]; played = new = 0; dl = []
    for _, k, tn in rows:
        on = onset(e, tn + off - 0.02, tn + off + 0.3)
        if on is None: continue
        played += 1; dl.append((on - tn - off) * 1000)
        new += abs(pitch(a[int((on + 0.02) * SR):int((on + 0.12) * SR)]) - HZ[k]) < 60
    print(f'   {g:>2}ms |  {played}/{len(rows)}   |   {new}/{played}     | {np.median(dl):+5.1f} ms' if dl else f'   {g:>2}ms |  0/{len(rows)}')

# ---- B) param change during held note ----
def body_b(o, now):
    ev = []
    for case in ('none', 'param', 'param', 'param', 'vced', 'vced'):
        sysex(o, vced_sysex(V[1])); time.sleep(0.1)
        o.send(mido.Message('note_on', note=69, velocity=110)); time.sleep(0.25)
        if case == 'param': dx7_param(o, param_index(1, 'FC'), 2)
        if case == 'vced': sysex(o, vced_sysex(V[2]))
        time.sleep(0.2)
        t1 = now(); o.send(mido.Message('note_off', note=69)); time.sleep(0.35)
        t2 = now(); o.send(mido.Message('note_off', note=69)); time.sleep(0.35)
        ev.append((case, t1, t2))
        panic(o); time.sleep(0.15)
    return ev
a, e, off, ev = session(body_b, 20)
print(f'\nB) change while note held, then note-off (offset {off*1000:.0f} ms)')
for case, t1, t2 in ev:
    after1 = db(a[int((t1 + off + 0.12) * SR):int((t1 + off + 0.33) * SR)])
    after2 = db(a[int((t2 + off + 0.12) * SR):int((t2 + off + 0.33) * SR)])
    print(f'   {case:5s}: after 1st note-off {after1:6.1f} dB {"STUCK" if after1 > -70 else "released"}'
          f' | after 2nd note-off {after2:6.1f} dB {"STUCK" if after2 > -70 else "released"}')
