"""Write app/samples/demo.mid: 8 bars at 104 BPM - bass (ch1), chords (ch2), lead (ch3), GM drums (ch10)."""
import pathlib, mido

PPQ, BPM = 480, 104
mid = mido.MidiFile(ticks_per_beat=PPQ)
def track(name, events):
    t = mido.MidiTrack(); mid.tracks.append(t)
    t.append(mido.MetaMessage('track_name', name=name, time=0))
    if name == 'Bass': t.append(mido.MetaMessage('set_tempo', tempo=mido.bpm2tempo(BPM), time=0))
    ev = []
    for start, dur, ch, note, vel in events:
        ev += [(start, 1, mido.Message('note_on', channel=ch, note=note, velocity=vel)),
               (start + dur, 0, mido.Message('note_off', channel=ch, note=note, velocity=0))]
    now = 0
    for tick, _, msg in sorted(ev, key=lambda e: (e[0], e[1])):
        t.append(msg.copy(time=tick - now)); now = tick
    return len(events)

q, e8, s16, bar = PPQ, PPQ // 2, PPQ // 4, PPQ * 4
roots = [45, 41, 36, 43] * 2                                  # Am F C G
chords = {45: [57, 60, 64], 41: [57, 60, 65], 36: [55, 60, 64], 43: [55, 59, 62]}
bass = [(b * bar + i * e8, e8 - 20, 0, roots[b] + (12 if i in (3, 6) else 0), 100) for b in range(8) for i in range(8)]
pads = [(b * bar, bar - 10, 1, n, 70) for b in range(8) for n in chords[roots[b]]]
mel = [69, 72, 76, 74, 72, 69, 67, 69]
lead = [(b * bar + k * q + (e8 if k == 3 else 0), q - 40, 2, mel[(b + k) % 8] + (12 if b >= 4 else 0), 95)
        for b in range(8) for k in range(4) if (b + k) % 3 != 2]
drums = []
for b in range(8):
    for i in range(16):
        t = b * bar + i * s16
        if i in (0, 7, 10): drums.append((t, 60, 9, 36, 115))
        if i in (4, 12): drums.append((t, 60, 9, 38, 110))
        if i % 2 == 0: drums.append((t, 40, 9, 42, 70 if i % 4 else 95))
        if b % 2 == 1 and i == 14: drums.append((t, 200, 9, 46, 90))
        if b == 7 and i >= 12: drums.append((t, 60, 9, 45 + (15 - i) * 2, 100))
n = sum(track(nm, ev) for nm, ev in [('Bass', bass), ('Chords', pads), ('Lead', lead), ('Drums', drums)])
out = pathlib.Path(__file__).parent / 'app' / 'samples' / 'demo.mid'
out.parent.mkdir(exist_ok=True); mid.save(out)
print(f'wrote {out}: {n} notes, {mid.length:.2f} s')
