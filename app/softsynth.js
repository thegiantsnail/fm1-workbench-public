// Software FM-1 as a MIDI output: selecting it as "Out" makes Engine.out = SoftFM1, and everything the app would send
// the hardware (notes, param diffs, voice/bank dumps) plays through fm1-synth.js in an AudioWorklet instead.
// Timestamps (performance.now ms) map onto the audio clock, so the Engine runs in 'timestamp' mode for sample-accurate
// timing. The offset is set once per start/resume and only re-synced if the clocks drift apart, never per message:
// a re-sync between a voice diff and its note could reorder them.
'use strict';
const SoftFM1 = (() => {
  const S = { id: 'soft-fm1', name: 'Software FM-1 (no hardware)', isSoft: true, ctx: null, node: null, pending: [], starting: null,
    volume: 0.8, offset: 0, margin: 0.03, checkedAt: 0 };
  const sync = () => { S.offset = S.ctx.currentTime - performance.now() / 1000; S.checkedAt = performance.now(); };
  S.start = () => {
    if (S.starting) return S.starting;
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return Promise.reject(new Error('Web Audio is not available'));
    S.ctx = new AC({ latencyHint: 'interactive' });
    S.starting = (async () => {
      await S.ctx.audioWorklet.addModule('fm1-synth.js');
      S.node = new AudioWorkletNode(S.ctx, 'fm1', { numberOfInputs: 0, outputChannelCount: [2] });
      S.node.connect(S.ctx.destination);
      S.node.port.postMessage({ volume: S.volume, fxCh: S.fxCh, profile: S.profile });
      sync();
      S.pending.splice(0).forEach(([b, t]) => S.send(b, t));
    })();
    return S.starting;
  };
  S.resume = () => {
    if (S.ctx && S.ctx.state !== 'running') S.ctx.resume().then(() => { if (S.node) sync(); });
  };
  S.send = (bytes, t) => {
    if (!S.node) { S.pending.push([Array.from(bytes), t]); S.start().catch(e => console.warn('Software FM-1:', e)); return; }
    const now = performance.now();
    if (now - S.checkedAt > 5000) {                       // clocks drift slowly: re-sync only when clearly off
      S.checkedAt = now;
      if (Math.abs(S.ctx.currentTime - now / 1000 - S.offset) > 0.02) sync();
    }
    const ct = Math.max(S.offset + (t && t > now ? t : now) / 1000 + S.margin, S.ctx.currentTime + 0.005);
    S.node.port.postMessage({ msgs: [{ b: Array.from(bytes), t: ct }] });
  };
  S.reset = () => { if (S.node) S.node.port.postMessage({ reset: true }); };
  S.setVolume = v => { S.volume = v; if (S.node) S.node.port.postMessage({ volume: v }); };
  S.fxCh = 1; S.profile = 'stock';
  S.setProfile = pr => { S.profile = pr; if (S.node) S.node.port.postMessage({ profile: pr }); };
  S.setFxChannel = ch => { S.fxCh = ch; if (S.node) S.node.port.postMessage({ fxCh: ch }); };
  // Browsers only start audio after a user gesture.
  ['pointerdown', 'keydown'].forEach(ev => document.addEventListener(ev, S.resume, { capture: true }));
  return S;
})();
