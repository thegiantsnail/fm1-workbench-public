// Run the web app's speech engine (app/speech.js) in Node: text -> FM-1 MIDI events (JSON) and a render by the
// software FM-1 (app/fm1-synth.js). Used by speech/eval_web.py for Whisper scoring and hardware playback.
//   node speech/web_speak.cjs '{"text": "hello world", "wav": "out.wav", "events": "ev.json", "opts": {...}}'
const fs = require('fs'), path = require('path');
const ctx = require('./webload.cjs')(['dx7.js', 'fm1-synth.js', 'speech.js']);
const { Speech, FM1Core } = ctx;
const app = path.join(__dirname, '..', 'app', 'speech');
Speech.setData(JSON.parse(fs.readFileSync(path.join(app, 'units.json'), 'utf8')), fs.readFileSync(path.join(app, 'cmudict.txt'), 'utf8'),
  JSON.parse(fs.readFileSync(path.join(app, 'pos.json'), 'utf8')));

function wavBytes(x, sr, raw = false) {           // raw: keep the level (no peak normalising)
  const b = Buffer.alloc(44 + x.length * 2);
  b.write('RIFF', 0); b.writeUInt32LE(36 + x.length * 2, 4); b.write('WAVEfmt ', 8); b.writeUInt32LE(16, 16); b.writeUInt16LE(1, 20);
  b.writeUInt16LE(1, 22); b.writeUInt32LE(sr, 24); b.writeUInt32LE(sr * 2, 28); b.writeUInt16LE(2, 32); b.writeUInt16LE(16, 34);
  b.write('data', 36); b.writeUInt32LE(x.length * 2, 40);
  let peak = 1e-9; for (const v of x) peak = Math.max(peak, Math.abs(v));
  if (raw) peak = 0.9;
  for (let i = 0; i < x.length; i++) b.writeInt16LE(Math.round(x[i] / peak * 0.9 * 32767), 44 + i * 2);
  return b;
}
function render(events, sr = 22050) {
  const c = new FM1Core(sr);
  for (const e of events) c.queue(e.b, Math.round(e.t / 1000 * sr));
  const n = Math.ceil(((events.length ? events[events.length - 1].t : 0) / 1000 + 0.4) * sr), out = new Float32Array(n), blk = new Float32Array(128);
  for (let f = 0; f < n; f += 128) { c.render(blk, f); out.set(blk.subarray(0, Math.min(128, n - f)), f); }
  return out;
}

const job = JSON.parse(process.argv[2]);
const jobs = Array.isArray(job) ? job : [job];
const summary = [];
for (const j of jobs) {
  const o = j.character ? Speech.character(j.character, j.opts || {}) : (j.opts || {});
  if (j.tune && ctx.FM1T) Object.assign(ctx.FM1T, j.tune);   // experiment knobs (FB_SCALE, OUT_LP_HZ)
  if (j.eventsIn) {                           // raw MIDI events [{t, b}] -> software FM-1 render (synth comparisons)
    const ev = JSON.parse(fs.readFileSync(j.eventsIn, 'utf8'));
    fs.writeFileSync(j.wav, wavBytes(render(ev, j.sr || 22050), j.sr || 22050, j.raw));
    summary.push({ events: ev.length });
    continue;
  }
  let r;
  if (j.frames) r = { frames: JSON.parse(fs.readFileSync(j.frames, 'utf8')), emax: j.emax ?? 0, notes: [] };     // analysed recording (Python)
  else r = Speech.speak(j.text, o);
  const notes = Speech.plan(r.frames, { ...o, emax: r.emax });
  const ev = Speech.toEvents(notes, o);
  if (j.events) fs.writeFileSync(j.events, JSON.stringify(ev));
  if (j.wav) fs.writeFileSync(j.wav, wavBytes(render(ev), 22050));
  // planned pitch per 10 ms frame (semitones around the key; null = unvoiced/silent) and the notes' actual keys
  const contour = r.frames.map(f => (f.v && f.e > (r.emax || 0) - 42) ? +(f.semi || 0).toFixed(2) : null);
  const words = (r.sentences || []).flatMap(s => s.words.map(w => `${w.text}/${w.pos || '?'}${w.weight ? '*' : ''}`));
  summary.push({ notes: notes.length, events: ev.length, params: ev.filter(e => e.b[0] === 0xF0).length, lint: r.notes, ms: ev.length ? ev[ev.length - 1].t : 0,
    contour, keys: notes.map(n => [n.start, n.key, n.v.name.trim() === 'SPEECH V' ? 1 : 0]), words, types: (r.sentences || []).map(s => s.type) });
}
console.log(JSON.stringify(summary));
