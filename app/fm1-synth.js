// Software FM-1: a DX7-compatible 6-operator FM engine that understands the MIDI the Workbench sends to the hardware
// (notes, DX7 parameter changes, single-voice and 32-voice dumps, program change, sustain), so every tab can be played
// without the unit. Runs as an AudioWorklet in the browser and as a plain class in Node (offline renders for tests).
//
// Like the FM-1, a parameter change only affects the NEXT note: each note takes a snapshot of the edit buffer, so held
// notes keep their sound while the voice changes (the Engine's voice-switching relies on this).
// Envelope, level, velocity and scaling maths follow the DX7 behaviour documented by the Music Synthesizer for Android /
// Dexed emulations; they were checked against measurements of the FM-1 (FINDINGS.md): 0.75 dB per output-level step,
// release rate 72 = -40 dB in ~50 ms, modulation index 4π at full modulator level. The effect section (FM1Fx) follows
// the unit's CC map on the effect channel. Not emulated: the unit's presets (program change only recalls a bank sent
// with "Send bank"), pitch bend and the mod wheel.
'use strict';
const FM1T = (() => {
  const SIN_N = 4096, SIN = new Float32Array(SIN_N + 1);
  for (let i = 0; i <= SIN_N; i++) SIN[i] = Math.sin(2 * Math.PI * i / SIN_N);
  const EXP = new Float32Array(4097);                     // level units (1/256 doubling, 3840 = full) -> amplitude
  for (let i = 0; i <= 4096; i++) EXP[i] = Math.pow(2, (i - 3840) / 256);
  const LOW = [0, 5, 9, 13, 17, 20, 23, 25, 27, 29, 31, 33, 35, 37, 39, 41, 42, 43, 45, 46];
  const scaleOut = x => x >= 20 ? 28 + x : LOW[x];
  const VEL = [0, 70, 86, 97, 106, 114, 121, 126, 132, 138, 142, 148, 152, 156, 160, 163, 166, 170, 173, 174, 178, 181,
    184, 186, 189, 190, 194, 196, 198, 200, 202, 205, 206, 209, 211, 214, 216, 218, 220, 222, 224, 225, 227, 229, 230,
    232, 233, 235, 237, 238, 240, 241, 242, 243, 244, 246, 246, 248, 249, 250, 251, 252, 253, 254];
  const EXPSCALE = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 11, 14, 16, 19, 23, 27, 33, 39, 47, 56, 66, 80, 94, 110, 126, 142, 158,
    174, 190, 206, 222, 238, 250];
  const PLV = [-128, -116, -104, -95, -85, -76, -68, -61, -56, -52, -49, -46, -43, -41, -39, -37, -35, -33, -32, -31,
    -30, -29, -28, -27, -26, -25, -24, -23, -22, -21, -20, -19, -18, -17, -16, -15, -14, -13, -12, -11, -10, -9, -8, -7,
    -6, -5, -4, -3, -2, -1, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24,
    25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 38, 40, 43, 46, 49, 53, 58, 65, 73, 82, 92, 103, 115, 127];
  const PRATE = [1, 2, 3, 3, 4, 4, 5, 5, 6, 6, 7, 7, 8, 8, 9, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23,
    24, 25, 26, 27, 28, 30, 31, 33, 34, 36, 37, 38, 39, 41, 42, 44, 46, 47, 49, 51, 53, 54, 56, 58, 60, 62, 64, 66, 68,
    70, 72, 74, 76, 79, 82, 85, 88, 91, 94, 98, 102, 106, 110, 115, 120, 125, 130, 135, 141, 147, 153, 159, 165, 171,
    178, 185, 193, 202, 211, 232, 243, 254, 255, 255, 255, 255, 255, 255, 255, 255];
  // LFO depths measured on the FM-1 (test_soft_probe.py lfo): vibrato follows the DX7 sensitivity table at ~0.88
  // (±41/137/383/1069 cents at PMS 1/3/5/7, LPMD 99); tremolo is 2.1 / 5.3 dB peak-to-peak at AMS 1 / 2, and AMS 3
  // silences the operator at the trough even at LAMD 50.
  const PMS = [0, 10, 20, 33, 55, 92, 153, 255].map(x => x / 255 * 12 * 0.88);   // semitones at LPMD 99
  const AMS = [0, 2.1, 5.3, 130].map(db => db * 256 / 6.02);                    // level units p-p at LAMD 99
  // The FM-1's LFO ignores the patch's speed, waveform and delay (measured: always ~5.8 Hz, near-sine, no delay),
  // and it is free-running: the phase at note-on varies (hence run-to-run spread on deep tremolo patches).
  const LFO_HZ = 5.8;
  const ALG = ['2>1,6>5,5>4,4>3|6', '2>1,6>5,5>4,4>3|2', '3>2,2>1,6>5,5>4|6', '3>2,2>1,6>5,5>4|6', '2>1,4>3,6>5|6',
    '2>1,4>3,6>5|6', '2>1,4>3,5>3,6>5|6', '2>1,4>3,5>3,6>5|4', '2>1,4>3,5>3,6>5|2', '3>2,2>1,5>4,6>4|3',
    '3>2,2>1,5>4,6>4|6', '2>1,4>3,5>3,6>3|2', '2>1,4>3,5>3,6>3|6', '2>1,4>3,5>4,6>4|6', '2>1,4>3,5>4,6>4|2',
    '2>1,3>1,4>3,5>1,6>5|6', '2>1,3>1,4>3,5>1,6>5|2', '2>1,3>1,4>1,5>4,6>5|3', '3>2,2>1,6>4,6>5|6', '3>1,3>2,5>4,6>4|3',
    '3>1,3>2,6>4,6>5|3', '2>1,6>3,6>4,6>5|6', '3>2,6>4,6>5|6', '6>3,6>4,6>5|6', '6>4,6>5|6', '3>2,5>4,6>4|6',
    '3>2,5>4,6>4|3', '2>1,5>4,4>3|5', '4>3,6>5|6', '5>4,4>3|5', '6>5|6', '|6'].map(s => {
    const [e, fb] = s.split('|');
    const edges = e ? e.split(',').map(p => p.split('>').map(Number)) : [];
    const mods = [[], [], [], [], [], [], []];
    edges.forEach(([f, t]) => mods[t].push(f));
    return { mods, fb: +fb, carriers: [1, 2, 3, 4, 5, 6].filter(o => !edges.some(([f]) => f === o)) };
  });
  const INIT = (() => {
    const d = new Uint8Array(156);
    for (let n = 6; n >= 1; n--) {
      const o = (6 - n) * 21;
      d.set([99, 99, 99, 99, 99, 99, 99, 0, 39, 0, 0, 0, 0, 0, 0, 0, n === 1 ? 99 : 0, 0, 1, 0, 7], o);
    }
    d.set([99, 99, 99, 99, 50, 50, 50, 50, 0, 0, 1, 35, 0, 0, 0, 1, 0, 3, 24], 126);
    [...'INIT VOICE'].forEach((c, i) => d[145 + i] = c.charCodeAt(0));
    d[155] = 63;
    return d;
  })();
  function vmemToVced(b) {
    const d = new Uint8Array(156);
    for (let n = 6; n >= 1; n--) {
      const s = (6 - n) * 17, o = (6 - n) * 21;
      for (let i = 0; i < 11; i++) d[o + i] = b[s + i] & 127;
      d[o + 11] = b[s + 11] & 3; d[o + 12] = (b[s + 11] >> 2) & 3;
      d[o + 13] = b[s + 12] & 7; d[o + 20] = (b[s + 12] >> 3) & 15;
      d[o + 14] = b[s + 13] & 3; d[o + 15] = (b[s + 13] >> 2) & 7;
      d[o + 16] = b[s + 14] & 127; d[o + 17] = b[s + 15] & 1; d[o + 18] = (b[s + 15] >> 1) & 31; d[o + 19] = b[s + 16] & 127;
    }
    for (let i = 0; i < 8; i++) d[126 + i] = b[102 + i] & 127;
    d[134] = b[110] & 31; d[135] = b[111] & 7; d[136] = (b[111] >> 3) & 1;
    d[137] = b[112] & 127; d[138] = b[113] & 127; d[139] = b[114] & 127; d[140] = b[115] & 127;
    d[141] = b[116] & 1; d[142] = (b[116] >> 1) & 7; d[143] = (b[116] >> 4) & 7; d[144] = b[117] & 127;
    for (let i = 0; i < 10; i++) d[145 + i] = b[118 + i] & 127;
    d[155] = 63;
    return d;
  }
  // Fitted to the unit (test_soft_probe.py): feedback strength, and the gentle treble roll-off of its output.
  const FB_SCALE = 0.25, OUT_LP_HZ = 5600;     // FB 1-7 harmonics within 1.3 dB; response within 0.5 dB to 13.6 kHz
  return { SIN, SIN_N, EXP, scaleOut, VEL, EXPSCALE, PLV, PRATE, PMS, AMS, LFO_HZ, ALG, INIT, vmemToVced, FB_SCALE, OUT_LP_HZ };
})();

