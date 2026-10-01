// DX7 voice model: VCED (155 unpacked params) <-> VMEM (128 packed bytes), SysEx framing, algorithms.
'use strict';
const DX7 = (() => {
  const OP_FIELDS = ['R1','R2','R3','R4','L1','L2','L3','L4','BP','LD','RD','LC','RC','RS','AMS','KVS','OL','MODE','FC','FF','DT'];
  const OP_MAX = {R1:99,R2:99,R3:99,R4:99,L1:99,L2:99,L3:99,L4:99,BP:99,LD:99,RD:99,LC:3,RC:3,RS:7,AMS:3,KVS:7,OL:99,MODE:1,FC:31,FF:99,DT:14};
  const G_FIELDS = ['PR1','PR2','PR3','PR4','PL1','PL2','PL3','PL4','ALG','FB','OKS','LFS','LFD','LPMD','LAMD','LFKS','LFW','LPMS','TRNP'];
  const G_MAX = {PR1:99,PR2:99,PR3:99,PR4:99,PL1:99,PL2:99,PL3:99,PL4:99,ALG:31,FB:7,OKS:1,LFS:99,LFD:99,LPMD:99,LAMD:99,LFKS:1,LFW:5,LPMS:7,TRNP:48};

  // Modulation edges "from>to" and feedback op, DX7 algorithms 1..32.
  const ALG_SRC = [
    '2>1,6>5,5>4,4>3|6','2>1,6>5,5>4,4>3|2','3>2,2>1,6>5,5>4|6','3>2,2>1,6>5,5>4|6',
    '2>1,4>3,6>5|6','2>1,4>3,6>5|6','2>1,4>3,5>3,6>5|6','2>1,4>3,5>3,6>5|4',
    '2>1,4>3,5>3,6>5|2','3>2,2>1,5>4,6>4|3','3>2,2>1,5>4,6>4|6','2>1,4>3,5>3,6>3|2',
    '2>1,4>3,5>3,6>3|6','2>1,4>3,5>4,6>4|6','2>1,4>3,5>4,6>4|2','2>1,3>1,4>3,5>1,6>5|6',
    '2>1,3>1,4>3,5>1,6>5|2','2>1,3>1,4>1,5>4,6>5|3','3>2,2>1,6>4,6>5|6','3>1,3>2,5>4,6>4|3',
    '3>1,3>2,6>4,6>5|3','2>1,6>3,6>4,6>5|6','3>2,6>4,6>5|6','6>3,6>4,6>5|6',
    '6>4,6>5|6','3>2,5>4,6>4|6','3>2,5>4,6>4|3','2>1,5>4,4>3|5',
    '4>3,6>5|6','5>4,4>3|5','6>5|6','|6'];
  const ALGS = ALG_SRC.map(s => {
    const [e, fb] = s.split('|');
    const edges = e ? e.split(',').map(p => p.split('>').map(Number)) : [];
    const carriers = [1,2,3,4,5,6].filter(o => !edges.some(([f]) => f === o));
    return { edges, fb: +fb, carriers };
  });

  const initOp = () => ({R1:99,R2:99,R3:99,R4:99,L1:99,L2:99,L3:99,L4:0,BP:39,LD:0,RD:0,LC:0,RC:0,RS:0,AMS:0,KVS:0,OL:0,MODE:0,FC:1,FF:0,DT:7});
  function initVoice(name = 'INIT VOICE') {
    const ops = [1,2,3,4,5,6].map(initOp);
    ops[0].OL = 99;
    return { name, ops, on: [true,true,true,true,true,true],
      g: {PR1:99,PR2:99,PR3:99,PR4:99,PL1:50,PL2:50,PL3:50,PL4:50,ALG:0,FB:0,OKS:1,LFS:35,LFD:0,LPMD:0,LAMD:0,LFKS:1,LFW:0,LPMS:3,TRNP:24} };
  }
  const clone = v => JSON.parse(JSON.stringify(v));
  const clampVoice = v => {
    v.ops.forEach(o => OP_FIELDS.forEach(f => o[f] = Math.max(0, Math.min(OP_MAX[f], Math.round(o[f] || 0)))));
    G_FIELDS.forEach(f => v.g[f] = Math.max(0, Math.min(G_MAX[f], Math.round(v.g[f] || 0))));
    v.name = cleanName(v.name);
    return v;
  };
  const cleanName = s => (s || '').replace(/[^\x20-\x7e]/g, ' ').slice(0, 10).padEnd(10, ' ');

  // VCED parameter number. op: 1..6 (VCED stores OP6 first).
  const paramIndex = (op, f) => op ? (6 - op) * 21 + OP_FIELDS.indexOf(f) : 126 + G_FIELDS.indexOf(f);

  function toVced(v) {
    const out = [];
    for (let n = 6; n >= 1; n--) OP_FIELDS.forEach(f => out.push(v.ops[n-1][f]));
    G_FIELDS.forEach(f => out.push(v.g[f]));
    for (const c of cleanName(v.name)) out.push(c.charCodeAt(0));
    return out;
  }
  function fromVced(d) {
    const v = initVoice();
    for (let n = 6; n >= 1; n--) OP_FIELDS.forEach((f, i) => v.ops[n-1][f] = d[(6-n)*21 + i]);
    G_FIELDS.forEach((f, i) => v.g[f] = d[126 + i]);
    v.name = String.fromCharCode(...d.slice(145, 155));
    return clampVoice(v);
  }

  function toVmem(v) {
    const b = new Array(128).fill(0);
    for (let n = 6; n >= 1; n--) {
      const o = v.ops[n-1], p = (6-n) * 17;
      ['R1','R2','R3','R4','L1','L2','L3','L4','BP','LD','RD'].forEach((f, i) => b[p+i] = o[f]);
      b[p+11] = (o.RC << 2) | o.LC;
      b[p+12] = (o.DT << 3) | o.RS;
      b[p+13] = (o.KVS << 2) | o.AMS;
      b[p+14] = o.OL;
      b[p+15] = (o.FC << 1) | o.MODE;
      b[p+16] = o.FF;
    }
    const g = v.g;
    [g.PR1,g.PR2,g.PR3,g.PR4,g.PL1,g.PL2,g.PL3,g.PL4,g.ALG].forEach((x, i) => b[102+i] = x);
    b[111] = (g.OKS << 3) | g.FB;
    b[112] = g.LFS; b[113] = g.LFD; b[114] = g.LPMD; b[115] = g.LAMD;
    b[116] = (g.LPMS << 4) | (g.LFW << 1) | g.LFKS;
    b[117] = g.TRNP;
    cleanName(v.name).split('').forEach((c, i) => b[118+i] = c.charCodeAt(0));
    return b;
  }
  function fromVmem(b) {
    const v = initVoice();
    for (let n = 6; n >= 1; n--) {
      const o = v.ops[n-1], p = (6-n) * 17;
      ['R1','R2','R3','R4','L1','L2','L3','L4','BP','LD','RD'].forEach((f, i) => o[f] = b[p+i] & 127);
      o.LC = b[p+11] & 3; o.RC = (b[p+11] >> 2) & 3;
      o.RS = b[p+12] & 7; o.DT = (b[p+12] >> 3) & 15;
      o.AMS = b[p+13] & 3; o.KVS = (b[p+13] >> 2) & 7;
      o.OL = b[p+14] & 127;
      o.MODE = b[p+15] & 1; o.FC = (b[p+15] >> 1) & 31;
      o.FF = b[p+16] & 127;
    }
    const g = v.g;
    ['PR1','PR2','PR3','PR4','PL1','PL2','PL3','PL4'].forEach((f, i) => g[f] = b[102+i] & 127);
    g.ALG = b[110] & 31; g.FB = b[111] & 7; g.OKS = (b[111] >> 3) & 1;
    g.LFS = b[112] & 127; g.LFD = b[113] & 127; g.LPMD = b[114] & 127; g.LAMD = b[115] & 127;
    g.LFKS = b[116] & 1; g.LFW = (b[116] >> 1) & 7; g.LPMS = (b[116] >> 4) & 7;
    g.TRNP = b[117] & 127;
    v.name = String.fromCharCode(...b.slice(118, 128).map(x => x & 127));
    return clampVoice(v);
  }

  const checksum = d => (128 - (d.reduce((a, x) => a + x, 0) & 127)) & 127;
  const vcedSysex = (v, ch = 0) => { const d = toVced(v); return [0xF0, 0x43, ch, 0x00, 0x01, 0x1B, ...d, checksum(d), 0xF7]; };
  function vmemSysex(voices, ch = 0) {
    const d = [];
    for (let i = 0; i < 32; i++) d.push(...toVmem(voices[i] || initVoice('INIT VOICE')));
    return [0xF0, 0x43, ch, 0x09, 0x20, 0x00, ...d, checksum(d), 0xF7];
  }
  const paramSysex = (p, val, ch = 0) => [0xF0, 0x43, 0x10 | ch, (p >> 7) & 3, p & 127, val & 127, 0xF7];
  const opMaskValue = on => on.reduce((m, x, i) => m | (x ? 1 << (5 - i) : 0), 0); // param 155: bit5 = OP1

  // Parse any .syx buffer: returns [{voice, kind}] from VMEM banks, VCED singles, or raw 4096-byte data.
  function parseSyx(bytes) {
    const found = [];
    const u = Array.from(bytes);
    let i = 0;
    while (i < u.length) {
      if (u[i] === 0xF0 && u[i+1] === 0x43) {
        const fmt = u[i+3];
        if (fmt === 0x09 && u.length >= i + 6 + 4096) {
          for (let k = 0; k < 32; k++) found.push({ voice: fromVmem(u.slice(i+6+k*128, i+6+(k+1)*128)), kind: 'bank', slot: k });
          i += 6 + 4096; continue;
        }
        if (fmt === 0x00 && u.length >= i + 6 + 155) {
          found.push({ voice: fromVced(u.slice(i+6, i+6+155)), kind: 'single' }); i += 6 + 155; continue;
        }
      }
      i++;
    }
    if (!found.length && u.length === 4096) {
      for (let k = 0; k < 32; k++) found.push({ voice: fromVmem(u.slice(k*128, (k+1)*128)), kind: 'bank', slot: k });
    }
    return found;
  }

  function freqLabel(o) {
    if (o.MODE) { const f = Math.pow(10, o.FC & 3) * Math.pow(10, o.FF / 100); return f.toFixed(f < 10 ? 3 : f < 100 ? 2 : 1) + ' Hz'; }
    const r = (o.FC === 0 ? 0.5 : o.FC) * (1 + o.FF / 100);
    return '×' + r.toFixed(2);
  }

  return { OP_FIELDS, OP_MAX, G_FIELDS, G_MAX, ALGS, initVoice, clone, clampVoice, cleanName, paramIndex,
    toVced, fromVced, toVmem, fromVmem, vcedSysex, vmemSysex, paramSysex, opMaskValue, parseSyx, freqLabel };
})();
