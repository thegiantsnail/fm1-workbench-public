"""Web sequencer upgrades, checked in Chrome with a fake MIDI output: accent, per-step gate, slide/tie, triplet rate,
64 steps, chaining across slots, Shift/Alt-drag move/copy, and old 32-step patterns still loading."""
import json, time
from playwright.sync_api import sync_playwright

SETUP = """() => {
  window.__L = []; Engine.out = { name: 'FM-1 fake', send(d) { __L.push([performance.now(), Array.from(d)]); } }; Engine.forgetDevice();
}"""
RUN = """async (ms) => { __L.length = 0; document.getElementById('seqPlay').click(); await new Promise(r => setTimeout(r, ms));
  document.getElementById('seqPlay').click(); await new Promise(r => setTimeout(r, 300));
  // pair note-ons with their note-offs per key (ignoring retrigger/stop sweeps after the run)
  const ev = []; const open = {};
  for (const [t, m] of __L) {
    if ((m[0] & 0xF0) === 0x90) { open[m[1]] = open[m[1]] || []; open[m[1]].push({ t, note: m[1], vel: m[2] }); ev.push(open[m[1]][open[m[1]].length - 1]); }
    else if ((m[0] & 0xF0) === 0x80 && open[m[1]] && open[m[1]].length) { const e = open[m[1]].shift(); e.off = t; }
  }
  return ev.map(e => ({ t: Math.round(e.t), note: e.note, vel: e.vel, len: e.off ? Math.round(e.off - e.t) : null }));
}"""


def pattern(page, js):
    page.evaluate("(src) => { const P = Seq.pattern; P.bpm = 120; P.swing = 0; P.human = 0; P.glitch = 0; P.rate = '1/16';"
                  " P.tracks.forEach(t => { t.mute = false; t.solo = false; t.mode = 'fixed'; t.len = 16; t.ratPitch = 0; t.plock = -1; t.steps.forEach(s => Object.assign(s, { on: false, vel: 100, prob: 100, rat: 1, nudge: 0, pitch: 0, pl: null, gate: null, acc: false, slide: false })); });"
                  " new Function('P', src)(P); }", js)


