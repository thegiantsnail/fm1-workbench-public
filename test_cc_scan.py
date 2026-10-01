"""Test 2: which CCs does the FM-1 respond to? For each CC, play a note at CC=0 and CC=127 and compare audio.
Skips data-entry/RPN/NRPN (6,38,96-101) and channel-mode CCs (120-127). Nothing is saved on the unit."""
import time, mido, json, pathlib
from fm1 import out_port, recording, seg, rms_db, pitch_hz, centroid_hz

NOTE, ON, TAIL = 64, 0.35, 0.3
SKIP = {6, 38, 96, 97, 98, 99, 100, 101}
CCS = [c for c in range(0, 120) if c not in SKIP]

with out_port() as out, recording(seconds_max=260) as rec:
    t0 = time.time()
    marks = []
    time.sleep(0.3)
    for cc in CCS:
        for val in (0, 127):
            out.send(mido.Message('control_change', control=cc, value=val))
            time.sleep(0.05)
            t = time.time() - t0
            out.send(mido.Message('note_on', note=NOTE, velocity=100))
            time.sleep(ON)
            out.send(mido.Message('note_off', note=NOTE))
            time.sleep(TAIL)
            marks.append((cc, val, t))
    out.send(mido.Message('control_change', control=121, value=0))   # reset controllers

a = rec['audio']
m = {}
for cc, val, t in marks:
    body, tail = seg(a, t + 0.05, t + ON), seg(a, t + ON + 0.08, t + ON + TAIL)
    m.setdefault(cc, {})[val] = dict(rms=rms_db(body), cent=centroid_hz(body), pitch=pitch_hz(body), tail=rms_db(tail))

rows = []
for cc in CCS:
    lo, hi = m[cc][0], m[cc][127]
    d = {k: hi[k] - lo[k] for k in lo}
    hit = abs(d['rms']) > 1.5 or abs(d['cent']) > 150 or abs(d['pitch']) > 5 or abs(d['tail']) > 6
    rows.append(dict(cc=cc, hit=hit, **{f'd_{k}': round(float(v), 1) for k, v in d.items()},
                     lo=lo, hi=hi))
    if hit:
        print(f'CC{cc:>3}: loud {d["rms"]:+6.1f} dB  bright {d["cent"]:+7.0f} Hz  pitch {d["pitch"]:+6.1f} Hz  tail {d["tail"]:+6.1f} dB'
              f'   (lo {lo["rms"]:.0f}dB/{lo["cent"]:.0f}Hz  hi {hi["rms"]:.0f}dB/{hi["cent"]:.0f}Hz)')
pathlib.Path('dumps/cc_scan.json').write_text(json.dumps(rows, default=float, indent=1))
print(f'\n{sum(r["hit"] for r in rows)} of {len(rows)} CCs changed the sound. Full data: dumps/cc_scan.json')
