"""No-hardware regression test for the review fixes, run in real Chrome with the production security headers
injected (same values deploy_hosting.py sends), and a fake MIDI output that records every message.
Checks: no CSP violations; timer Worker + sequencer run under CSP; library/kits/macros; player incl. a 150k-note
MIDI file; malformed pattern/kit files rejected cleanly; stop with no MIDI output logs no errors."""
import struct, time, json
from playwright.sync_api import sync_playwright
from deploy_hosting import SECURITY_HEADERS

BASE = 'http://localhost:8731'


def big_midi(n_notes=150_000):
    """Format-0 SMF with n_notes short notes (tests the stack-overflow fix)."""
    ev = bytearray()
    for i in range(n_notes):
        ev += bytes([0x00 if i else 0x00, 0x90, 60 + i % 12, 90, 0x10, 0x80, 60 + i % 12, 0])
    ev += bytes([0x00, 0xFF, 0x2F, 0x00])
    return b'MThd' + struct.pack('>IHHH', 6, 0, 1, 96) + b'MTrk' + struct.pack('>I', len(ev)) + bytes(ev)


def wait_js(pg, expr, timeout=180):
    """Poll from Python: page.wait_for_function evals a string inside the page, which our CSP (correctly) forbids."""
    end = time.time() + timeout
    while time.time() < end:
        if pg.evaluate(expr):
            return
        time.sleep(0.2)
    raise TimeoutError(expr)


FAKE = """() => { window.__L = []; Engine.out = { name: 'FM-1 fake', send(d) { __L.push(Array.from(d)); } }; Engine.forgetDevice(); }"""

with sync_playwright() as pw:
    br = pw.chromium.launch(channel='chrome', headless=True)
    ctx = br.new_context(viewport={'width': 1440, 'height': 1000})
    def add_headers(route):
        r = route.fetch()
        route.fulfill(response=r, headers={**r.headers, **SECURITY_HEADERS})
    ctx.route('**/*', add_headers)
    pg = ctx.new_page(); errs, csp = [], []
    pg.on('console', lambda m: m.type in ('error', 'warning') and errs.append(f'{m.type}: {m.text}'))
    pg.on('pageerror', lambda e: errs.append('pageerror: ' + str(e)))
    pg.add_init_script("document.addEventListener('securitypolicyviolation', e => (window.__csp = window.__csp || []).push(e.violatedDirective + ' ' + e.blockedURI))")
    pg.goto(BASE + '/'); pg.wait_for_timeout(800)
    hdr = pg.evaluate("fetch('index.html').then(r => r.headers.get('content-security-policy'))")
    print('CSP header present:', bool(hdr))

    pg.click('#loadBundled'); wait_js(pg, "document.getElementById('loadBundled').textContent.startsWith('Load') && document.getElementById('libCount').textContent.includes('/ 3')")
    print('library:', pg.inner_text('#libCount'))

    pg.evaluate(FAKE)
    pg.click('button[data-tab=drums]'); wait_js(pg, "document.querySelectorAll('#kitSel option').length > 0")
    pg.select_option('#kitSel', index=2); pg.click('#kitLoad')
    pg.click('#seqPlay'); pg.wait_for_timeout(1500); pg.click('#seqPlay'); pg.wait_for_timeout(300)
    L = pg.evaluate('__L')
    ons = sum(1 for m in L if (m[0] & 0xF0) == 0x90); sx = sum(1 for m in L if m[0] == 0xF0)
    print(f'sequencer under CSP: {ons} note-ons, {sx} param msgs (timer Worker from blob: works: {ons > 0})')

    # stop with NO output: must not log TypeErrors
    n_err = len(errs)
    pg.evaluate("Engine.out = null"); pg.click('#seqPlay'); pg.wait_for_timeout(600); pg.click('#seqPlay'); pg.wait_for_timeout(200)
    print('play/stop with no MIDI output: new console errors =', len(errs) - n_err)

    # malformed files
    pg.evaluate(FAKE)
    def upload(sel, name, content):
        pg.set_input_files(sel, files=[{'name': name, 'mimeType': 'application/json', 'buffer': content.encode()}]); pg.wait_for_timeout(300)
    kits_before = pg.evaluate("JSON.parse(localStorage.getItem('fm1.kits') || '[]').length")
    upload('#kitImport', 'bad.json', json.dumps({'name': 'broken', 'tracks': [{'name': 'x'}]}))
    upload('#kitImport', 'junk.json', 'not json at all')
    kits_after = pg.evaluate("JSON.parse(localStorage.getItem('fm1.kits') || '[]').length")
    print(f'bad kit files: saved kits {kits_before} -> {kits_after} (should not grow); last log:', pg.inner_text('#log').split('\n')[0][12:])
    pat_before = pg.evaluate('JSON.stringify(Seq.pattern.tracks.map(t => t.name))')
    upload('#patImport', 'badpat.json', json.dumps({'bpm': 'fast', 'tracks': [{'steps': 'nope', 'len': -5, 'voices': [{'vced': [999]}]}]}))
    p = pg.evaluate('({ bpm: Seq.pattern.bpm, len: Seq.pattern.tracks[0].len, steps: Seq.pattern.tracks[0].steps.length, voices: Seq.pattern.tracks[0].voices.length })')
    print('odd pattern file sanitised to:', p)
    pg.click('#seqPlay'); pg.wait_for_timeout(700); pg.click('#seqPlay')
    upload('#patImport', 'nothing.json', json.dumps({'tracks': []}))
    print('empty pattern rejected, log:', pg.inner_text('#log').split('\n')[0][12:])

    # player: demo + 150k-note file
    pg.evaluate("async () => { const b = await (await fetch('samples/demo.mid')).arrayBuffer(); await Player.openFile(new File([b], 'demo.mid')); }")
    print('demo.mid:', pg.inner_text('#midInfo'))
    data = list(big_midi())
    t = time.time()
    pg.evaluate("async (d) => { await Player.openFile(new File([new Uint8Array(d)], 'huge.mid')); }", data)
    print(f'150k-note file: {pg.inner_text("#midInfo")} ({time.time() - t:.1f}s)')

    csp = pg.evaluate('window.__csp || []')
    print('CSP violations:', csp or 'none')
    print('console errors/warnings:', [e for e in errs if 'MIDI access denied' not in e] or 'none')
    br.close()
