"""Drive the Workbench's Drums tab in a throwaway Chrome profile: load a factory kit, then verify each drum macro by
recording what the FM-1 actually plays (level, decay, pitch sweep), plus the editor round trip and .syx export."""
import time, json, numpy as np
from playwright.sync_api import sync_playwright
from fm1 import recording, SR

def hit_and_measure(page, js_setup='', note_ms=1.2, reps=3):
    rs = [hit_once(page, note_ms) for _ in range(reps)]
    med = lambda k: round(float(np.median([r[k] for r in rs])), 1)
    return {k: med(k) for k in rs[0]}

def hit_once(page, note_ms):
    js_setup = ''
    if js_setup:
        page.evaluate(js_setup)
    with recording(seconds_max=note_ms + 2) as rec:
        time.sleep(0.25)
        page.evaluate("document.querySelector('#seqInspector .mplay').click()")
        time.sleep(note_ms)
    a = rec['audio']
    env = np.sqrt(np.convolve(a ** 2, np.ones(220) / 220, 'same'))
    start = int(0.22 * SR)                      # audition is clicked at 0.25 s; ignore anything before
    if env[:start].max() > 10 ** (-80 / 20): print('   (warning: sound before the hit)')
    on = start + np.flatnonzero(env[start:] > 10 ** (-80 / 20))
    if not len(on):
        return {'level': None}
    s = on[0]
    body = a[s:s + int(0.12 * SR)]
    pk = s + int(np.argmax(env[s:s + int(0.2 * SR)]))
    below = np.flatnonzero(env[pk:] < env[pk] * 0.1)
    def zc_hz(x):                               # zero-crossing rate: works for a 40 Hz kick in a 25 ms window
        return float(np.sum(np.abs(np.diff(np.sign(x))) > 0) / 2 / (len(x) / SR))
    early = a[s:s + int(0.025 * SR)]; late = a[s + int(0.06 * SR):s + int(0.085 * SR)]
    return {'level': round(20 * np.log10(np.sqrt(np.mean(body ** 2)) + 1e-9), 1),
            'decay_ms': int((below[0] if len(below) else len(env) - pk) / SR * 1000),
            'hz_0_25ms': round(zc_hz(early)), 'hz_60_85ms': round(zc_hz(late))}

def set_macro(page, mid, val):
    page.evaluate(f"""() => {{ const defs = DrumMacros.DEFS.map(d => d.id); const i = defs.indexOf('{mid}');
        const inp = document.querySelectorAll('#seqInspector .macro-grid input')[i]; inp.value = {val}; inp.oninput(); }}""")
    time.sleep(1.6)                       # let the drag-audition fire and ring out before we measure

with sync_playwright() as pw:
    br = pw.chromium.launch(channel='chrome', headless=True)
    ctx = br.new_context(viewport={'width': 1440, 'height': 1000}); ctx.grant_permissions(['midi', 'midi-sysex'], origin='http://localhost:8731')
    pg = ctx.new_page(); errs = []; pg.on('console', lambda m: m.type == 'error' and errs.append(m.text))
    pg.goto('http://localhost:8731/'); pg.wait_for_function("document.getElementById('midiStatus').textContent.includes('FM-1')", timeout=10000)
    pg.click('button[data-tab=drums]')
    pg.wait_for_function("document.querySelectorAll('#kitSel option').length > 0", timeout=5000)
    kits = pg.evaluate("[...document.querySelectorAll('#kitSel option')].map(o => o.textContent)")
    print('kits in menu:', kits)
    pg.select_option('#kitSel', label=next(k for k in kits if k.startswith('DX7-DX11-4')))
    pg.click('#kitLoad')
    print('pads:', pg.evaluate("[...document.querySelectorAll('#pads .pad')].map(p => p.innerText.replace(/\\n/g, ' '))"))
    pg.click('#pads .pad >> nth=0')          # kick becomes the active track
    time.sleep(0.5)
    print('macro panel:', pg.inner_text('#seqInspector .macro-head').replace('\n', ' '))

    base = hit_and_measure(pg); print('\nkick as loaded      ', base)
    set_macro(pg, 'level', -20); r = hit_and_measure(pg); print('level -20 (-15 dB)  ', r, f"-> {r['level'] - base['level']:+.1f} dB (expected -15)")
    set_macro(pg, 'level', 0)
    set_macro(pg, 'decay', 30); r = hit_and_measure(pg); print('decay +30 (longer)  ', r)
    set_macro(pg, 'decay', -30); r = hit_and_measure(pg); print('decay -30 (shorter) ', r)
    set_macro(pg, 'decay', 0)
    set_macro(pg, 'punch', 35); set_macro(pg, 'sweep', 45); r = hit_and_measure(pg); print('punch +35 sweep 45  ', r)
    pg.evaluate("document.querySelector('#seqInspector .mreset').click()"); time.sleep(0.4)
    r = hit_and_measure(pg); print('after Reset         ', r)

    # editor round trip: open kick in editor, drop OP1 level, Save to track
    pg.evaluate("document.querySelector('#seqInspector .medit').click()")
    btn = pg.inner_text('#saveToTarget'); print('\neditor target button:', btn)
    pg.evaluate("() => { S.voice.ops[0].OL = Math.max(0, S.voice.ops[0].OL - 30); S.voice.name = 'KICK EDIT '; }")
    pg.click('#saveToTarget')
    print('after save -> tab:', pg.evaluate('S.tab'), '| track voice:', pg.evaluate("Seq.pattern.tracks[0].voices[0].name"),
          '| button hidden:', pg.evaluate("document.getElementById('saveToTarget').hidden"))

    # kit save + .syx bank export
    pg.evaluate("window.prompt = () => 'Test kit'"); pg.click('#kitSave')
    print('my kits:', pg.evaluate("JSON.parse(localStorage.getItem('fm1.kits')).map(k => k.name + ':' + k.tracks.length)"))
    syx = pg.evaluate("Array.from((() => { const k = { tracks: Seq.pattern.tracks.filter(t => t.voices.length).map(t => ({ voices: t.voices })) }; "
                      "return DX7.vmemSysex(k.tracks.flatMap(t => t.voices).map(v => { const x = DX7.fromVced(Seq.voiceVced(v)); x.name = v.name; return x; })); })())")
    names = [bytes(syx[6 + i * 128 + 118:6 + i * 128 + 128]).decode() for i in range(8)]
    print('.syx bank:', len(syx), 'bytes; first voices:', names)
    pg.evaluate("Engine.panic()")
    print('console errors:', errs)
    br.close()
