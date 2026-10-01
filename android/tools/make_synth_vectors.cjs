// Reference renders from the web app's Software FM-1 (app/fm1-synth.js) for the Kotlin port's parity test.
//   node android/tools/make_synth_vectors.cjs  ->  android/app/src/test/resources/synth_vectors.json
const fs = require('fs'), path = require('path');
const ctx = require('../../speech/webload.cjs')(['dx7.js', 'fm1-synth.js']);
const { FM1Core, DX7 } = ctx;
const SR = 22050, LEN = SR;                       // 1 s per scenario
const lib = JSON.parse(fs.readFileSync(path.join(__dirname, '../../app/library/index.json'), 'utf8')).voices;
const vced = i => DX7.toVced(DX7.fromVmem([...Buffer.from(lib[i][3], 'base64')]));
const dump = d => [0xF0, 0x43, 0x00, 0x00, 0x01, 0x1B, ...d, (128 - (d.reduce((a, x) => a + x, 0) & 127)) & 127, 0xF7];
const params = d => d.map((x, p) => DX7.paramSysex(p, x));
const ev = (ms, b) => ({ f: Math.round(ms / 1000 * SR), b });

const scenarios = {
  init_a4: [ev(0, [0x90, 69, 100]), ev(500, [0x80, 69, 0])],
  brass_chord: [ev(0, dump(vced(0))), ev(10, [0x90, 60, 100]), ev(10, [0x90, 64, 90]), ev(10, [0x90, 67, 80]), ev(600, [0x80, 60, 0]), ev(600, [0x80, 64, 0]), ev(600, [0x80, 67, 0])],
  epiano_vel: [ev(0, dump(vced(10))), ev(10, [0x90, 57, 40]), ev(250, [0x90, 69, 127]), ev(500, [0x80, 57, 0]), ev(700, [0x80, 69, 0])],
  steel_drum: [ev(0, dump(vced(26))), ev(10, [0x90, 62, 100]), ev(400, [0x80, 62, 0])],
  // param diffs between notes (voice switching like the drum sequencer / speech)
  switching: (() => {
    const out = [], a = vced(21), b = vced(25);                // MARIMBA, TUB BELLS
    out.push(...params(a).map(m => ev(0, m)));
    for (let k = 0; k < 8; k++) {
      const v = k % 2 ? b : a, prev = k % 2 ? a : b;
      if (k) v.forEach((x, p) => { if (x !== prev[p]) out.push(ev(k * 110 + 1, DX7.paramSysex(p, x))); });
      out.push(ev(k * 110 + 2, [0x90, 60 + k, 100]), ev(k * 110 + 90, [0x80, 60 + k, 0]));
    }
    return out;
  })(),
  fx_chain: [ev(0, dump(vced(13))), ...[[0, 1], [1, 0], [2, 70], [3, 4], [4, 1], [6, 70], [7, 40], [8, 1], [10, 20], [11, 40], [12, 1], [13, 40],
    [16, 1], [18, 60], [19, 50], [20, 1], [22, 60], [23, 50]].map(([c, v]) => ev(0, [0xB1, c, v])),
    ev(10, [0x90, 60, 100]), ev(300, [0x80, 60, 0])],
  va_lfo_volume: [ev(0, dump((() => { const d = vced(23); d[137] = 70; d[139] = 60; d[142] = 0; return d; })())),
    ev(1, [0xB0, 7, 64]), ev(10, [0x90, 69, 100]), ev(400, [0xB0, 76, 100]), ev(900, [0x80, 69, 0])],
};
const profile = { va_lfo_volume: 'va' };

const out = {};
for (const [name, events] of Object.entries(scenarios)) {
  const c = new FM1Core(SR); if (profile[name]) c.profile = profile[name];
  events.forEach(e => c.queue(e.b, e.f));
  const buf = new Float32Array(LEN), blk = new Float32Array(128);
  for (let f = 0; f < LEN; f += 128) { c.render(blk, f); buf.set(blk.subarray(0, Math.min(128, LEN - f)), f); }
  const rms = [];
  for (let i = 0; i + 220 <= LEN; i += 220) { let s = 0; for (let k = i; k < i + 220; k++) s += buf[k] * buf[k]; rms.push(+(10 * Math.log10(s / 220 + 1e-20)).toFixed(4)); }
  out[name] = { profile: profile[name] || 'stock', events: events.map(e => [e.f, ...e.b]), rms, head: Array.from(buf.subarray(0, 3000), x => +x.toFixed(7)) };
}
const dst = path.join(__dirname, '../app/src/test/resources/synth_vectors.json');
fs.writeFileSync(dst, JSON.stringify({ sr: SR, len: LEN, scenarios: out }));
console.log('wrote', dst, Object.keys(out).length, 'scenarios');