// The FM-1's effect section, driven by CC 0-23 on the effect channel (same map as the hardware, FINDINGS.md):
// 0-3 filter on/type(LP,BP,HP)/cutoff 0-107/Q 0-10 · 4-7 reverb on/type(room,hall,plate)/decay/mix ·
// 8-11 delay on/decay/rate/mix · 12-15 distortion on/gain/tone/level · 16-19 chorus on/freq/depth/mix ·
// 20-23 phaser on/freq/depth/mix. Chain: distortion -> filter -> chorus -> phaser -> delay -> reverb.
// The unit's algorithms are unpublished: these are standard designs with ranges picked to feel alike, except the
// distortion level, which follows the measured trim (level 20 = about -14 dB, i.e. ~0.47 dB per step from 50).
class FM1Fx {
  constructor(sr) {
    this.sr = sr;
    this.cc = new Uint8Array(24);
    [[2, 80], [3, 2], [5, 1], [6, 50], [7, 30], [9, 40], [10, 40], [11, 30], [13, 30], [14, 50], [15, 50],
      [17, 30], [18, 50], [19, 40], [21, 30], [22, 50], [23, 40]].forEach(([k, v]) => this.cc[k] = v);   // panel defaults, all off
    const buf = s => new Float32Array(Math.ceil(s * sr) + 2);
    this.svf = [0, 0]; this.toneZ = 0;
    this.dl = buf(1.05); this.dlw = 0;
    this.chb = buf(0.04); this.chw = 0; this.chPh = 0;
    this.ap = new Float32Array(4); this.apA = 0; this.phPh = 0; this.phFb = 0; this.phN = 0;
    this.combs = [1116, 1188, 1277, 1356].map(n => ({ b: new Float32Array(Math.round(n * sr / 44100 * 1.25)), i: 0, z: 0 }));
    this.aps = [556, 441].map(n => ({ b: new Float32Array(Math.round(n * sr / 44100)), i: 0 }));
    this.update();
  }
  set(n, v) { if (n < 24) { this.cc[n] = v; this.update(); } }
  update() {
    const c = this.cc, sr = this.sr;
    this.distOn = c[12] > 0; this.drive = Math.pow(10, c[13] * 0.36 / 20);
    this.toneA = 1 - Math.exp(-2 * Math.PI * 800 * Math.pow(15, c[14] / 100) / sr); this.level = Math.pow(10, (c[15] - 50) * 0.47 / 20);
    this.filtOn = c[0] > 0; this.ftype = Math.min(2, c[1]);
    this.g = Math.tan(Math.PI * Math.min(sr * 0.45, 20 * Math.pow(2, Math.min(107, c[2]) / 107 * 10)) / sr);
    this.k = 1 / (0.5 + Math.min(10, c[3]) * 1.2);
    this.revOn = c[4] > 0; const size = [0.55, 1, 0.8][Math.min(2, c[5])];
    this.revLen = this.combs.map(cb => Math.max(1, Math.floor(cb.b.length * size)));
    this.revFb = 0.7 + Math.min(100, c[6]) / 100 * 0.27; this.revDamp = [0.35, 0.25, 0.1][Math.min(2, c[5])]; this.revMix = Math.min(100, c[7]) / 100;
    this.dlOn = c[8] > 0; this.dlFb = Math.min(100, c[9]) / 100 * 0.9;
    this.dlLen = Math.min(this.dl.length - 2, Math.round((40 + Math.min(100, c[10]) * 9.6) / 1000 * sr)); this.dlMix = Math.min(100, c[11]) / 100;
    this.chOn = c[16] > 0; this.chRate = 0.1 * Math.pow(50, c[17] / 100); this.chDepth = c[18] / 100 * 0.008 * sr; this.chMix = Math.min(100, c[19]) / 100;
    this.phOn = c[20] > 0; this.phRate = 0.05 * Math.pow(80, c[21] / 100); this.phDepth = Math.min(100, c[22]) / 100; this.phMix = Math.min(100, c[23]) / 100;
  }
  process(buf) {
    const sr = this.sr;
    for (let i = 0; i < buf.length; i++) {
      let x = buf[i];
      if (this.distOn) { x = Math.tanh(x * this.drive); this.toneZ += this.toneA * (x - this.toneZ); x = this.toneZ * this.level; }
      if (this.filtOn) {                                            // state-variable filter (trapezoidal)
        const s = this.svf, g = this.g, k = this.k, a1 = 1 / (1 + g * (g + k)), a2 = g * a1, a3 = g * a2;
        const v3 = x - s[1], v1 = a1 * s[0] + a2 * v3, v2 = s[1] + a2 * s[0] + a3 * v3;
        s[0] = 2 * v1 - s[0]; s[1] = 2 * v2 - s[1];
        x = this.ftype === 0 ? v2 : this.ftype === 1 ? v1 * k : x - k * v1 - v2;
      }
      if (this.chOn) {                                              // modulated delay, 12 ms ± depth
        const b = this.chb, L = b.length;
        b[this.chw] = x; this.chPh = (this.chPh + this.chRate / sr) % 1;
        let r = this.chw - (0.012 * sr + this.chDepth * (0.5 + 0.5 * Math.sin(2 * Math.PI * this.chPh)));
        while (r < 0) r += L;
        const r0 = r | 0, f = r - r0, y = b[r0] * (1 - f) + b[(r0 + 1) % L] * f;
        this.chw = (this.chw + 1) % L;
        x = x * (1 - this.chMix * 0.5) + y * this.chMix * 0.5;
      }
      if (this.phOn) {                                              // 4 swept all-pass stages with feedback
        if ((this.phN++ & 15) === 0) {
          this.phPh = (this.phPh + 16 * this.phRate / sr) % 1;
          const f = 300 * Math.pow(2, this.phDepth * 3 * (0.5 + 0.5 * Math.sin(2 * Math.PI * this.phPh))), t = Math.tan(Math.PI * Math.min(f, sr * 0.45) / sr);
          this.apA = (t - 1) / (t + 1);
        }
        let u = x + this.phFb * 0.5;
        for (let k = 0; k < 4; k++) { const y = this.apA * u + this.ap[k]; this.ap[k] = u - this.apA * y; u = y; }
        this.phFb = u;
        x = x * (1 - this.phMix * 0.5) + u * this.phMix * 0.5;
      }
      if (this.dlOn) {
        const b = this.dl, L = b.length;
        let r = this.dlw - this.dlLen; if (r < 0) r += L;
        const y = b[r];
        b[this.dlw] = x + y * this.dlFb; this.dlw = (this.dlw + 1) % L;
        x += y * this.dlMix;
      }
      if (this.revOn) {                                             // 4 damped combs + 2 all-passes (Schroeder/Freeverb)
        let wet = 0;
        const inp = x * 0.03;
        for (let k = 0; k < 4; k++) {
          const c = this.combs[k], y = c.b[c.i];
          c.z = y * (1 - this.revDamp) + c.z * this.revDamp;
          c.b[c.i] = inp + c.z * this.revFb;
          if (++c.i >= this.revLen[k]) c.i = 0;
          wet += y;
        }
        for (const a of this.aps) { const y = a.b[a.i]; a.b[a.i] = wet + y * 0.5; wet = y - wet; if (++a.i >= a.b.length) a.i = 0; }
        x += wet * this.revMix * 3;
      }
      buf[i] = x;
    }
  }
}

