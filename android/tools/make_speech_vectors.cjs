// Reference output of the web app's speech engine (app/speech.js) for the Kotlin port's parity test.
//   node android/tools/make_speech_vectors.cjs  ->  android/app/src/test/resources/speech_vectors.json
const fs = require('fs'), path = require('path');
const ctx = require('../../speech/webload.cjs')(['dx7.js', 'fm1-synth.js', 'speech.js']);
const { Speech } = ctx;
const dir = path.join(__dirname, '../../app/speech');
Speech.setData(JSON.parse(fs.readFileSync(path.join(dir, 'units.json'), 'utf8')), fs.readFileSync(path.join(dir, 'cmudict.txt'), 'utf8'),
  JSON.parse(fs.readFileSync(path.join(dir, 'pos.json'), 'utf8')));

const cases = [
  { text: 'Hello world. I am a synthesizer, made of sine waves. Can you hear me?', opts: {} },
  { text: 'The FM-1 costs $42.50 on the 3rd of May, 1984! Is it a synthesizer? Blarg.', opts: {} },
  { text: 'I will record a record. They refuse to take the refuse. Where is the station?', opts: { accent: 5 } },
  { text: 'Dr. Smith & co. said 7 vs. 12%, i.e. not bad', opts: { tone: 'flat', speed: 0.8 } },
  { text: 'One, two, three, four. Watch out for the car!', opts: { pos: false, base: 50, bright: 60 } },
  { text: 'Hello world. Can you hear me?', opts: { design: 'harmonic' } },
  { text: 'The quick brown fox.', opts: { design: 'lebrun', vib: 12 } },
  { text: 'See you tomorrow.', opts: { design: 'sine' } },
  { text: 'She sells sea shells; he has zero vision.', opts: { select: false, fric: 'fb' } },
  { text: 'Fish and chips, please.', opts: { fric: 'noise', fricDepth: 80, fricSpread: 0.3 } },
  { text: 'Is this the zoo? Tip the pot.', opts: { burst: false, fricDepth: 78 } },
  { text: 'Hello world. I am a synthesizer, made of sine waves.', opts: { sync: false } },
  { text: 'Can you hear me now? The weather is nice.', opts: { sync: true, fine: true, glide: 99, attack: 80, overlap: 20, releaseRate: 75 } },
  { text: 'My voice is made of many little notes.', opts: { fine: true } },
  { text: 'Can you hear me now? Please say that again, slowly.', opts: { diph: 'sweep', softStart: 99, softRise: 80, overlap: 20, releaseRate: 75 } },
  { text: 'My voice is made of many little notes.', opts: { softStart: 6, softRise: 70, softVoiced: false, overlap: 12, releaseRate: 80 } },
  { text: 'I like to ride my bike by the white house. How now, brown cow?', opts: { diph: 'sweep', diphEdge: 0.15 } },
  { text: 'The boy enjoys his noisy toy. They say the rain stays away.', opts: { diph: 'sweep', diphAll: true, diphOnset: 0.2, diphGlide: 0.65 } },
  ...['feminine', 'whisper', 'stutter', 'cyborg', 'alien', 'singer', 'radio', 'uptalk', 'singsong', 'drawl', 'excited'].map(ch =>
    ({ text: 'Hello there. I can speak in many voices, can you hear me?', opts: {}, character: ch })),
];
// deterministic "recording": voiced vowel-ish tone with a formant-shaped harmonic series, a noise burst, silence
function signal(sr) {
  const n = Math.floor(sr * 1.2), x = new Float32Array(n);
  let seed = 12345;
  const rnd = () => { seed = (seed * 16807) % 2147483647; return seed / 2147483647 * 2 - 1; };   // exact in JS and Kotlin
  for (let i = 0; i < n; i++) {
    const t = i / sr;
    if (t < 0.5) { let s = 0; for (let h = 1; h <= 30; h++) { const f = 120 * h * (1 + 0.1 * t), a = Math.exp(-((f - 700) ** 2) / 2e5) + 0.5 * Math.exp(-((f - 1200) ** 2) / 3e5) + 0.2 * Math.exp(-((f - 2500) ** 2) / 5e5); s += a * Math.sin(2 * Math.PI * f * t); } x[i] = 0.3 * s; }
    else if (t < 0.7) x[i] = 0.1 * rnd();
    else if (t < 1.0) { let s = 0; for (let h = 1; h <= 20; h++) { const f = 150 * h, a = Math.exp(-((f - 350) ** 2) / 1e5) + Math.exp(-((f - 2200) ** 2) / 4e5); s += a * Math.sin(2 * Math.PI * f * t); } x[i] = 0.2 * s; }
  }
  return x;
}
const ev = e => [+e.t.toFixed(6), ...e.b];
const out = { cases: [] };
for (const c of cases) {
  const o = c.character ? Speech.character(c.character, c.opts) : c.opts;
  const r = Speech.speak(c.text, o);
  const notes = Speech.plan(r.frames, { ...o, emax: r.emax });
  out.cases.push({ text: c.text, opts: c.opts, notes: r.notes.map(n => n.k + '|' + n.m),
    words: r.sentences.flatMap(s => s.words.map(w => `${w.text}/${w.pos}/${w.weight}/${w.ph.join(' ')}`)),
    frames: r.frames.length, semis: r.frames.map(f => +f.semi.toFixed(6)), events: Speech.toEvents(notes, o).map(ev), character: c.character || null });
}
const sig = signal(16000);
const fr = Speech.analyse(sig, 16000);
const rec = Speech.framesFromRecording(fr, { tone: 'follow' });
const rn = Speech.plan(rec.frames, { emax: rec.emax });
out.recording = { sr: 16000, frames: fr.map(f => [+f.e.toFixed(6), f.v, +f.f0.toFixed(6), ...f.F.map(x => +x.toFixed(4)), ...f.L.map(x => +x.toFixed(4)), +f.cent.toFixed(4)]),
  events: Speech.toEvents(rn).map(ev) };
const dst = path.join(__dirname, '../app/src/test/resources/speech_vectors.json');
fs.writeFileSync(dst, JSON.stringify(out));
console.log('wrote', dst, out.cases.length, 'texts,', out.recording.frames.length, 'analysis frames');
