"""Passive MIDI capture from the FM-1. Sends nothing. Usage: python listen.py [seconds]"""
import sys, time, json, pathlib, collections, mido
from fm1 import in_port

SECS = float(sys.argv[1]) if len(sys.argv) > 1 else 75
log_path = pathlib.Path(__file__).parent / 'dumps' / f'capture_{time.strftime("%Y%m%d_%H%M%S")}.jsonl'
log_path.parent.mkdir(exist_ok=True)

counts = collections.Counter()
ccs = collections.defaultdict(lambda: [128, -1, 0])   # (ch, cc) -> [min, max, n]
notes, progs, sysex, other = set(), [], [], collections.Counter()
t0 = time.time()

with in_port() as inp, log_path.open('w') as log:
    print(f'LISTENING for {SECS:.0f}s ...', flush=True)
    while time.time() - t0 < SECS:
        for m in inp.iter_pending():
            if m.type in ('clock', 'active_sensing'):
                other[m.type] += 1
                continue
            t = round(time.time() - t0, 3)
            log.write(json.dumps({'t': t, 'hex': bytes(m.bytes()).hex(' '), 'msg': str(m)}) + '\n')
            counts[m.type] += 1
            if m.type == 'control_change':
                r = ccs[(m.channel + 1, m.control)]
                r[0], r[1], r[2] = min(r[0], m.value), max(r[1], m.value), r[2] + 1
            elif m.type in ('note_on', 'note_off'):
                notes.add((m.channel + 1, m.note))
            elif m.type == 'program_change':
                progs.append((t, m.channel + 1, m.program))
            elif m.type == 'sysex':
                sysex.append((t, bytes(m.bytes())))
        time.sleep(0.002)

print('\n=== SUMMARY ===')
print('message types:', dict(counts), '| realtime:', dict(other))
print('notes (ch, note):', sorted(notes))
print('CCs  (ch, cc): [min, max, count]')
for k in sorted(ccs):
    print(f'  ch{k[0]:>2} CC{k[1]:>3}: {ccs[k]}')
print('program changes (t, ch, prog):', progs)
for t, b in sysex:
    print(f'sysex t={t} len={len(b)}: {b[:24].hex(" ")}{" ..." if len(b) > 24 else ""}')
print('full log:', log_path)
