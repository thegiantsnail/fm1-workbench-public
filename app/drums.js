// Drum macros: a handful of musical controls mapped onto DX7 parameters, applied as offsets to a voice's
// original ("base") parameters. The base is never modified, so Reset always returns to the original sound.
'use strict';
const DrumMacros = (() => {
  // keep: slider value meaning "leave the voice's own setting" (absolute macros only)
  const DEFS = [
    { id: 'level', label: 'Level', min: -40, max: 20, def: 0, fmt: v => (v > 0 ? '+' : '') + (v * 0.75).toFixed(1) + ' dB',
      title: 'Output level: carrier operator levels (≈0.75 dB per step). Can only boost as far as the carriers have headroom.' },
    { id: 'decay', label: 'Decay', min: -40, max: 40, def: 0, fmt: v => v ? (v > 0 ? 'longer ' : 'shorter ') + Math.abs(v) : '0',
      title: 'How long the hit rings: decay rates R2/R3 of the carrier (sounding) operators.' },
    { id: 'release', label: 'Release', min: -40, max: 40, def: 0, fmt: v => v ? (v > 0 ? 'longer ' : 'shorter ') + Math.abs(v) : '0',
      title: 'Ring after the note ends: carrier release rate R4 — matters for long gates and cymbals.' },
    { id: 'tone', label: 'Tone', min: -40, max: 40, def: 0, fmt: v => v ? (v > 0 ? 'brighter ' : 'darker ') + Math.abs(v) : '0',
      title: 'Modulator levels: more or less FM brightness / clang.' },
    { id: 'punch', label: 'Punch', min: -40, max: 40, def: 0, fmt: v => v ? (v > 0 ? 'drop ' : 'rise ') + Math.abs(v) : '0',
      title: 'Pitch-envelope start level: the note starts higher and drops (+), or starts lower and rises (−).' },
    { id: 'sweep', label: 'Sweep time', min: -1, max: 99, def: null, keep: -1, fmt: v => v < 0 ? 'own' : v,
      title: 'How long the pitch sweep takes (pitch-envelope rate 1). "own" keeps the voice\'s setting.' },
    { id: 'grit', label: 'Grit', min: -7, max: 7, def: 0, fmt: v => (v > 0 ? '+' : '') + v,
      title: 'Operator feedback: adds noise and edge (+) or cleans up (−).' },
    { id: 'dyn', label: 'Dynamics', min: -1, max: 7, def: null, keep: -1, fmt: v => v < 0 ? 'own' : v,
      title: 'How much velocity changes the level (carrier velocity sensitivity). "own" keeps the voice\'s setting.' },
  ];
  const clamp = (v, a, b) => Math.max(a, Math.min(b, Math.round(v)));
  const oi = (op, f) => (6 - op) * 21 + DX7.OP_FIELDS.indexOf(f);
  const PR1 = 126, PL4 = 133, ALG = 134, FB = 135;

  function apply(base, m) {
    if (!m || !Object.values(m).some(x => x != null && x !== 0)) return base;
    const v = Array.from(base).slice(0, 155);
    const car = DX7.ALGS[v[ALG]].carriers;
    for (let op = 1; op <= 6; op++) {
      const isCar = car.includes(op), ol = oi(op, 'OL');
      if (v[ol] > 0) {                       // leave silent operators silent
        if (m.level && isCar) v[ol] = clamp(v[ol] + m.level, 1, 99);
        if (m.tone && !isCar) v[ol] = clamp(v[ol] + m.tone, 0, 99);
      }
      // Length is set by the carriers' envelopes; modulator envelopes shape tone, so decay/release leave them alone.
      if (m.decay && isCar) { for (const f of ['R2', 'R3']) v[oi(op, f)] = clamp(v[oi(op, f)] - m.decay, 0, 99); }
      if (m.release && isCar) v[oi(op, 'R4')] = clamp(v[oi(op, 'R4')] - m.release, 0, 99);
      if (m.dyn != null && isCar) v[oi(op, 'KVS')] = clamp(m.dyn, 0, 7);
    }
    if (m.punch) v[PL4] = clamp(v[PL4] + m.punch, 0, 99);
    if (m.sweep != null) v[PR1] = clamp(99 - m.sweep, 0, 99);
    if (m.grit) v[FB] = clamp(v[FB] + m.grit, 0, 7);
    return v;
  }
  // Cached derived parameters for a track voice {name, vced, macros}.
  const cache = new WeakMap();           // not on the object, so it never ends up in saved patterns/kits
  function vcedOf(voice) {
    if (!voice) return null;
    const key = JSON.stringify(voice.macros || {});
    let c = cache.get(voice);
    if (!c || c.k !== key || c.b !== voice.vced) cache.set(voice, c = { k: key, b: voice.vced, v: apply(voice.vced, voice.macros) });
    return c.v;
  }
  const isEdited = voice => !!voice && !!voice.macros && Object.values(voice.macros).some(x => x != null && x !== 0);
  return { DEFS, apply, vcedOf, isEdited };
})();
