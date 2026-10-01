// MIDI file player with a playhead. Per-channel voices are switched on the FM-1 with param diffs between
// notes (Engine.hit); channel 10 can be routed to the Drums tab's kit.
'use strict';
const Player = (() => {
  let song = null, fileName = '';
  const chan = {};          // ch -> { on, voice: {name, vced}|null, kit: bool }
  let activeCh = null;
  const st = { on: false, pos: 0, t0: 0, ni: 0, ci: 0, rate: 1, loopEnd: 0 };
  const ui = {};

  const rate = () => +ui.tempo.value / 100;
  const songAt = perf => (perf - st.t0) * st.rate;           // song ms at perf time
  const perfAt = ms => st.t0 + ms / st.rate;

  function seekIndex(ms) {
    st.ni = song.notes.findIndex(n => n.t >= ms); if (st.ni < 0) st.ni = song.notes.length;
    st.ci = song.ctrl.findIndex(c => c.t >= ms); if (st.ci < 0) st.ci = song.ctrl.length;
  }
  const producer = {
    schedule(horizon) {
      if (!song) return;
      const now = Engine.now();
      let guard = 0;
      while (guard++ < 4) {
        const songH = songAt(horizon);
        const trans = +ui.trans.value || 0, velK = +ui.vel.value / 100;
        // controllers (sustain, mod wheel, pitch bend) from included channels
        while (st.ci < song.ctrl.length && song.ctrl[st.ci].t < songH) {
          const c = song.ctrl[st.ci++], cs = chan[c.ch];
          if (!cs || !cs.on || cs.kit) continue;
          const t = Math.max(perfAt(c.t), now);
          if (c.kind === 'cc' && (c.a === 64 || c.a === 1)) Engine.send([0xB0 | Engine.ch, c.a, c.b], t);
          else if (c.kind === 'bend' && ui.bend.checked) Engine.send([0xE0 | Engine.ch, c.a, c.b], t);
        }
        while (st.ni < song.notes.length && song.notes[st.ni].t < songH) {
          const n = song.notes[st.ni++], cs = chan[n.ch];
          if (!cs || !cs.on) continue;
          const t = Math.max(perfAt(n.t), now), gate = n.dur / st.rate;
          const vel = Math.max(1, Math.min(127, Math.round(n.vel * velK)));
          if (cs.kit) {
            const tr = Seq.trackForGm(n.note); if (!tr) continue;
            Engine.hit(Seq.voiceVced(tr.voices[0]), tr.note, vel, t, Math.min(gate, tr.gate));
          } else {
            const note = n.note + trans; if (note < 0 || note > 127) continue;
            Engine.hit(cs.voice ? cs.voice.vced : null, note, vel, t, gate);
          }
        }
        // end of song: loop or stop
        if (st.ni >= song.notes.length && songAt(horizon) >= song.duration) {
          if (ui.loop.checked) { st.t0 = perfAt(song.duration); st.ni = 0; st.ci = 0; continue; }
          if (!st.ending) { st.ending = true; setTimeout(() => { if (st.on && st.ending) stop(true); }, Math.max(0, perfAt(song.duration) - now + 50)); }
        }
        break;
      }
    },
  };
  function start() {
    if (!song || st.on) return;
    Bus.dispatchEvent(new CustomEvent('stopall', { detail: 'player' }));
    st.rate = rate(); st.on = true; st.ending = false;
    if (st.pos >= song.duration) st.pos = 0;
    st.t0 = Engine.now() + 60 - st.pos / st.rate;
    seekIndex(st.pos);
    Engine.start(producer);
    ui.play.textContent = '■ Stop'; ui.play.classList.add('on');
    requestAnimationFrame(frame);
  }
  function stop(ended = false) {
    if (!st.on) return;
    st.on = false;
    st.pos = ended ? 0 : Math.max(0, Math.min(song.duration, songAt(Engine.now())));
    Engine.stop(producer);
    Engine.send([0xB0 | Engine.ch, 64, 0]);
    if (ui.bend.checked) Engine.send([0xE0 | Engine.ch, 0, 64]);
    ui.play.textContent = '▶ Play'; ui.play.classList.remove('on');
    showPos(st.pos);
  }
  function seek(ms) {
    const was = st.on;
    if (was) stop();
    st.pos = Math.max(0, Math.min(song.duration, ms));
    showPos(st.pos);
    if (was) start();
  }
  const fmt = ms => { const s = Math.max(0, ms / 1000); return Math.floor(s / 60) + ':' + String(Math.floor(s % 60)).padStart(2, '0'); };
  function showPos(ms) {
    ui.time.textContent = fmt(ms);
    if (!ui.dragging) ui.pos.value = song ? Math.round(ms / song.duration * 1000) : 0;
    drawHead(ms);
  }
  function frame() {
    if (!st.on) return;
    showPos(Math.min(song.duration, songAt(Engine.now())));
    requestAnimationFrame(frame);
  }

  /* ---------- piano roll overview ---------- */
  let rollImg = null;
  const COLORS = ['#f0a23b', '#5cc8c2', '#e5484d', '#8e7dff', '#46a758', '#e879c9', '#ffd166', '#4ea8de', '#c0c0c0', '#ff8c42', '#9bd35a', '#b392f0', '#f7768e', '#7dcfff', '#e0af68', '#bb9af7'];
  function drawRoll() {
    const c = ui.roll, dpr = window.devicePixelRatio || 1;
    rollImg = null;
    if (!c.clientWidth) return;                    // tab hidden; redrawn when the Player tab opens
    c.width = c.clientWidth * dpr; c.height = 120 * dpr;
    const x = c.getContext('2d', { willReadFrequently: true }); x.clearRect(0, 0, c.width, c.height);
    if (!song) return;
    let lo = 127, hi = 0;                              // loop: spreading huge note arrays overflows the stack
    for (const n of song.notes) { if (n.note < lo) lo = n.note; if (n.note > hi) hi = n.note; }
    const W = c.width, H = c.height, span = Math.max(12, hi - lo + 1);
    song.notes.forEach(n => {
      const cs = chan[n.ch];
      x.fillStyle = COLORS[n.ch]; x.globalAlpha = cs && cs.on ? 0.9 : 0.15;
      x.fillRect(n.t / song.duration * W, H - ((n.note - lo + 1) / span) * H, Math.max(1, n.dur / song.duration * W), Math.max(1, H / span));
    });
    x.globalAlpha = 1;
    rollImg = x.getImageData(0, 0, W, H);
  }
  function drawHead(ms) {
    const c = ui.roll; if (!rollImg || !song) return;
    const x = c.getContext('2d', { willReadFrequently: true }); x.putImageData(rollImg, 0, 0);
    const px = ms / song.duration * c.width;
    x.fillStyle = getComputedStyle(document.body).getPropertyValue('--text'); x.fillRect(px - 1, 0, 2, c.height);
  }

  /* ---------- channels ---------- */
  function buildChans() {
    const box = ui.chans; box.innerHTML = '';
    if (!song) return;
    Object.values(song.chans).sort((a, b) => a.ch - b.ch).forEach(info => {
      const ch = info.ch, cs = chan[ch];
      const row = document.createElement('div'); row.className = 'chan' + (activeCh === ch ? ' active' : '');
      row.style.setProperty('--c', COLORS[ch]);
      row.innerHTML = `<label class="chk"><input type="checkbox" class="on"></label>
        <button class="cname" title="Make active (library + adds a voice here)"></button>
        <span class="dim cinfo"></span>
        <span class="cvoice"></span>
        ${ch === 9 ? '<label class="chk" title="Play channel 10 with the Drums tab kit (GM drum map by track role)"><input type="checkbox" class="kit"> Drum kit</label>' : ''}
        <button class="use" title="Use the editor's current voice">editor voice</button><button class="solo" title="Play only this channel">solo</button>`;
      row.querySelector('.on').checked = cs.on;
      row.querySelector('.cname').textContent = `Ch ${ch + 1}` + (info.name ? ' · ' + info.name : '');
      row.querySelector('.cinfo').textContent = `${info.count} notes · ${noteName(info.lo)}–${noteName(info.hi)}` + (info.pc != null ? ` · GM prog ${info.pc + 1}` : '');
      row.querySelector('.cvoice').textContent = cs.kit ? '→ drum kit' : cs.voice ? '→ ' + cs.voice.name : '→ (whatever is loaded)';
      row.querySelector('.on').onchange = e => { cs.on = e.target.checked; drawRoll(); drawHead(st.pos); };
      row.querySelector('.cname').onclick = () => { activeCh = ch; buildChans(); };
      if (ch === 9) { const k = row.querySelector('.kit'); k.checked = cs.kit; k.onchange = () => { cs.kit = k.checked; buildChans(); }; }
      row.querySelector('.use').onclick = () => { cs.voice = { name: S.voice.name.trim(), vced: DX7.toVced(S.voice) }; cs.kit = false; buildChans(); };
      row.querySelector('.solo').onclick = () => { Object.keys(chan).forEach(k => chan[k].on = +k === ch); buildChans(); drawRoll(); drawHead(st.pos); };
      box.appendChild(row);
    });
  }
  async function openFile(f) {
    try {
      const s = SMF.parse(new Uint8Array(await f.arrayBuffer()));
      if (!s.notes.length) throw new Error('no notes in file');
      if (st.on) stop();
      song = s; fileName = f.name; st.pos = 0;
      Object.keys(chan).forEach(k => delete chan[k]);
      Object.values(song.chans).forEach(c => chan[c.ch] = { on: c.ch !== 9 || !!Seq.pattern.tracks.length, voice: null, kit: c.ch === 9 });
      activeCh = +Object.keys(song.chans)[0];
      ui.name.textContent = f.name;
      ui.info.textContent = `${song.notes.length} notes · ${Object.keys(song.chans).length} channels · ${Math.round(song.bpm)} BPM · ${fmt(song.duration)}`;
      ui.len.textContent = fmt(song.duration);
      [ui.play, ui.rew, ui.pos].forEach(el => el.disabled = false);
      showTab('player');
      buildChans(); drawRoll(); showPos(0);
      log(`Loaded ${f.name}: ${ui.info.textContent}`, 'ok');
    } catch (e) { log(`${f.name}: ${e.message}`, 'err'); }
  }

  function init() {
    Object.assign(ui, { play: $('plPlay'), rew: $('plRew'), loop: $('plLoop'), tempo: $('plTempo'), trans: $('plTrans'), vel: $('plVel'),
      pos: $('plPos'), time: $('plTime'), len: $('plLen'), roll: $('plRoll'), chans: $('plChans'), name: $('midName'), info: $('midInfo') });
    const bendBox = document.createElement('label'); bendBox.className = 'chk'; bendBox.title = 'Pass pitch bend from the file (bends every sounding note on the FM-1)';
    bendBox.innerHTML = '<input type="checkbox"> Pitch bend'; ui.loop.parentElement.after(bendBox); ui.bend = bendBox.querySelector('input');
    $('midInput').onchange = e => { if (e.target.files[0]) openFile(e.target.files[0]); e.target.value = ''; };
    ui.play.onclick = () => st.on ? stop() : start();
    ui.rew.onclick = () => seek(0);
    ui.tempo.oninput = () => {
      $('plTempoLbl').textContent = ui.tempo.value + '%';
      if (st.on) { const cur = songAt(Engine.now()); st.rate = rate(); st.t0 = Engine.now() - cur / st.rate; }
    };
    ui.vel.oninput = () => $('plVelLbl').textContent = ui.vel.value + '%';
    ui.pos.addEventListener('pointerdown', () => ui.dragging = true);
    ui.pos.addEventListener('change', () => { ui.dragging = false; if (song) seek(+ui.pos.value / 1000 * song.duration); });
    ui.pos.addEventListener('input', () => { if (song) { ui.time.textContent = fmt(+ui.pos.value / 1000 * song.duration); drawHead(+ui.pos.value / 1000 * song.duration); } });
    ui.roll.addEventListener('click', e => { if (song) seek(e.offsetX / ui.roll.clientWidth * song.duration); });
    window.addEventListener('resize', () => { drawRoll(); drawHead(st.pos); });
    Bus.addEventListener('midfile', e => openFile(e.detail));
    Bus.addEventListener('tab', e => { if (e.detail === 'player') requestAnimationFrame(() => { drawRoll(); drawHead(st.pos); }); });
    Bus.addEventListener('addvoice', e => {
      if (S.tab !== 'player' || activeCh == null || !chan[activeCh]) return;
      chan[activeCh].voice = { name: e.detail.name.trim(), vced: Array.from(e.detail.vced) }; chan[activeCh].kit = false;
      buildChans(); log(`Ch ${activeCh + 1} → ${e.detail.name.trim()}`);
    });
    Bus.addEventListener('transport', e => { if (e.detail === 'player' && song) st.on ? stop() : start(); });
    Bus.addEventListener('stopall', e => { if (e.detail !== 'player') stop(); });
  }
  init();
  return { openFile, start, stop };
})();
