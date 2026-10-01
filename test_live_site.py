"""Smoke-test the deployed site against the real FM-1: MIDI connect, library + kits load, drum pattern plays
(verified by recording the unit's USB audio), player demo parses. Usage: python test_live_site.py [url]"""
import sys, time, numpy as np
from playwright.sync_api import sync_playwright
from fm1 import recording, SR

URL = (sys.argv[1] if len(sys.argv) > 1 else 'https://fm1-workbench.web.app').rstrip('/')

with sync_playwright() as pw:
    br = pw.chromium.launch(channel='chrome', headless=True)
    ctx = br.new_context(viewport={'width': 1440, 'height': 1000}); ctx.grant_permissions(['midi', 'midi-sysex'], origin=URL)
    pg = ctx.new_page(); errs = []
    pg.on('console', lambda m: m.type == 'error' and errs.append(m.text)); pg.on('pageerror', lambda e: errs.append(str(e)))
    t = time.time(); pg.goto(URL + '/'); print(f'loaded {URL} in {time.time() - t:.1f}s, secure context:', pg.evaluate('isSecureContext'))
    pg.wait_for_function("document.getElementById('midiStatus').textContent.includes('FM-1')", timeout=15000)
    print('MIDI:', pg.inner_text('#midiStatus'))
    t = time.time(); pg.click('#loadBundled')
    pg.wait_for_function("document.getElementById('libCount').textContent.split('/')[1]?.trim() > 30000", timeout=180000)
    print(f'library: {pg.inner_text("#libCount")} ({time.time() - t:.1f}s)')
    pg.click('button[data-tab=drums]'); pg.wait_for_function("document.querySelectorAll('#kitSel option').length > 0", timeout=15000)
    print('kits:', pg.evaluate("document.querySelectorAll('#kitSel option').length"))
    pg.select_option('#kitSel', index=0); pg.click('#kitLoad')
    with recording(seconds_max=8) as rec:
        time.sleep(0.3); pg.click('#seqPlay'); time.sleep(3.5); pg.click('#seqPlay'); time.sleep(1.0)
    a = rec['audio']; env = np.sqrt(np.convolve(a ** 2, np.ones(220) / 220, 'same'))
    up = np.flatnonzero((env[1:] > 10 ** (-80 / 20)) & (env[:-1] <= 10 ** (-80 / 20)))
    print(f'drum pattern -> FM-1 audio: peak {20 * np.log10(np.abs(a).max() + 1e-9):.1f} dBFS, {len(up)} onsets heard, '
          f'after stop {20 * np.log10(np.sqrt(np.mean(a[-int(0.3 * SR):] ** 2)) + 1e-9):.0f} dB')
    pg.evaluate("async () => { const b = await (await fetch('samples/demo.mid')).arrayBuffer(); await Player.openFile(new File([b], 'demo.mid')); }")
    print('player:', pg.inner_text('#midInfo'))
    pg.evaluate('Engine.panic()'); print('console errors:', errs or 'none'); br.close()
