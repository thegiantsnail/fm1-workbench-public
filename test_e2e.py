"""End-to-end: drive the Workbench in a throwaway Chrome profile (MIDI+SysEx granted), play the drum sequencer and the
MIDI player into the real FM-1, record its audio, and compare audio onsets with the timestamps the app scheduled."""
import time, json, sys, numpy as np
from playwright.sync_api import sync_playwright
from fm1 import recording, SR

URL = 'http://localhost:8731/'
TAP = """
window.__L = []; window.__tap = () => {
  if (!Engine.out) return false; if (Engine.__tapped) return true;
  const send = Engine.send;          // record the INTENDED time of every message
  Engine.send = (d, ts) => { const t = ts && ts > performance.now() ? ts : performance.now(); window.__L.push({ d: Array.from(d), ts: t, wall: performance.timeOrigin + t }); return send(d, ts); };
  Engine.__tapped = true; return true;
};"""

def env(a): return np.sqrt(np.convolve(a ** 2, np.ones(44) / 44, 'same'))
def onsets(e, thr_db=-55, min_gap=0.03):
    thr = 10 ** (thr_db / 20); above = e > thr; out = []; last = -1
    for i in np.flatnonzero(above[1:] & ~above[:-1]) + 1:
        t = i / SR
        if t - last >= min_gap: out.append(t); last = t
    return np.array(out)

def analyse(label, audio, rec_wall0, msgs):
    """Match each scheduled note-on to the nearest recorded onset after a constant latency offset."""
    ons = [m for m in msgs if (m['d'][0] & 0xF0) == 0x90]
    sched = np.array([m['wall'] / 1000 - rec_wall0 for m in ons])          # seconds into the recording
    e = env(audio); det = onsets(e)
    if not len(det) or not len(sched):
        print(f'{label}: no onsets ({len(det)}) or notes ({len(sched)})'); return
    # latency = most common (detected - scheduled) difference
    diffs = np.array([d - s for s in sched for d in det if -0.05 < d - s < 0.4])
    hist, edges = np.histogram(diffs, bins=np.arange(-0.05, 0.4, 0.002))
    lat = edges[np.argmax(hist)] + 0.001
    # onsets closer than ~30 ms merge in the envelope, so only check notes that are isolated in time
    iso = [i for i, s in enumerate(sched) if all(abs(s - x) > 0.035 for j, x in enumerate(sched) if j != i)]
    errs, missing = [], 0
    for i in iso:
        s = sched[i] + lat
        near = det[np.abs(det - s) < 0.03]
        if len(near): errs.append((near[np.argmin(np.abs(near - s))] - s) * 1000)
        else: missing += 1
    per = {}
    for i in iso:
        s = sched[i] + lat; near = det[np.abs(det - s) < 0.03]; n = ons[i]['d'][1]
        per.setdefault(n, []).append(round((near[np.argmin(np.abs(near - s))] - s) * 1000, 1) if len(near) else None)
    for n, v in sorted(per.items()): print(f'    note {n:>3}: ' + ' '.join('miss' if x is None else f'{x:+.1f}' for x in v))
    errs = np.array(errs)
    tail_db = 20 * np.log10(np.sqrt(np.mean(audio[-int(0.4 * SR):] ** 2)) + 1e-9)
    print(f'{label}: {len(sched)} notes sent, {len(det)} onsets heard; latency {lat*1000:.0f} ms; '
          f'isolated notes {len(iso)}: {len(errs)} on time (|err| median {np.median(np.abs(errs)):.1f} ms, p95 {np.percentile(np.abs(errs), 95):.1f} ms, max {np.abs(errs).max():.1f} ms), '
          f'{missing} missing; level after stop {tail_db:.0f} dB' + ('  <-- STUCK NOTE?' if tail_db > -75 else ''))

if __name__ == '__main__':
  with sync_playwright() as pw:
    br = pw.chromium.launch(channel='chrome', headless=True)
    ctx = br.new_context(viewport={'width': 1440, 'height': 900})
    ctx.grant_permissions(['midi', 'midi-sysex'], origin=URL.rstrip('/'))
    page = ctx.new_page()
    page.on('console', lambda m: m.type == 'error' and print('console error:', m.text))
    page.goto(URL); page.evaluate(TAP)
    page.wait_for_function("document.getElementById('midiStatus').textContent.includes('FM-1')", timeout=10000)
    assert page.evaluate('__tap()')
    print('connected:', page.inner_text('#midiStatus'))
    page.evaluate("Engine.panic()")

    # ---- 1) drum sequencer, default kit, 110 BPM, some swing + a ratchet ----
    page.click('button[data-tab=drums]')
    page.evaluate("""() => { const P = Seq.pattern; P.bpm = 110; P.swing = 20; P.tracks[0].steps[14].on = true; P.tracks[0].steps[14].rat = 3; }""")
    for mode in (sys.argv[1:] or ['timestamp', 'jit']):
        page.evaluate(f"Engine.timing = '{mode}'")
        with recording(seconds_max=12) as rec:
            wall0 = time.time(); time.sleep(0.3)
            page.evaluate('__L.length = 0'); page.click('#seqPlay'); time.sleep(6.5); page.click('#seqPlay'); time.sleep(1.2)
        msgs = page.evaluate('__L')
        print(f'  sent {sum(1 for m in msgs if m["d"][0] == 0xF0)} param msgs')
        analyse(f'SEQUENCER [{mode}]', rec['audio'], wall0, msgs)

    # ---- 2) MIDI player: demo.mid, per-channel voices from the kit, ch10 -> kit ----
    page.evaluate("""async () => { const b = await (await fetch('samples/demo.mid')).arrayBuffer(); await Player.openFile(new File([b], 'demo.mid')); }""")
    page.evaluate("""() => { const k = Seq.pattern.tracks; [[0, 4], [1, 5], [2, 1]].forEach(([ch, t]) => {
        document.querySelectorAll('#plChans .cname')[ch].click();
        Bus.dispatchEvent(new CustomEvent('addvoice', { detail: { name: k[t].name, vced: k[t].voices[0].vced } })); }); }""")
    with recording(seconds_max=12) as rec:
        wall0 = time.time(); time.sleep(0.3)
        page.evaluate('__L.length = 0'); page.click('#plPlay'); time.sleep(7.0); page.click('#plPlay'); time.sleep(1.2)
    msgs = page.evaluate('__L')
    analyse('PLAYER', rec['audio'], wall0, msgs)
    page.evaluate("Engine.panic()")
    br.close()
