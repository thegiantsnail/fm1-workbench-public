// Node self-test for dx7.js: VMEM/VCED round trips, checksums, parser, and parity with Python dx7.py output.
const fs = require('fs'), vm = require('vm'), assert = require('assert');
const ctx = {}; vm.createContext(ctx);
vm.runInContext(fs.readFileSync(__dirname + '/dx7.js', 'utf8') + ';this.DX7=DX7;', ctx);
const { DX7 } = ctx;

const rand = (max) => Math.floor(Math.random() * (max + 1));
function randomVoice() {
  const v = DX7.initVoice();
  v.ops.forEach(o => DX7.OP_FIELDS.forEach(f => o[f] = rand(DX7.OP_MAX[f])));
  DX7.G_FIELDS.forEach(f => v.g[f] = rand(DX7.G_MAX[f]));
  v.name = 'TEST ' + rand(99999);
  return DX7.clampVoice(v);
}
for (let i = 0; i < 2000; i++) {
  const v = randomVoice();
  assert.strictEqual(JSON.stringify(DX7.toVced(DX7.fromVmem(DX7.toVmem(v)))), JSON.stringify(DX7.toVced(v)), 'VMEM round trip');
  assert.strictEqual(JSON.stringify(DX7.toVced(DX7.fromVced(DX7.toVced(v)))), JSON.stringify(DX7.toVced(v)), 'VCED round trip');
  assert.ok(DX7.toVmem(v).every(b => b >= 0 && b < 128), 'VMEM bytes 7-bit');
}
const bank = Array.from({ length: 32 }, randomVoice);
const syx = DX7.vmemSysex(bank);
assert.strictEqual(syx.length, 4104);
const data = syx.slice(6, 6 + 4096);
assert.strictEqual((data.reduce((a, b) => a + b, 0) + syx[4102]) & 127, 0, 'bank checksum');
const parsed = DX7.parseSyx(Uint8Array.from([...syx, ...DX7.vcedSysex(bank[3])]));
assert.strictEqual(parsed.length, 33);
parsed.slice(0, 32).forEach((p, i) => assert.strictEqual(JSON.stringify(DX7.toVced(p.voice)), JSON.stringify(DX7.toVced(bank[i]))));
assert.strictEqual(DX7.vcedSysex(bank[0]).length, 163);
assert.strictEqual(JSON.stringify(DX7.paramSysex(123, 2)), JSON.stringify([0xF0, 0x43, 0x10, 0x00, 123, 2, 0xF7]));
assert.strictEqual(JSON.stringify(DX7.paramSysex(134, 5)), JSON.stringify([0xF0, 0x43, 0x10, 0x01, 6, 5, 0xF7]));
assert.strictEqual(DX7.paramIndex(1, 'FC'), 123);
assert.strictEqual(DX7.opMaskValue([true, false, false, false, false, false]), 32);
// Parity with the Python builder that was verified on hardware (test_patch.py sine voice).
const py = DX7.initVoice('CLAUDESINE'); py.g.ALG = 31; py.ops.forEach(o => o.OL = 0); py.ops[0].OL = 99; py.ops[0].KVS = 7;
const pyHex = process.argv[2];
if (pyHex) assert.strictEqual(Buffer.from(DX7.vcedSysex(py).slice(1)).toString('hex'), pyHex, 'matches python');
console.log('dx7.js: all tests passed' + (pyHex ? ' (incl. python parity)' : ''));
