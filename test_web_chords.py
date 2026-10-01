"""Web app regression: every note-on in the MIDI player plays with its own channel's voice (the chord bug: 2nd/3rd
chord notes went out before the pending voice diff and sounded with the previous channel's voice)."""
import time
from playwright.sync_api import sync_playwright

JS = """async () => {
  const L = []; Engine.out = { name: 'FM-1 fake', send(d) { L.push(Array.from(d)); } }; Engine.forgetDevice();
  const b = await (await fetch('samples/demo.mid')).arrayBuffer(); await Player.openFile(new File([b], 'demo.mid'));
  const voice = fc => { const v = DX7.initVoice('CH' + fc); v.g.ALG = 31; v.ops.forEach(o => o.OL = 0); v.ops[0].OL = 99; v.ops[0].FC = fc; return DX7.toVced(v); };
  const V = { 0: voice(1), 1: voice(2), 2: voice(3) };
  [0, 1, 2].forEach(ch => { document.querySelectorAll('#plChans .cname')[ch].click(); Bus.dispatchEvent(new CustomEvent('addvoice', { detail: { name: 'CH' + ch, vced: V[ch] } })); });
  document.querySelectorAll('#plChans .on')[3].click();   // ch10 off: only melodic channels
  const tempo = document.getElementById('plTempo'); tempo.value = 200; tempo.dispatchEvent(new Event('input'));
  document.getElementById('plPlay').click(); await new Promise(r => setTimeout(r, 4000)); document.getElementById('plPlay').click();
  await new Promise(r => setTimeout(r, 300));
  const song = SMF.parse(new Uint8Array(b));
  const chOf = {}; song.notes.filter(n => n.ch < 3).forEach(n => (chOf[n.note] = chOf[n.note] || new Set()).add(n.ch));
  const dev = []; let ons = 0, wrong = 0; const bad = [];
  for (const m of L) {
    if (m[0] === 0xF0) dev[(m[3] << 7) | m[4]] = m[5];
    else if ((m[0] & 0xF0) === 0x90) { ons++; const fc = dev[DX7.paramIndex(1, 'FC')]; const ok = [...(chOf[m[1]] || [])].some(ch => V[ch][DX7.paramIndex(1, 'FC')] === fc);
      if (!ok) { wrong++; if (bad.length < 5) bad.push(m[1] + ':FC' + fc); } }
  }
  return { ons, wrong, bad };
}"""

with sync_playwright() as pw:
    br = pw.chromium.launch(channel='chrome', headless=True)
    pg = br.new_page(); pg.goto('http://localhost:8731/'); time.sleep(1)
    r = pg.evaluate(JS)
    print(f"web player: {r['ons']} note-ons, {r['wrong']} with the wrong channel voice", r['bad'] or '')
    br.close()