with sync_playwright() as pw:
    br = pw.chromium.launch(channel='chrome', headless=True)
    pg = br.new_page(viewport={'width': 1440, 'height': 1000}); errs = []
    pg.on('pageerror', lambda e: errs.append(str(e))); pg.on('console', lambda m: m.type == 'error' and 'MIDI' not in m.text and errs.append(m.text))
    pg.goto('http://localhost:8731/'); time.sleep(0.8)
    pg.click('button[data-tab=drums]'); pg.evaluate(SETUP)
    ok = lambda c, msg: print(('  ok   ' if c else '  FAIL ') + msg)

    # accent + per-step gate (120 BPM, 1/16 = 125 ms)
    pattern(pg, "P.tracks[0].steps[0].on = true; P.tracks[0].steps[0].acc = true; P.tracks[0].steps[0].vel = 60;"
                "P.tracks[0].steps[4].on = true; P.tracks[0].steps[4].gate = 50; P.tracks[0].gate = 400;")
    ev = pg.evaluate(RUN, 700)
    k = [e for e in ev if e['note'] == 48]
    ok(k[0]['vel'] == 127, f"accent plays at full velocity (vel {k[0]['vel']}, step vel was 60)")
    ok(abs(k[1]['len'] - 62.5) < 6, f"step gate 50% = 62.5 ms (got {k[1]['len']} ms; track gate is 400 ms)")

    # slide: legato into the next note; tie over empty steps
    pattern(pg, "const s = P.tracks[0].steps; s[0].on = true; s[0].slide = true; s[1].on = true; s[1].pitch = 5;"
                "s[4].on = true; s[4].slide = true; s[7].on = true; s[7].pitch = 7; P.tracks[0].gate = 50;")
    ev = pg.evaluate(RUN, 1100)
    a, b, c, d = ev[0], ev[1], ev[2], ev[3]
    ok(a['t'] + a['len'] > b['t'], f"slide overlaps the next note (off at +{a['len']} ms, next note at +{b['t'] - a['t']} ms)")
    ok(abs(c['len'] - (3 * 125 + 15)) < 8, f"tie over 2 empty steps lasts 3 steps + overlap = 390 ms (got {c['len']} ms)")

    # triplet rate
    pattern(pg, "P.rate = '1/16T'; [0, 1, 2, 3].forEach(i => P.tracks[0].steps[i].on = true);")
    ev = pg.evaluate(RUN, 500)
    gaps = [ev[i + 1]['t'] - ev[i]['t'] for i in range(3)]
    ok(all(abs(g - 83.3) < 4 for g in gaps), f"1/16T steps are 83.3 ms apart (got {gaps})")

    # 64 steps
    pattern(pg, "P.tracks[0].len = 64; P.tracks[0].steps[0].on = true; P.tracks[0].steps[40].on = true;")
    ev = pg.evaluate(RUN, 5400)
    k = [e for e in ev if e['note'] == 48][:3]
    ok(len(k) >= 2 and abs(k[1]['t'] - k[0]['t'] - 40 * 125) < 8, f"step 41 of 64 plays 5000 ms after step 1 (got {k[1]['t'] - k[0]['t'] if len(k) > 1 else None} ms)")

    # chaining: slot 1 = kick on step 1, slot 2 = snare on step 1; chain "1 2", pattern length 4
    pattern(pg, "P.len = 4; P.tracks[0].steps[0].on = true;"); pg.evaluate("() => { document.getElementById('patSlot').value = '1'; document.getElementById('patSave').click(); }")
    pattern(pg, "P.len = 4; P.tracks[1].steps[0].on = true;"); pg.evaluate("() => { document.getElementById('patSlot').value = '2'; document.getElementById('patSave').click(); }")
    pg.evaluate("() => { const c = document.getElementById('chainOn'); c.checked = true; c.onchange(); const s = document.getElementById('chainSlots'); s.value = '1 2'; s.onchange(); }")
    ev = pg.evaluate(RUN, 2100)
    order = [e['note'] for e in ev][:4]
    ok(order == [48, 60, 48, 60], f"chain 1 2 alternates kick/snare every 4 steps (got notes {order}, at {[e['t'] - ev[0]['t'] for e in ev[:4]]} ms)")
    pg.evaluate("() => { const c = document.getElementById('chainOn'); c.checked = false; c.onchange(); }")

    # Shift-drag moves a step, Alt-drag copies it (real pointer events on the grid)
    pattern(pg, "P.tracks[0].steps[0].on = true; P.tracks[0].steps[0].acc = true; P.tracks[0].steps[0].rat = 3;")
    pg.evaluate("() => { document.querySelector('#seqPage button').click(); }")
    cell = lambda t, i: pg.locator(f'.cell[data-t="{t}"][data-i="{i}"]').bounding_box()
    def drag(mod, a, b):
        pa, pb = cell(*a), cell(*b)
        pg.keyboard.down(mod); pg.mouse.move(pa['x'] + 5, pa['y'] + 5); pg.mouse.down(); pg.mouse.move(pb['x'] + 5, pb['y'] + 5, steps=4); pg.mouse.up(); pg.keyboard.up(mod)
    drag('Shift', (0, 0), (0, 5))
    st = pg.evaluate("[Seq.pattern.tracks[0].steps[0].on, JSON.stringify(Seq.pattern.tracks[0].steps[5])]")
    ok(st[0] is False and json.loads(st[1])['acc'] and json.loads(st[1])['rat'] == 3, f"Shift-drag moved step 1 -> 6 with accent and ratchet ({st[1]})")
    drag('Alt', (0, 5), (1, 9))
    st = pg.evaluate("[Seq.pattern.tracks[0].steps[5].on, JSON.stringify(Seq.pattern.tracks[1].steps[9])]")
    ok(st[0] and json.loads(st[1])['rat'] == 3, f"Alt-drag copied it to Snare step 10 and kept the original")

    # an old 32-step pattern still loads (padded to 64)
    old = pg.evaluate("() => { const p = JSON.parse(JSON.stringify(Seq.pattern)); p.tracks.forEach(t => { t.steps = t.steps.slice(0, 32); t.steps.forEach(s => { delete s.gate; delete s.acc; delete s.slide; }); }); delete p.rate; delete p.len; return JSON.stringify(p); }")
    pg.set_input_files('#patImport', files=[{'name': 'old.json', 'mimeType': 'application/json', 'buffer': old.encode()}]); time.sleep(0.4)
    r = pg.evaluate("[Seq.pattern.tracks[0].steps.length, Seq.pattern.rate, Seq.pattern.len]")
    ok(r == [64, '1/16', 16], f"old 32-step pattern imports as 64 steps, rate 1/16, length 16 ({r})")
    print('  page errors:', errs or 'none')
    br.close()
