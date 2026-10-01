"""Timing tests for voice-cycling: measure patch-swap latency and behaviour by recording FM-1 audio.
Uses 3 pure-sine test voices at ratios 1/2/3 (A4 -> 440/880/1320 Hz) so the active patch is readable from pitch.
Edit buffer only; nothing is saved on the unit."""
import time, mido, numpy as np
from fm1 import out_port, recording, SR, sysex, dx7_param
from dx7 import init_voice, vced_sysex, param_index

NOTE = 69
def sine(ratio, name):
    return init_voice(name, ALG=31, ops={1: dict(OL=99, FC=ratio, R4=85)})      # fast release; tail test lengthens it
V = {1: sine(1, 'SINE X1'), 2: sine(2, 'SINE X2'), 3: sine(3, 'SINE X3')}
HZ = {1: 440, 2: 880, 3: 1320}

def track(a, t0, t1, hop=0.001, win=512):
    """(time, dominant freq, rms) frames between t0 and t1."""
    out, w = [], np.hanning(win)
    for t in np.arange(t0, t1, hop):
        i = int(t * SR); x = a[i:i + win]
        if len(x) < win: break
        m = np.abs(np.fft.rfft(x * w)); k = int(np.argmax(m[3:-2])) + 3
        a_, b_, c_ = m[k - 1], m[k], m[k + 1]                       # parabolic peak interpolation
        k2 = k + 0.5 * (a_ - c_) / (a_ - 2 * b_ + c_ + 1e-12)
        out.append((t + win / 2 / SR, k2 * SR / win, np.sqrt(np.mean(x ** 2))))
    return out

def classify(f):
    return min(HZ, key=lambda r: abs(HZ[r] - f)) if f > 200 else 0

def onset(frames, thresh=0.003):
    quiet = False
    for t, f, r in frames:
        if r < thresh * 0.3: quiet = True
        elif quiet and r > thresh: return t
    return None

def switch_time(frames, to):
    return next((t for t, f, r in frames if r > 0.002 and classify(f) == to), None)

T = time.perf_counter
results = {}
with out_port() as out, recording(seconds_max=90) as rec:
    t0 = T()
    now = lambda: T() - t0
    def note_on(): out.send(mido.Message('note_on', note=NOTE, velocity=110)); return now()
    def note_off(): out.send(mido.Message('note_off', note=NOTE)); return now()
    def load(k): sysex(out, vced_sysex(V[k])); return now()
    for c in range(16): out.send(mido.Message('control_change', channel=c, control=123, value=0))
    time.sleep(0.5)

    # 1) note-on latency baseline (x8) and 2) VCED swap on a HELD note (x8)
    held = []
    for i in range(8):
        load(1); time.sleep(0.15)
        tn = note_on(); time.sleep(0.25)
        ts = load(2); time.sleep(0.25)
        note_off(); time.sleep(0.4)
        held.append((tn, ts))
    # 3) swap during RELEASE tail
    tail = []
    for i in range(4):
        load(1); dx7_param(out, param_index(1, 'R4'), 55); time.sleep(0.15)
        tn = note_on(); time.sleep(0.2); toff = note_off(); time.sleep(0.03)
        ts = load(3); time.sleep(0.6)
        tail.append((tn, toff, ts))
    # 4) note-on N ms after VCED: does the new note use the new patch?
    gaps = [0, 1, 2, 4, 8, 16]
    gap_marks = []
    for g in gaps:
        for k in (1, 2):                                     # alternate so each test is a real change
            load(3 - k if k == 2 else 2); time.sleep(0.2)    # preload the *other* patch
            ts = load(k); time.sleep(g / 1000)
            tn = note_on(); time.sleep(0.15); note_off(); time.sleep(0.35)
            gap_marks.append((g, k, ts, tn))
    # 5) single-param change latency on held note (coarse 1->2)
    pmarks = []
    for i in range(6):
        load(1); time.sleep(0.15)
        tn = note_on(); time.sleep(0.25)
        dx7_param(out, param_index(1, 'FC'), 2); tp = now(); time.sleep(0.25)
        note_off(); time.sleep(0.35)
        pmarks.append((tn, tp))
    # 6) rapid alternation while holding: VCED swaps and param swaps at various periods
    rapid = []
    for kind in ('vced', 'param'):
        for period in (40, 20, 10, 5, 2):
            load(1); time.sleep(0.15)
            tn = note_on(); ts = now(); k = 1; sends = []
            while now() - ts < 0.6:
                k = 3 - k
                if kind == 'vced': load(k)
                else: dx7_param(out, param_index(1, 'FC'), k)
                sends.append((now(), k))
                time.sleep(period / 1000)
            toff = note_off(); time.sleep(0.5)
            rapid.append((kind, period, tn, sends, toff))
            for c in range(16): out.send(mido.Message('control_change', channel=c, control=123, value=0))
            time.sleep(0.1)

