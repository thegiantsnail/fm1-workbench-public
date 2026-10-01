// Timing + device engine shared by the editor, drum sequencer and MIDI player.
//
// Hardware facts this is built on (measured, see FINDINGS.md):
//  - Single-param SysEx changes apply to the NEXT note with 0 ms added latency; ringing notes keep their sound.
//  - Full VCED dumps stall the FM-1 for 20-450 ms at random, so real-time voice changes send param diffs only.
//  - The FM-1 ignores CC120/CC123: only explicit note-offs release voices.
//  - The FM-1 matches a note-off using the transpose (TRNP) in effect at note-OFF time: if TRNP changes while a note
//    is held, the note hangs forever. So the device's TRNP stays at 24 (none) and each voice's transpose is applied
//    by shifting the note number instead.
'use strict';
const Engine = (() => {
  const LOOKAHEAD = 120;          // ms scheduled ahead of now
  const PREP_LEAD = 30;           // ms: how early a voice diff may be sent before its note
  const MS_PER_PARAM = 0.17;      // measured transmit+parse cost per 7-byte param message
  const SOUND_PARAMS = [...Array(145).keys()];   // 0..144 (names 145..154 excluded for speed)

  const E = {
    out: null, ch: 0,
    dev: null,                    // Uint8Array(156) = what the FM-1 edit buffer holds, or null if unknown
    lastOnAt: 0,                  // time of the latest scheduled note-on (voice diffs go after it)
    voiceReadyAt: 0,              // when the latest scheduled voice diff is sent; later notes (e.g. rest of a chord) wait for it
    lastOn: new Int32Array(128).fill(-1), onSeq: 0,
    trn: 0,                       // transpose of the loaded voice (semitones), applied to note numbers
    keyOf: new Map(),             // note id -> key actually sent (so note-offs match their note-on)
    offQueue: [], producers: new Set(), onTick: null,
    stats: { switches: 0, params: 0 },
  };

  E.now = () => performance.now();
  // Dispatch modes: 'jit' holds messages in our own queue and sends each at its time from a fast worker tick (2 ms asked;
  // browsers clamp repeating timers to ~4 ms, which matches the measured p95 of ~3.5 ms);
  // 'timestamp' hands future timestamps to Web MIDI (Chrome on Windows releases those on a coarse ~15 ms timer).
  E.timing = 'jit';
  const outQ = []; let outSeq = 0;
  const rawSend = bytes => { if (!E.out) return false; try { E.out.send(bytes); return true; } catch (e) { console.warn('MIDI send failed', e); return false; } };
  E.send = (bytes, t) => {
    if (!E.out) return false;
    const now = E.now();
    if (!t || t <= now + 0.5) return rawSend(bytes);
    if (E.timing === 'timestamp') { try { E.out.send(bytes, t); return true; } catch (e) { return false; } }
    const item = { t, bytes, s: outSeq++ };
    let lo = 0, hi = outQ.length;                       // keep sorted by time, FIFO for equal times
    while (lo < hi) { const m = (lo + hi) >> 1; if (outQ[m].t <= t) lo = m + 1; else hi = m; }
    outQ.splice(lo, 0, item); tick(true);
    return true;
  };
  const flushOut = () => { const lim = E.now() + 0.7; while (outQ.length && outQ[0].t <= lim) rawSend(outQ.shift().bytes); };

  // ---- device voice state (param diffs) ----
  function target(vced, opMask) {
    const t = new Uint8Array(156);
    t.set(vced.slice(0, 155)); t[155] = opMask ?? 63;
    t[144] = 24;                  // device transpose stays neutral (see header)
    return t;
  }
  // Send whatever differs between the device buffer and `vced`. Returns {count, doneAt}.
  E.setVoice = (vced, t, { opMask, withName = false } = {}) => {
    const want = target(vced, opMask);
    E.trn = (vced[144] ?? 24) - 24;
    const idx = withName ? [...SOUND_PARAMS, 145, 146, 147, 148, 149, 150, 151, 152, 153, 154, 155] : [...SOUND_PARAMS, 155];
    let n = 0;
    for (const p of idx) {
      if (E.dev && E.dev[p] === want[p]) continue;
      E.send(DX7.paramSysex(p, want[p], E.ch), t);
      n++;
    }
    if (!E.dev) E.dev = new Uint8Array(156);
    E.dev.set(want);
    // Baud Girl's FM-1+VA firmware: the DX7 LFO speed/delay params never reach the running LFO (measured), but its
    // CC 76 (LFO Speed) and CC 78 (LFO Delay) on the MIDI channel do. Stock firmware ignores the patch LFO speed.
    if (E.firmware === 'va') {
      for (const [p, c] of [[137, 76], [138, 78]]) {
        const v = Math.round(want[p] * 127 / 99);
        if (E.lfoSent[c] !== v) { E.send([0xB0 | E.ch, c, v], t); E.lfoSent[c] = v; n++; }
      }
    }
    const doneAt = (t || E.now()) + n * MS_PER_PARAM;
    // The mirror above changes when a diff is *scheduled*: a note that needs no diff of its own must still wait for it.
    if (n) { E.stats.switches++; E.stats.params += n; E.voiceReadyAt = Math.max(E.voiceReadyAt, doneAt + 0.5); }
    return { count: n, doneAt };
  };
  E.setParam = (p, v, t) => {
    if (p === 144) { E.trn = v - 24; return; }                   // transpose = note shift, never the device's TRNP
    E.send(DX7.paramSysex(p, v, E.ch), t); if (E.dev) E.dev[p] = v;
  };
  E.forgetDevice = () => { E.dev = null; E.lfoSent = {}; };
  E.firmware = 'stock';                           // 'stock' (M-VAVE) or 'va' (Baud Girl's FM-1+VA)
  E.lfoSent = {};

  // Schedule a hit with a specific voice at time t. The diff is sent as early as allowed
  // (right after the previous note-on, at most PREP_LEAD before t) so the note stays on time.
  E.hit = (vced, note, vel, t, gateMs, opts = {}) => {
    let noteAt = t;
    if (vced) {
      const prepAt = Math.max(E.lastOnAt + 0.5, t - PREP_LEAD, E.now());
      E.setVoice(vced, prepAt, opts);
      noteAt = Math.max(t, E.voiceReadyAt);
    }
    return E.note(note, vel, noteAt, gateMs);
  };
  // Note with retrigger handling: a new note-on of the same number cuts the old one; stale offs are skipped.
  E.note = (note, vel, t, gateMs) => {
    const key = Math.max(0, Math.min(127, note + E.trn));
    if (E.lastOn[key] >= 0) E.send([0x80 | E.ch, key, 0], t);
    const id = ++E.onSeq;
    E.lastOn[key] = id; E.keyOf.set(id, key);
    E.send([0x90 | E.ch, key, Math.max(1, Math.min(127, vel | 0))], t);
    E.lastOnAt = Math.max(E.lastOnAt, t);
    if (gateMs != null) { E.offQueue.push({ t: (t || E.now()) + gateMs, note: key, id, raw: true }); tick(true); }
    return id;
  };
  // note: the note number as played (before transpose); with an id, the key that note-on actually used is looked up.
  E.noteOff = (note, t, id, raw = false) => {
    const key = id != null && E.keyOf.has(id) ? E.keyOf.get(id) : (raw ? note : Math.max(0, Math.min(127, note + E.trn)));
    if (id != null) E.keyOf.delete(id);
    if (id != null && E.lastOn[key] !== id) return;      // a newer note took over this key
    E.lastOn[key] = -1;
    E.send([0x80 | E.ch, key, 0], t);
  };
  E.panic = () => {
    E.offQueue.length = 0; if (outQ.some(m => m.bytes[0] === 0xF0)) E.dev = null; outQ.length = 0;
    // The FM-1 plays notes from every channel ("Midi Chn: All"), so a note stuck by another app may be on any of them.
    for (let c = 0; c < 16; c++) {
      for (let n = 0; n < 128; n++) E.send([0x80 | c, n, 0]);
      E.send([0xB0 | c, 64, 0]);
    }
    E.lastOn.fill(-1); E.keyOf.clear();
    if (E.out && E.out.isSoft) E.out.reset();       // software FM-1: also drop anything it has scheduled ahead
  };

  // ---- ticker (worker timer: not throttled in background tabs) ----
  const worker = new Worker(URL.createObjectURL(new Blob(
    ['let h=null;onmessage=e=>{clearInterval(h);if(e.data>0)h=setInterval(()=>postMessage(0),e.data)}'], { type: 'text/javascript' })));
  let lastPlan = 0;
  worker.onmessage = () => {
    flushOut();
    const now = E.now();
    if (now - lastPlan < 15 && E.timing === 'jit') return;     // plan every ~15 ms, dispatch every tick (~4 ms)
    lastPlan = now;
    const horizon = now + LOOKAHEAD;
    for (const p of E.producers) { try { p.schedule(horizon); } catch (err) { console.error(err); } }
    E.offQueue.sort((a, b) => a.t - b.t);
    while (E.offQueue.length && E.offQueue[0].t <= horizon) { const o = E.offQueue.shift(); E.noteOff(o.note, o.t, o.id, true); }
    if (E.onTick) E.onTick();
    flushOut();
    if (!E.producers.size && !E.offQueue.length && !outQ.length) tick(false);
  };
  let running = false;
  const tick = on => { if (on !== running) { worker.postMessage(on ? (E.timing === 'jit' ? 2 : 20) : 0); running = on; } };
  E.start = p => { E.producers.add(p); tick(true); };
  E.stop = p => {
    E.producers.delete(p);
    if (!E.producers.size && E.timing === 'jit') {
      // Queued messages are still ours: drop them, release anything that sounded, and resync the voice next time.
      const hadSysex = outQ.some(m => m.bytes[0] === 0xF0);
      // Queued note-offs belong to notes that already sounded (their key is no longer marked held): send them now.
      // Dropping them left notes hanging. Queued note-ons and voice diffs are dropped.
      outQ.filter(m => (m.bytes[0] & 0xF0) === 0x80).forEach(m => rawSend(m.bytes));
      outQ.length = 0; E.offQueue.length = 0;
      for (let n = 0; n < 128; n++) if (E.lastOn[n] >= 0) { rawSend([0x80 | E.ch, n, 0]); E.lastOn[n] = -1; }
      E.keyOf.clear();
      if (hadSysex) E.dev = null;
      E.lastOnAt = 0; E.voiceReadyAt = 0;
    } else if (!E.producers.size) {
      // Notes may already be queued (timestamped) up to LOOKAHEAD ahead and can't be recalled, so the offs go after them.
      const at = Math.max(E.now(), E.lastOnAt + 1);
      E.offQueue.splice(0).forEach(o => E.noteOff(o.note, Math.min(o.t, at), o.id, true));
    }
  };
  return E;
})();
