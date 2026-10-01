// Speech tab: type text (or record / open audio) and the FM-1 — hardware or the Software FM-1 — speaks it.
// Speech.js builds the plan; this file plays its timed MIDI events through the Engine, draws the tracks and the lint.
'use strict';
const SpeechUI = (() => {
  const st = { src: 'text', rec: null, recLabel: '', recorder: null, playing: null, last: null };
  const opts = () => ({
    tone: $('spTone').value, base: +$('spKey').value, speed: +$('spSpeed').value / 100,
    accent: +$('spAccent').value, bright: +$('spBright').value, design: $('spDesign').value, smooth: $('spSmooth').value,
    ...({ glide: { diph: 'sweep', diphEdge: 0.15 }, all: { diph: 'sweep', diphAll: true, diphEdge: 0.15 }, off: {} })[$('spDiph').value],
  });
  const status = s => { $('spStatus').textContent = s; };
  async function ensure() {
    if (Speech.ready()) return true;
    status('Loading dictionary and voice units…');
    try { await Speech.load('speech/'); status(''); return true; }
    catch (e) { status(''); log('Could not load speech data: ' + e.message, 'err'); return false; }
  }

  // text/recording -> {frames, notes, events}
  async function build() {
    if (!(await ensure())) return null;
    const o = Speech.character($('spChar').value, opts());   // character preset on top of the sliders
    let frames, emax = 0, info = null;
    if (st.src === 'text') {
      const text = $('spText').value.trim();
      if (!text) { status('Type something first.'); return null; }
      info = Speech.speak(text, { ...o, tone: o.tone === 'follow' ? 'grammar' : o.tone });   // keeps a character's 'sing'
      frames = info.frames;
    } else {
      if (!st.rec) { status('Record or open some audio first.'); return null; }
      const r = Speech.framesFromRecording(st.rec, { tone: o.tone === 'flat' ? 'flat' : 'follow', speed: o.speed });
      frames = r.frames; emax = r.emax;
    }
    const notes = Speech.plan(frames, { ...o, emax });
    const events = Speech.toEvents(notes, { ...o, ch: Engine.ch, fxCh: S.fxCh });
    st.last = { frames, notes, events };
    draw(frames, o);
    renderLint(info);
    return st.last;
  }

  // ---- playback through the Engine (FM-1 or Software FM-1) ----
  function play(events) {
    stop();
    Bus.dispatchEvent(new CustomEvent('stopall', { detail: 'speech' }));
    const t0 = Engine.now() + 60, keys = new Set();
    let i = 0;
    const prod = {
      schedule(h) {
        while (i < events.length && t0 + events[i].t <= h) {
          const e = events[i++];
          Engine.send(e.b, t0 + e.t);
          if ((e.b[0] & 0xF0) === 0x90) keys.add(e.b[1]);
        }
        if (i >= events.length && st.playing && st.playing.prod === prod) {
          // everything is queued: leave the Engine's queue to drain (Engine.stop would drop the tail)
          Engine.producers.delete(prod);
          const end = t0 + events[events.length - 1].t - Engine.now();
          setTimeout(() => { if (st.playing && st.playing.prod === prod) finish(); }, Math.max(0, end) + 100);
        }
      },
    };
    st.playing = { prod, keys };
    Engine.forgetDevice();                  // our param diffs bypass the Engine's mirror of the edit buffer
    Engine.start(prod);
    $('spSay').textContent = '■ Stop'; $('spSay').classList.add('on');
  }
  function finish() {
    st.playing = null; Engine.forgetDevice();
    $('spSay').textContent = '▶ Speak'; $('spSay').classList.remove('on');
  }
  function stop() {
    if (!st.playing) return;
    const { prod, keys } = st.playing;
    Engine.stop(prod);
    if (Engine.out && Engine.out.isSoft) Engine.out.reset();
    keys.forEach(k => Engine.send([0x80 | Engine.ch, k, 0]));
    finish();
  }
  async function speak() {
    if (st.playing) { stop(); return; }
    if (!Engine.out) { log('Choose an output first (the Software FM-1 works without hardware).', 'err'); return; }
    const r = await build();
    if (!r || !r.events.length) { if (r) status('Nothing to say (silence).'); return; }
    status(`${r.notes.length} notes · ${(r.events[r.events.length - 1].t / 1000).toFixed(1)} s · ${r.events.filter(e => e.b[0] === 0xF0).length} parameter changes`);
    play(r.events);
  }

  // ---- offline render with the software model ----
  function renderWav(events, sr = 44100) {
    const core = new FM1Core(sr);
    for (const e of events) core.queue(e.b, Math.round(e.t / 1000 * sr));
    const n = Math.ceil((events[events.length - 1].t / 1000 + 0.4) * sr), out = new Float32Array(n), blk = new Float32Array(128);
    for (let f = 0; f < n; f += 128) { core.render(blk, f); out.set(blk.subarray(0, Math.min(128, n - f)), f); }
    let peak = 1e-9; for (const v of out) peak = Math.max(peak, Math.abs(v));
    const b = new DataView(new ArrayBuffer(44 + n * 2)), w = (o, s) => [...s].forEach((c, i) => b.setUint8(o + i, c.charCodeAt(0)));
    w(0, 'RIFF'); b.setUint32(4, 36 + n * 2, true); w(8, 'WAVEfmt '); b.setUint32(16, 16, true); b.setUint16(20, 1, true); b.setUint16(22, 1, true);
    b.setUint32(24, sr, true); b.setUint32(28, sr * 2, true); b.setUint16(32, 2, true); b.setUint16(34, 16, true); w(36, 'data'); b.setUint32(40, n * 2, true);
    for (let i = 0; i < n; i++) b.setInt16(44 + i * 2, Math.round(out[i] / peak * 0.9 * 32767), true);
    return new Blob([b], { type: 'audio/wav' });
  }
  async function downloadWav() {
    const r = await build();
    if (!r || !r.events.length) return;
    status('Rendering…');
    await new Promise(res => setTimeout(res, 20));
    const blob = renderWav(r.events), a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = (st.src === 'text' ? $('spText').value.trim().slice(0, 32).replace(/[^\w]+/g, '_') : 'recording') + '_fm1.wav';
    a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 5000);
    status('Saved WAV (Software FM-1 render).');
  }

  // ---- recording / audio files ----
  async function useAudio(buf, label) {
    status('Decoding…');
    const AC = window.OfflineAudioContext || window.webkitOfflineAudioContext;
    const ab = await new AC(1, 1, 44100).decodeAudioData(buf);
    const x = ab.getChannelData(0).slice(0, ab.sampleRate * 15);
    status('Analysing…');
    await new Promise(res => setTimeout(res, 20));
    st.rec = Speech.analyse(x, ab.sampleRate);
    st.recLabel = `${label}: ${(x.length / ab.sampleRate).toFixed(1)} s`;
    setSrc('rec');
    if ($('spTone').value === 'grammar') $('spTone').value = 'follow';
    status('');
    await build();
  }
  async function record() {
    if (st.recorder) { st.recorder.stop(); return; }
    if (!navigator.mediaDevices?.getUserMedia) { log('Microphone recording is not available in this browser.', 'err'); return; }
    let stream;
    try { stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } }); }
    catch (e) { log('Microphone not available: ' + e.message, 'err'); return; }
    const mr = new MediaRecorder(stream), chunks = [];
    mr.ondataavailable = e => chunks.push(e.data);
    mr.onstop = async () => {
      stream.getTracks().forEach(t => t.stop());
      st.recorder = null; $('spRec').textContent = '● Record'; $('spRec').classList.remove('on');
      try { await useAudio(await new Blob(chunks).arrayBuffer(), 'Microphone'); } catch (e) { status(''); log('Could not read the recording: ' + e.message, 'err'); }
    };
    mr.start(); st.recorder = mr;
    $('spRec').textContent = '■ Stop recording'; $('spRec').classList.add('on');
    status('Recording… (up to 10 s)');
    setTimeout(() => { if (mr.state === 'recording') mr.stop(); }, 10000);
  }
  function setSrc(src) {
    st.src = src;
    $('spSrc').querySelectorAll('button').forEach(b => b.classList.toggle('on', b.dataset.src === src));
    $('spText').hidden = src !== 'text';
    $('spSrcInfo').textContent = src === 'rec' ? (st.recLabel || 'no recording yet') : '';
  }

  // ---- view: energy, formants, pitch contour ----
  function draw(frames, o) {
    const cv = $('spView'), dpr = window.devicePixelRatio || 1, W = cv.clientWidth, H = cv.clientHeight;
    cv.width = W * dpr; cv.height = H * dpr;
    const g = cv.getContext('2d'); g.scale(dpr, dpr); g.clearRect(0, 0, W, H);
    if (!frames.length) return;
    const css = getComputedStyle(document.body), col = v => css.getPropertyValue(v).trim();
    const x = k => k / frames.length * W, fy = f => H - 8 - Math.min(1, f / 4000) * (H - 16);
    const emax = Math.max(...frames.map(f => f.e));
    g.fillStyle = col('--line');
    frames.forEach((f, k) => { const h = Math.max(0, (f.e - emax + 50) / 50) * (H - 16); g.fillRect(x(k), H - 8 - h, Math.max(1, W / frames.length), h); });
    frames.forEach((f, k) => {
      if (f.e < emax - 42) return;
      if (f.v) { g.fillStyle = col('--accent2'); f.F.forEach(F => g.fillRect(x(k), fy(F) - 1, 2, 2)); }
      else { g.fillStyle = col('--dim'); g.fillRect(x(k), fy(f.cent) - 1, 2, 2); }
    });
    g.strokeStyle = col('--accent'); g.lineWidth = 2; g.beginPath();
    frames.forEach((f, k) => { const y = H / 2 - (f.semi || 0) * 5; k ? g.lineTo(x(k), y) : g.moveTo(x(k), y); });
    g.stroke();
    g.fillStyle = col('--dim'); g.font = '10px system-ui';
    let prev = '';
    frames.forEach((f, k) => { if (f.p && f.p !== prev && f.p !== '_') { g.fillText(f.p, x(k), 11); } prev = f.p; });
    g.fillText('formants 0–4 kHz · pitch contour (orange, semitones around key ' + o.base + ')', 6, H - 2);
  }
  function renderLint(info) {
    const words = $('spWords'), lint = $('spLint');
    words.innerHTML = ''; lint.innerHTML = '';
    if (!info) return;
    for (const s of info.sentences) {
      for (const w of s.words) {
        const el = document.createElement('span');
        el.className = 'sp-w' + (w.weight ? '' : ' func') + (w.src === 'guess' ? ' guess' : '');
        el.style.setProperty('--wt', w.weight || 0);
        el.title = `${(w.pos || '').toLowerCase()}${w.weight ? ` · accent ${Math.round(w.weight * 100)}%` : ' · unaccented'} · /${w.ph.join(' ').toLowerCase()}/` + (w.src !== 'dict' ? ` (${w.src})` : '');
        el.textContent = w.text + (w.brk ? ',' : '');
        words.appendChild(el);
      }
      const end = document.createElement('span'); end.className = 'sp-end'; end.textContent = s.end + ' ' + { stmt: '↘', excl: '↘!', yn: '↗', wh: '↘?' }[s.type];
      words.appendChild(end);
    }
    const LABEL = { number: 'number', abbr: 'abbrev.', spell: 'spelled', guess: 'guessed', tone: 'intonation', style: 'style', grammar: 'grammar' };
    for (const n of info.notes) {
      const li = document.createElement('div');
      li.className = 'sp-note ' + n.k;
      const b = document.createElement('b'); b.textContent = LABEL[n.k] || n.k;
      li.append(b, ' ' + n.m);
      lint.appendChild(li);
    }
  }

  function init() {
    for (const [k, c] of Object.entries(Speech.CHARACTERS)) $('spChar').add(new Option(c.label, k));
    const lbl = (id, f) => { const u = () => $(id + 'Lbl').textContent = f(+$(id).value); $(id).addEventListener('input', u); u(); };
    const NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B'];
    lbl('spKey', v => NAMES[v % 12] + (Math.floor(v / 12) - 1));
    lbl('spSpeed', v => v + '%'); lbl('spAccent', v => v ? '±' + v + ' st' : 'off'); lbl('spBright', v => v);
    try { const s = JSON.parse(localStorage.getItem('fm1.speech') || '{}'); for (const k of ['spText', 'spChar', 'spSmooth', 'spDiph', 'spTone', 'spDesign', 'spKey', 'spSpeed', 'spAccent', 'spBright']) if (s[k] != null) { $(k).value = s[k]; $(k).dispatchEvent(new Event('input')); } } catch {}
    const save = () => { try { localStorage.setItem('fm1.speech', JSON.stringify(Object.fromEntries(['spText', 'spChar', 'spSmooth', 'spDiph', 'spTone', 'spDesign', 'spKey', 'spSpeed', 'spAccent', 'spBright'].map(k => [k, $(k).value])))); } catch {} };
    ['spText', 'spChar', 'spSmooth', 'spDiph', 'spTone', 'spDesign', 'spKey', 'spSpeed', 'spAccent', 'spBright'].forEach(k => $(k).addEventListener('change', () => { save(); if (Speech.ready()) build(); }));
    $('spSay').onclick = speak;
    $('spWav').onclick = downloadWav;
    $('spRec').onclick = record;
    $('spFile').onchange = async e => { const f = e.target.files[0]; e.target.value = ''; if (f) try { await useAudio(await f.arrayBuffer(), f.name); } catch (err) { status(''); log('Could not decode ' + f.name + ': ' + err.message, 'err'); } };
    $('spSrc').querySelectorAll('button').forEach(b => b.onclick = () => { setSrc(b.dataset.src); if (Speech.ready()) build(); });
    Bus.addEventListener('transport', e => { if (e.detail === 'speech') speak(); });
    Bus.addEventListener('stopall', e => { if (e.detail !== 'speech') stop(); });
    Bus.addEventListener('tab', e => { if (e.detail === 'speech') ensure().then(ok => ok && !st.last && build()); });
    if (S.tab === 'speech') ensure().then(ok => ok && build());
  }
  init();
  return { speak, stop, build };
})();
