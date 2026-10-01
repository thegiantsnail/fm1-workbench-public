// Generate parity test vectors for the Kotlin port from the web app's own code (app/dx7.js, app/drums.js, app/smf.js),
// which is the hardware-verified reference. Output: android/app/src/test/resources/*.txt (one case per line).
const fs = require('fs'), vm = require('vm'), path = require('path');
const ROOT = path.join(__dirname, '..', '..');
const ctx = { console, Uint8Array, DataView, ArrayBuffer, Math, JSON, String, Number, Array, Object, Set, Map, Error };
vm.createContext(ctx);
for (const f of ['dx7.js', 'drums.js', 'smf.js'])
  vm.runInContext(fs.readFileSync(path.join(ROOT, 'app', f), 'utf8').replace(/^const (\w+) =/m, 'this.$1 ='), ctx);
const { DX7, DrumMacros, SMF } = ctx;
const out = path.join(__dirname, '..', 'app', 'src', 'test', 'resources');
fs.mkdirSync(out, { recursive: true });
const hex = a => Buffer.from(Array.from(a)).toString('hex');

// deterministic PRNG so vectors are stable
let seed = 12345; const rnd = n => { seed = (seed * 1103515245 + 12345) & 0x7fffffff; return seed % (n + 1); };

// 1) codec: random voices -> vced | vmem | vced sysex ; plus ROM1A unpacked from the real bank
const codec = [];
for (let i = 0; i < 300; i++) {
  const v = DX7.initVoice();
  v.ops.forEach(o => DX7.OP_FIELDS.forEach(f => o[f] = rnd(DX7.OP_MAX[f])));
  DX7.G_FIELDS.forEach(f => v.g[f] = rnd(DX7.G_MAX[f]));
  v.name = 'RND ' + rnd(99999);
  DX7.clampVoice(v);
  codec.push([hex(DX7.toVced(v)), hex(DX7.toVmem(v)), hex(DX7.vcedSysex(v))].join('|'));
}
fs.writeFileSync(path.join(out, 'codec_vectors.txt'), codec.join('\n') + '\n');
const rom = fs.readFileSync(path.join(ROOT, 'sysexFinal', '0_Original_Yamaha', '0_DX7', 'ROM1A.syx'));
const parsed = DX7.parseSyx(new Uint8Array(rom));
fs.writeFileSync(path.join(out, 'rom1a_vced.txt'), parsed.map(p => hex(DX7.toVced(p.voice))).join('\n') + '\n');
fs.writeFileSync(path.join(out, 'bank_sysex.txt'), hex(DX7.vmemSysex(parsed.map(p => p.voice))) + '\n');

// 2) param sysex + op mask + carriers
const misc = [];
[[123, 2], [134, 5], [155, 63], [0, 99], [144, 24]].forEach(([p, v]) => misc.push(`param ${p} ${v} ${hex(DX7.paramSysex(p, v, 0))}`));
misc.push('carriers ' + DX7.ALGS.map(a => a.carriers.join(',')).join(';'));
misc.push('edges ' + DX7.ALGS.map(a => a.edges.map(e => e.join('>')).join(',') + '|' + a.fb).join(';'));
misc.push('opmask ' + DX7.opMaskValue([true, false, true, false, true, false]));
fs.writeFileSync(path.join(out, 'misc_vectors.txt'), misc.join('\n') + '\n');

// 3) drum macros on ROM voices
const macroCases = [{ level: -20 }, { decay: 30 }, { decay: -30 }, { release: 25 }, { tone: 30 }, { tone: -20 }, { punch: 35, sweep: 45 },
  { grit: 5 }, { dyn: 7 }, { level: 10, decay: -10, tone: 15, punch: -20, sweep: 0, grit: -3, dyn: 0 }];
const mac = [];
parsed.slice(0, 32).forEach((p, i) => {
  const base = DX7.toVced(p.voice), m = macroCases[i % macroCases.length];
  mac.push([JSON.stringify(m), hex(base), hex(DrumMacros.apply(base, m))].join('|'));
});
fs.writeFileSync(path.join(out, 'macro_vectors.txt'), mac.join('\n') + '\n');

// 4) SMF: demo.mid summary + every note
const song = SMF.parse(new Uint8Array(fs.readFileSync(path.join(ROOT, 'app', 'samples', 'demo.mid'))));
const smf = [`summary ${song.notes.length} ${song.ctrl.length} ${song.duration.toFixed(3)} ${song.bpm.toFixed(3)} ${song.ppq}`];
song.notes.forEach(n => smf.push(`note ${n.t.toFixed(3)} ${n.dur.toFixed(3)} ${n.ch} ${n.note} ${n.vel}`));
fs.writeFileSync(path.join(out, 'smf_demo.txt'), smf.join('\n') + '\n');
fs.copyFileSync(path.join(ROOT, 'app', 'samples', 'demo.mid'), path.join(out, 'demo.mid'));
fs.copyFileSync(path.join(ROOT, 'sysexFinal', '0_Original_Yamaha', '0_DX7', 'ROM1A.syx'), path.join(out, 'ROM1A.syx'));
console.log(`vectors: codec ${codec.length}, rom ${parsed.length}, macros ${mac.length}, smf notes ${song.notes.length}`);