class FM1Core {
  constructor(sr) {
    this.sr = sr;
    this.buf = FM1T.INIT.slice();          // edit buffer (VCED 0..154 + operator mask 155)
    this.bank = null;                      // 32 VCEDs from the last bank dump (program change)
    this.voices = []; this.q = []; this.qs = 0;
    this.sustain = false; this.maxVoices = 16; this.volume = 0.8;
    this.rateK = 44100 / sr;               // DX7 rates were calibrated at 44.1 kHz
    this.fx = new FM1Fx(sr); this.fxCh = 1; // effect channel (0-based; the FM-1 default is channel 2)
    // Firmware profile. 'stock' = M-VAVE V15, 'va' = Baud Girl's FM-1+VA (test_firmware_quirks.py, 2026-09-30):
    // detune 2.8 vs 0.9 cents per DT step; LFO fixed 5.8 Hz vs following the patch / CC 76; CC 7 volume only on 'va'.
    this.profile = 'stock'; this.master = 1;
  }

  // ---- MIDI in ----
  queue(bytes, frame) {                    // schedule a message at an absolute sample frame
    const item = { f: frame, b: bytes, s: this.qs++ };
    let lo = 0, hi = this.q.length;
    while (lo < hi) { const m = (lo + hi) >> 1; if (this.q[m].f <= frame) lo = m + 1; else hi = m; }
    this.q.splice(lo, 0, item);
  }
  midi(b) {
    const st = b[0], type = st & 0xF0;
    if (st === 0xF0) return this.sysex(b);
    if (type === 0x90 && b[2] > 0) return this.noteOn(b[1], b[2]);
    if (type === 0x80 || type === 0x90) return this.noteOff(b[1]);
    if (type === 0xB0 && (st & 15) === this.fxCh && b[1] < 24) return this.fx.set(b[1], b[2]);
    if (type === 0xB0 && this.profile === 'va') {                   // FM-1+VA controllers on the MIDI channel
      const v = b[2], to99 = Math.round(v * 99 / 127);
      if (b[1] === 7) { this.master = Math.pow(v / 127, 1.09); return; }  // measured: 64 = -6.5 dB, 32 = -12 dB
      if (b[1] === 76) { this.buf[137] = to99; for (const x of this.voices) x.lfo.f = this.lfoHz(to99); return; }
      if (b[1] === 77) { this.buf[139] = to99; for (const x of this.voices) { x.lfo.pmd = to99 / 99 * FM1T.PMS[this.buf[143]]; x.lfoActive = true; } return; }
      if (b[1] === 78) { this.buf[138] = to99; return; }
    }
    if (type === 0xB0 && b[1] === 64) { this.sustain = b[2] >= 64; if (!this.sustain) this.voices.forEach(v => v.held && !v.down && this.release(v)); return; }
    if (type === 0xC0 && this.bank) this.buf = this.bank[b[1] & 31].slice();
    // Pitch bend and other controllers are ignored.
  }
  sysex(b) {
    if (b[1] !== 0x43) return;
    if ((b[2] & 0xF0) === 0x10 && b.length >= 7) {                                     // parameter change
      const p = ((b[3] & 3) << 7) | b[4];
      if (p <= 155) this.buf[p] = b[5] & 127;
    } else if ((b[2] & 0xF0) === 0 && b[3] === 0 && b.length >= 6 + 155) {             // single voice (VCED)
      this.buf.set(b.slice(6, 6 + 155).map(x => x & 127)); this.buf[155] = 63;
    } else if ((b[2] & 0xF0) === 0 && b[3] === 9 && b.length >= 6 + 4096) {            // 32-voice bank (VMEM)
      this.bank = [];
      for (let k = 0; k < 32; k++) this.bank.push(FM1T.vmemToVced(b.slice(6 + k * 128, 6 + (k + 1) * 128)));
    }
  }

