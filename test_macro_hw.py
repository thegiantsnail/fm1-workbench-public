"""Check drum macros in two layers:
  1) exact: which DX7 parameters each macro changes (computed by the app's own drums.js in Chrome)
  2) audible: play base vs. macro'd voice on the FM-1 (param diffs, hits 3 s apart so the unit's delay/reverb
     echoes don't leak into the next measurement), 3 reps each, and compare level / decay / early-vs-late pitch."""
import json, time, numpy as np, mido
from playwright.sync_api import sync_playwright
from fm1 import out_port, recording, SR
import dx7

CASES = [('level', {'level': -20}), ('decay+', {'decay': 30}), ('decay-', {'decay': -30}), ('release+', {'release': 30}),
         ('tone+', {'tone': 30}), ('punch+sweep', {'punch': 35, 'sweep': 45}), ('grit+', {'grit': 5})]

with sync_playwright() as pw:
    br = pw.chromium.launch(channel='chrome', headless=True)
    pg = br.new_page(); pg.goto('http://localhost:8731/')
    pg.wait_for_function("document.querySelectorAll('#kitSel option').length > 0", timeout=8000)
    data = pg.evaluate("""(cases) => {
        const kits = [...document.querySelectorAll('#kitSel option')];
        document.getElementById('kitSel').value = kits.find(o => o.textContent.startsWith('DX7-DX11-4')).value;
        document.getElementById('kitLoad').click();
        const tr = Seq.pattern.tracks.find(t => t.role === 'kick'), v = tr.voices[0];
        const base = Array.from(v.vced);
        return { note: tr.note, name: v.name, base, cases: cases.map(([n, m]) => [n, DrumMacros.apply(base, m)]) };
    }""", [[n, m] for n, m in CASES])
    br.close()

base = data['base']
print(f"voice {data['name']!r} at note {data['note']}, algorithm {base[134] + 1}\n\n1) parameters changed by each macro:")
for n, v in data['cases']:
    ch = [f'{dx7.param_name(i)} {base[i]}->{v[i]}' for i in range(155) if v[i] != base[i]]
    print(f'   {n:12s} ' + ', '.join(ch))

def play_and_measure(o, dev, vced, note, reps=3):
    res = []
    for _ in range(reps):
        want = list(vced) + [63]
        for i in list(range(145)) + [155]:
            if dev[0] is None or dev[0][i] != want[i]:
                o.send(mido.Message('sysex', data=[0x43, 0x10, (i >> 7) & 3, i & 127, want[i]]))
        dev[0] = want
        with recording(seconds_max=5) as rec:
            time.sleep(0.2); o.send(mido.Message('note_on', note=note, velocity=110)); time.sleep(0.15)
            o.send(mido.Message('note_off', note=note)); time.sleep(2.8)
        a = rec['audio']; env = np.sqrt(np.convolve(a ** 2, np.ones(220) / 220, 'same'))
        s = int(0.15 * SR) + int(np.argmax(env[int(0.15 * SR):] > 10 ** (-85 / 20)))
        pk = s + int(np.argmax(env[s:s + int(0.2 * SR)]))
        # length = until the envelope falls 40 dB below its peak and stays there for 20 ms (within 0.8 s)
        quiet = env[pk:pk + int(0.8 * SR)] < env[pk] * 0.01
        run = np.convolve(quiet.astype(float), np.ones(int(0.02 * SR)), 'valid') >= int(0.02 * SR)
        below = np.flatnonzero(run)
        zc = lambda x: np.sum(np.abs(np.diff(np.sign(x))) > 0) / 2 / (len(x) / SR)
        res.append((20 * np.log10(np.sqrt(np.mean(a[s:s + int(0.06 * SR)] ** 2)) + 1e-9),
                    20 * np.log10((np.sqrt(np.mean(a[s + int(0.06 * SR):s + int(0.3 * SR)] ** 2)) + 1e-9) / (np.sqrt(np.mean(a[s:s + int(0.06 * SR)] ** 2)) + 1e-9)), zc(a[s:s + int(0.02 * SR)]), zc(a[s + int(0.06 * SR):s + int(0.1 * SR)])))
    return np.median(np.array(res), axis=0)

print('\n2) on the FM-1 (median of 3):   level dB | ring dB  | Hz 0-20ms | Hz 60-100ms')
dev = [None]
with out_port() as o:
    for n in range(128): o.send(mido.Message('note_off', note=n))
    b = play_and_measure(o, dev, base, data['note'])
    print(f"   {'base':12s}  {b[0]:7.1f}  | {b[1]:6.1f}   | {b[2]:7.0f}   | {b[3]:7.0f}")
    for n, v in data['cases']:
        r = play_and_measure(o, dev, v, data['note'])
        print(f"   {n:12s}  {r[0]:7.1f}  | {r[1]:6.1f}   | {r[2]:7.0f}   | {r[3]:7.0f}   (level {r[0] - b[0]:+.1f} dB, ring {r[1] - b[1]:+.1f} dB)")
    b2 = play_and_measure(o, dev, base, data['note'])
    print(f"   {'base again':12s}  {b2[0]:7.1f}  | {b2[1]:6.1f}   | {b2[2]:7.0f}   | {b2[3]:7.0f}")
    for n in range(128): o.send(mido.Message('note_off', note=n))
