"""Engine timing on hardware with clean sine voices: every note switches voice (4 voices rotate), swing on."""
import time, sys, numpy as np
from playwright.sync_api import sync_playwright
from fm1 import recording, SR
from test_e2e import TAP, env, onsets

SETUP = """(mode) => {
  Engine.timing = mode;
  const sine = (fc) => { const v = DX7.initVoice('SINE ' + fc); v.g.ALG = 31; v.ops.forEach(o => { o.OL = 0; o.R4 = 99; }); v.ops[0].OL = 99; v.ops[0].FC = fc; return DX7.toVced(v); };
  const P = Seq.pattern; P.bpm = 120; P.swing = 20; P.human = 0; P.glitch = 0; P.fx.cc = -1;
  P.tracks.forEach((t, i) => { t.mute = i > 3; t.solo = false; t.len = 16; t.gate = 60; t.note = 69; t.mode = 'fixed';
    t.voices = [{ name: 'S' + (i + 1), vced: sine(i + 1) }];
    t.steps.forEach((s, k) => Object.assign(s, { on: i <= 3 && k % 4 === i, vel: 110, prob: 100, rat: 1, nudge: 0, pitch: 0, pl: null })); });
}"""
def run(page, mode):
    page.evaluate(SETUP, mode)
    with recording(seconds_max=12) as rec:
        wall0 = time.time(); time.sleep(0.3)
        page.evaluate('__L.length = 0'); page.click('#seqPlay'); time.sleep(5.0); page.click('#seqPlay'); time.sleep(0.8)
    msgs = page.evaluate('__L'); a = rec['audio']
    ons = [m for m in msgs if (m['d'][0] & 0xF0) == 0x90]
    sched = np.array([m['wall'] / 1000 - wall0 for m in ons])
    det = onsets(env(a), thr_db=-60)
    diffs = np.array([d - s for s in sched for d in det if -0.05 < d - s < 0.4])
    h, ed = np.histogram(diffs, bins=np.arange(-0.05, 0.4, 0.001)); lat = ed[np.argmax(h)] + 0.0005
    err, miss, wrong = [], 0, 0
    hz = {1: 440, 2: 880, 3: 1320, 4: 1760}
    for m, s in zip(ons, sched):
        near = det[np.abs(det - (s + lat)) < 0.03]
        if not len(near): miss += 1; continue
        o = near[np.argmin(np.abs(near - s - lat))]; err.append((o - s - lat) * 1000)
    err = np.array(err)
    switches = sum(1 for m in msgs if m['d'][0] == 0xF0)
    print(f'[{mode:9s}] {len(ons)} notes, {switches} param msgs, {miss} missing | onset error vs intended: '
          f'median {np.median(np.abs(err)):.1f} ms, p95 {np.percentile(np.abs(err), 95):.1f} ms, max {np.abs(err).max():.1f} ms, spread {err.min():+.1f}..{err.max():+.1f}')

with sync_playwright() as pw:
    br = pw.chromium.launch(channel='chrome', headless=True)
    ctx = br.new_context(); ctx.grant_permissions(['midi', 'midi-sysex'], origin='http://localhost:8731')
    page = ctx.new_page(); page.goto('http://localhost:8731/'); page.evaluate(TAP)
    page.wait_for_function("document.getElementById('midiStatus').textContent.includes('FM-1')", timeout=10000)
    page.evaluate('__tap()'); page.click('button[data-tab=drums]')
    for mode in ['timestamp', 'jit', 'timestamp', 'jit']:
        run(page, mode)
    page.evaluate('Engine.panic()'); br.close()