  // FM-1+VA LFO speed (measured with CC 76): LFS 31 = 5.0 Hz, 62 = 10.4, 86 = 33.7, 99 = 50.7 (log-interpolated)
  lfoHz(lfs) {
    const P = [[0, 0.06], [31, 5.0], [62, 10.4], [86, 33.7], [99, 50.7]];
    for (let i = 1; i < P.length; i++) if (lfs <= P[i][0]) {
      const [a, fa] = P[i - 1], [b, fb] = P[i], t = (lfs - a) / (b - a);
      return fa * Math.pow(fb / fa, t);
    }
    return 50.7;
  }

  // ---- voices ----
  noteOn(key, vel) {
    const T = FM1T, p = this.buf, sr = this.sr;
    if (this.voices.length >= this.maxVoices) {                      // steal: a released voice first, else the oldest
      let k = this.voices.findIndex(v => !v.down && !v.held);
      if (k < 0) k = 0;
      this.voices.splice(k, 1);
    }
    const note = Math.max(0, Math.min(127, key + p[144] - 24));
    const alg = T.ALG[p[134] & 31];
    const v = { key, note, down: true, held: false, age: 0, ops: [null], alg, fbOp: alg.fb, fb1: 0, fb2: 0, y: new Float64Array(7),
      fbAmt: p[135] ? FM1T.FB_SCALE * Math.pow(2, p[135] - 8) : 0, ctr: 0, baseHz: 440 * Math.pow(2, (note - 69) / 12) };
    const rateScale = sens => (Math.min(31, Math.max(0, Math.floor(note / 3) - 7)) * sens) >> 3;
    for (let n = 1; n <= 6; n++) {
      const o = (6 - n) * 21;
      const R = [p[o], p[o + 1], p[o + 2], p[o + 3]], L = [p[o + 4], p[o + 5], p[o + 6], p[o + 7]];
      // keyboard level scaling
      const off = note - p[o + 8] - 17;
      const curve = (group, depth, c) => {
        let s = (c === 0 || c === 3) ? (group * depth * 329) >> 12 : (T.EXPSCALE[Math.min(group, 32)] * depth * 329) >> 15;
        return c < 2 ? -s : s;
      };
      const kls = off >= 0 ? curve((off + 1) / 3 | 0, p[o + 10], p[o + 12]) : curve(-(off - 1) / 3 | 0, p[o + 9], p[o + 11]);
      let olUnits = Math.max(0, Math.min(127, T.scaleOut(p[o + 16]) + kls)) << 5;
      // velocity: DX7 table, but the FM-1 never boosts above the stored level (measured: ~0.87 dB lower per KVS step
      // than the DX7 formula at every velocity, test_soft_probe.py velmod), hence 257 instead of 239
      olUnits += ((p[o + 15] * (T.VEL[Math.min(127, vel) >> 1] - 257) + 7) >> 3) << 4;
      const mode = p[o + 17], fc = p[o + 18], ff = p[o + 19], dt = p[o + 20];
      const op = {
        on: (p[155] >> (6 - n)) & 1, R, L, olUnits, rs: rateScale(p[o + 13]), ams: T.AMS[p[o + 14]],
        fixed: mode ? Math.pow(10, fc & 3) * Math.pow(10, ff / 100) : 0,
        ratio: (fc === 0 ? 0.5 : fc) * (1 + ff / 100) * Math.pow(2, (dt - 7) * (this.profile === 'va' ? 0.9 : 2.8) / 1200),
        phase: 0, dph: 0, level: 0, target: 0, inc: 0, rising: false, ix: 0,
      };
      v.ops.push(op);
      this.advance(op, 0);
    }
    v.peg = { level: T.PLV[p[133]], ix: 0, target: 0, inc: 0, rising: false, R: [p[126], p[127], p[128], p[129]], L: [p[130], p[131], p[132], p[133]] };
    v.pegActive = v.peg.L.some(x => x !== 50);
    this.pegAdvance(v.peg, 0);
    const va = this.profile === 'va', lf = va ? this.lfoHz(p[137]) : T.LFO_HZ;
    v.lfo = { f: lf, ph: ((this.now || 0) * lf / sr) % 1,   // free-running: phase at note-on depends on the time
      wave: va ? p[142] : 4, sh: 0, delay: va ? Math.pow(p[138] / 99, 2) * 4 * sr : 0, ramp: va ? Math.max(1, Math.pow(p[138] / 99, 2) * 2 * sr) : 1,
      pmd: p[139] / 99 * T.PMS[p[143]], amd: p[140] / 99, t: 0 };
    v.lfoActive = v.lfo.pmd > 0 || v.lfo.amd > 0;
    this.modUpdate(v, 0);
    this.voices.push(v);
  }
  noteOff(key) {
    for (const v of this.voices) if (v.key === key && v.down) {
      if (this.sustain) { v.held = true; v.down = false; } else this.release(v);
    }
  }
  release(v) {
    v.down = false; v.held = false;
    for (let n = 1; n <= 6; n++) this.advance(v.ops[n], 3);
    this.pegAdvance(v.peg, 3);
  }
  advance(o, ix) {                                  // amplitude envelope stage ix (0-3; 4 = finished)
    o.ix = ix;
    if (ix > 3) return;
    const T = FM1T;
    o.target = Math.max(16, ((T.scaleOut(o.L[ix]) >> 1) << 6) + o.olUnits - 4256);
    o.rising = o.target > o.level;
    const q = Math.min(63, ((o.R[ix] * 41) >> 6) + o.rs);
    o.inc = (4 + (q & 3)) * Math.pow(2, 2 + (q >> 2)) / 65536 * this.rateK;
  }
  pegAdvance(g, ix) {
    g.ix = ix;
    if (ix > 3) return;
    g.target = FM1T.PLV[g.L[ix]];
    g.rising = g.target > g.level;
    g.inc = FM1T.PRATE[g.R[ix]] * 32 / (29.2 * this.sr);          // pitch-EG units (1/32 octave) per sample; 29.2 fitted to the FM-1 (test_peg_sweep.py: 1.37x slower than the DX7 figure 21.3)
  }
  modUpdate(v, n) {                                 // pitch EG + LFO, every 32 samples
    const T = FM1T, g = v.peg;
    if (v.pegActive && (g.ix < 3 || (g.ix < 4 && !v.down))) {
      if (g.rising) { g.level += g.inc * n; if (g.level >= g.target) { g.level = g.target; this.pegAdvance(g, g.ix + 1); } }
      else { g.level -= g.inc * n; if (g.level <= g.target) { g.level = g.target; this.pegAdvance(g, g.ix + 1); } }
    }
    let semis = v.pegActive ? g.level * 12 / 32 : 0, am = 0;
    if (v.lfoActive) {
      const l = v.lfo;
      l.t += n; const prev = l.ph; l.ph = (l.ph + l.f * n / this.sr) % 1;
      if (l.ph < prev) l.sh = Math.random() * 2 - 1;
      const x = l.ph;
      const val = [x < 0.5 ? 4 * x - 1 : 3 - 4 * x, 1 - 2 * x, 2 * x - 1, x < 0.5 ? 1 : -1, Math.sin(2 * Math.PI * x), l.sh][l.wave] ?? 0;
      const gain = Math.max(0, Math.min(1, (l.t - l.delay) / l.ramp));
      semis += val * gain * l.pmd;
      am = (1 - val) / 2 * gain * l.amd;
    }
    const mul = Math.pow(2, semis / 12);
    for (let k = 1; k <= 6; k++) {
      const o = v.ops[k];
      o.dph = (o.fixed || v.baseHz * o.ratio * mul) / this.sr;
      o.amod = am * o.ams;
    }
  }
  renderVoice(v, out, i0, i1) {
    const T = FM1T, SIN = T.SIN, N = T.SIN_N, EXP = T.EXP, ops = v.ops, y = v.y, mods = v.alg.mods, car = v.alg.carriers;
    for (let i = i0; i < i1; i++) {
      if ((v.ctr++ & 31) === 0) this.modUpdate(v, 32);
      for (let k = 6; k >= 1; k--) {
        const o = ops[k];
        if (o.ix < 3 || (o.ix < 4 && !v.down)) {                     // envelope step
          if (o.rising) {
            if (o.level < 1716) o.level = 1716;
            o.level += (17 - Math.floor(o.level / 256)) * o.inc;
            if (o.level >= o.target) { o.level = o.target; this.advance(o, o.ix + 1); }
          } else {
            o.level -= o.inc;
            if (o.level <= o.target) { o.level = o.target; this.advance(o, o.ix + 1); }
          }
        }
        if (!o.on) { y[k] = 0; continue; }
        let m = 0;
        const mk = mods[k];
        for (let j = 0; j < mk.length; j++) m += y[mk[j]];
        m *= 2;                                                      // full-level modulator = 2 cycles (4π)
        if (k === v.fbOp && v.fbAmt) m += (v.fb1 + v.fb2) * v.fbAmt;
        let ph = o.phase + m; ph -= Math.floor(ph);
        const x = ph * N, xi = x | 0;
        const s = SIN[xi] + (SIN[xi + 1] - SIN[xi]) * (x - xi);
        const lv = o.level - (o.amod || 0);
        const val = s * EXP[lv <= 0 ? 0 : lv >= 4096 ? 4096 : lv | 0];
        y[k] = val;
        if (k === v.fbOp) { v.fb2 = v.fb1; v.fb1 = val; }
        o.phase += o.dph; if (o.phase >= 1) o.phase -= 1;
      }
      let sum = 0;
      for (let j = 0; j < car.length; j++) sum += y[car[j]];
      out[i] += sum;
    }
    v.age += i1 - i0;
  }
  finished(v) {
    if (v.down || v.held) return false;
    for (const c of v.alg.carriers) { const o = v.ops[c]; if (o.on && o.ix < 4 && o.level > 900) return false; }
    return true;
  }
  // Render out.length samples starting at absolute frame `start`; messages are applied at their exact sample.
  render(out, start) {
    out.fill(0);
    const n = out.length;
    let i = 0;
    while (i < n) {
      this.now = start + i;
      while (this.q.length && this.q[0].f <= start + i) this.midi(this.q.shift().b);
      const j = this.q.length ? Math.min(n, Math.max(i + 1, this.q[0].f - start)) : n;
      for (const v of this.voices) this.renderVoice(v, out, i, j);
      i = j;
    }
    this.voices = this.voices.filter(v => !this.finished(v));
    const g = 0.2 * this.volume * this.master;
    if (FM1T.OUT_LP_HZ) {                                // the unit's output rolls off the treble (one-pole low-pass)
      const a = 1 - Math.exp(-2 * Math.PI * FM1T.OUT_LP_HZ / this.sr);
      for (let k = 0; k < n; k++) { this.lpZ = (this.lpZ || 0) + a * (out[k] - (this.lpZ || 0)); out[k] = this.lpZ * g; }
    } else for (let k = 0; k < n; k++) out[k] *= g;
    this.fx.process(out);
    for (let k = 0; k < n; k++) out[k] = Math.tanh(out[k]);
  }
}

if (typeof registerProcessor === 'function') {
  registerProcessor('fm1', class extends AudioWorkletProcessor {
    constructor() {
      super();
      this.core = new FM1Core(sampleRate);
      this.port.onmessage = e => {
        const d = e.data;
        if (d.volume != null) this.core.volume = d.volume;
        if (d.fxCh != null) this.core.fxCh = d.fxCh;
        if (d.profile) { this.core.profile = d.profile; if (d.profile !== 'va') this.core.master = 1; }
        if (d.reset) { this.core.voices = []; this.core.q = []; }
        for (const m of d.msgs || []) this.core.queue(m.b, Math.round(m.t * sampleRate));
      };
    }
    process(inputs, outputs) {
      const o = outputs[0];
      this.core.render(o[0], currentFrame);
      for (let c = 1; c < o.length; c++) o[c].set(o[0]);
      return true;
    }
  });
}
if (typeof module !== 'undefined' && module.exports) module.exports = { FM1Core, FM1T };