a = rec['audio']
np.save('dumps/timing_audio.npy', a)
def lat(fr, tsend, to):
    s = switch_time([f for f in fr if f[0] > tsend], to)
    return None if s is None else (s - tsend) * 1000

print('== 1) note-on latency (send -> audio onset, includes USB audio-in latency)')
note_lat = []
for tn, ts in held:
    fr = track(a, tn - 0.05, tn + 0.2); o = onset(fr)
    if o: note_lat.append((o - tn) * 1000)
nl = np.median(note_lat); print(f'   median {nl:.1f} ms, spread {min(note_lat):.1f}-{max(note_lat):.1f} ms')

print('== 2) VCED swap on held note: does the sounding note change? latency relative to note-on latency')
sw = []
for tn, ts in held:
    fr = track(a, ts - 0.05, ts + 0.24)
    l = lat(fr, ts, 2); sw.append(l)
print('   swap->audible (ms):', [None if x is None else round(x, 1) for x in sw])
ok = [x for x in sw if x is not None]
if ok: print(f'   => held notes DO change; swap is ~{np.median(ok) - nl:+.1f} ms vs a note-on')
else: print('   => held notes did NOT change pitch (swap only affects new notes?)')
# look at what happens around swap: continuity (dropout?)
tn, ts = held[0]; fr = track(a, ts - 0.02, ts + 0.06, hop=0.002)
print('   around first swap (t-ts ms, Hz, rms):', [(round((t - ts) * 1000), round(f), round(r, 4)) for t, f, r in fr[::3]])

print('== 3) swap during release tail')
for tn, toff, ts in tail:
    fr = track(a, ts, ts + 0.3); l = lat(fr, ts, 3)
    rr = [r for t, f, r in fr]
    print(f'   tail after swap: {"changed pitch" if l else "unchanged"}{f" ({l:.0f} ms)" if l else ""}, tail rms max {max(rr):.4f}')

print('== 4) note-on N ms after VCED: which patch plays?')
for g, k, ts, tn in gap_marks:
    fr = track(a, tn, tn + 0.14); fr = [x for x in fr if x[2] > 0.003]
    got = [classify(f) for t, f, r in fr]
    first = got[0] if got else None
    maj = max(set(got), key=got.count) if got else None
    print(f'   gap {g:>2} ms, wanted x{k}: first frame x{first}, majority x{maj}' + ('' if maj == k else '   <-- OLD PATCH'))

print('== 5) single param change (7 bytes) on held note')
pl = []
for tn, tp in pmarks:
    fr = track(a, tp - 0.02, tp + 0.2); pl.append(lat(fr, tp, 2))
okp = [x for x in pl if x is not None]
print('   latency (ms):', [None if x is None else round(x, 1) for x in pl], f'=> ~{np.median(okp) - nl:+.1f} ms vs note-on' if okp else '')

print('== 6) rapid alternation while holding (does audio follow?)')
for kind, period, tn, sends, toff in rapid:
    stuck = 20 * np.log10(np.sqrt(np.mean(a[int((toff + 0.3) * SR):int((toff + 0.5) * SR)] ** 2)) + 1e-9)
    fr = track(a, sends[0][0], sends[-1][0] + 0.05, hop=0.0005, win=256)
    seq = [classify(f) for t, f, r in fr if r > 0.002]
    changes = sum(1 for x, y in zip(seq, seq[1:]) if x != y and x and y)
    print(f'   {kind:5s} every {period:>2} ms: {len(sends):>3} sends, audible switches ~{changes:>3}, sounding {len(seq) * 0.5:.0f} ms of {(sends[-1][0] - sends[0][0]) * 1000:.0f}, after note-off {stuck:.0f} dB' + ('  <-- STUCK NOTE' if stuck > -80 else ''))
