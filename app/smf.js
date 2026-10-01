// Standard MIDI File parser -> flat, time-sorted note/controller list in milliseconds (tempo map applied).
'use strict';
const SMF = (() => {
  function parse(buf) {
    const d = new DataView(buf.buffer || buf, buf.byteOffset || 0, buf.byteLength);
    let p = 0;
    const str = n => { let s = ''; for (let i = 0; i < n; i++) s += String.fromCharCode(d.getUint8(p + i)); p += n; return s; };
    const u32 = () => { const v = d.getUint32(p); p += 4; return v; };
    const u16 = () => { const v = d.getUint16(p); p += 2; return v; };
    const vlq = () => { let v = 0, b; do { b = d.getUint8(p++); v = (v << 7) | (b & 0x7F); } while (b & 0x80); return v; };

    // Some files (RIFF RMID) wrap the SMF; find the MThd header.
    for (let i = 0; i < Math.min(d.byteLength - 4, 64); i++) if (d.getUint32(i) === 0x4D546864) { p = i; break; }
    if (str(4) !== 'MThd') throw new Error('Not a MIDI file (no MThd header)');
    const hlen = u32(), format = u16(), ntrk = u16(), div = u16();
    p += hlen - 6;
    const smpte = div & 0x8000;
    const ppq = smpte ? null : div;
    const msPerTickSmpte = smpte ? 1000 / ((256 - (div >> 8)) * (div & 0xFF)) : 0;

    const raw = [];            // {tick, order, kind, ...}
    const tempos = [];         // {tick, usPerQ}
    let names = [], order = 0;
    for (let t = 0; t < ntrk && p < d.byteLength - 8; t++) {
      const id = str(4), len = u32(), end = p + len;
      if (id !== 'MTrk') { p = end; continue; }
      let tick = 0, status = 0, tname = '';
      while (p < end) {
        tick += vlq();
        let b = d.getUint8(p);
        if (b & 0x80) { status = b; p++; } else if (!status) { p++; continue; }
        const type = status & 0xF0, ch = status & 0x0F;
        if (status === 0xFF) {
          const mt = d.getUint8(p++), ml = vlq();
          if (mt === 0x51 && ml === 3) tempos.push({ tick, us: (d.getUint8(p) << 16) | (d.getUint8(p + 1) << 8) | d.getUint8(p + 2) });
          if (mt === 0x03 && !tname) { const s = p; tname = ''; for (let i = 0; i < ml; i++) tname += String.fromCharCode(d.getUint8(s + i)); }
          p += ml; status = 0;                       // meta events cancel running status
          if (mt === 0x2F) break;
        } else if (status === 0xF0 || status === 0xF7) {
          p += vlq(); status = 0;
        } else if (type === 0xC0 || type === 0xD0) {
          raw.push({ tick, order: order++, trk: t, kind: type === 0xC0 ? 'pc' : 'at', ch, a: d.getUint8(p++) });
        } else {
          const a = d.getUint8(p++), c = d.getUint8(p++);
          if (type === 0x90 && c > 0) raw.push({ tick, order: order++, trk: t, kind: 'on', ch, note: a, vel: c });
          else if (type === 0x80 || type === 0x90) raw.push({ tick, order: order++, trk: t, kind: 'off', ch, note: a });
          else if (type === 0xB0) raw.push({ tick, order: order++, trk: t, kind: 'cc', ch, a, b: c });
          else if (type === 0xE0) raw.push({ tick, order: order++, trk: t, kind: 'bend', ch, a, b: c });
        }
      }
      names[t] = tname.trim();
      p = end;
    }

    // tick -> ms with tempo map
    tempos.sort((x, y) => x.tick - y.tick);
    if (!tempos.length || tempos[0].tick > 0) tempos.unshift({ tick: 0, us: 500000 });
    const seg = []; let ms = 0;
    for (let i = 0; i < tempos.length; i++) {
      if (i) ms += (tempos[i].tick - tempos[i - 1].tick) * tempos[i - 1].us / 1000 / ppq;
      seg.push({ tick: tempos[i].tick, ms, us: tempos[i].us });
    }
    const toMs = tick => {
      if (smpte) return tick * msPerTickSmpte;
      let lo = 0, hi = seg.length - 1;
      while (lo < hi) { const m = (lo + hi + 1) >> 1; if (seg[m].tick <= tick) lo = m; else hi = m - 1; }
      const s = seg[lo]; return s.ms + (tick - s.tick) * s.us / 1000 / ppq;
    };

    raw.sort((x, y) => x.tick - y.tick || (x.kind === 'off' ? -1 : 0) - (y.kind === 'off' ? -1 : 0) || x.order - y.order);
    // pair note-ons with offs per (channel, note), FIFO
    const open = new Map(), notes = [], ctrl = [];
    for (const e of raw) {
      const t = toMs(e.tick);
      if (e.kind === 'on') {
        const k = e.ch * 128 + e.note;
        const n = { t, dur: null, ch: e.ch, note: e.note, vel: e.vel, trk: e.trk };
        notes.push(n);
        (open.get(k) || open.set(k, []).get(k)).push(n);
      } else if (e.kind === 'off') {
        const q = open.get(e.ch * 128 + e.note);
        if (q && q.length) { const n = q.shift(); n.dur = Math.max(5, t - n.t); }
      } else ctrl.push({ t, ch: e.ch, kind: e.kind, a: e.a, b: e.b });
    }
    // loops, not Math.max(...spread): spreading 100k+ notes overflows the call stack
    let last = 0;
    for (const n of notes) if (n.t + (n.dur || 0) > last) last = n.t + (n.dur || 0);
    for (const c of ctrl) if (c.t > last) last = c.t;
    notes.forEach(n => { if (n.dur == null) n.dur = Math.max(50, last - n.t); });
    const bpm = 60000000 / tempos[0].us;
    const chans = {};
    notes.forEach(n => {
      const c = chans[n.ch] || (chans[n.ch] = { ch: n.ch, count: 0, lo: 127, hi: 0, trks: new Set(), pc: null });
      c.count++; c.lo = Math.min(c.lo, n.note); c.hi = Math.max(c.hi, n.note); c.trks.add(n.trk);
    });
    ctrl.filter(c => c.kind === 'pc').forEach(c => { if (chans[c.ch] && chans[c.ch].pc == null) chans[c.ch].pc = c.a; });
    Object.values(chans).forEach(c => c.name = [...c.trks].map(i => names[i]).filter(Boolean).join(' / '));
    return { format, ppq, bpm, duration: last, notes, ctrl, chans, trackNames: names };
  }
  return { parse };
})();
