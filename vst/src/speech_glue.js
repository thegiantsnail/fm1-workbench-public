// Glue between the plugin and the workbench's speech engine (app/dx7.js + app/speech.js, loaded
// unmodified before this file). It mirrors what app/speechui.js does to build a phrase, so the
// plugin speaks exactly as the workbench does.
function fm1SpeechLoad(unitsJson, dictText, posJson) {
  Speech.setData(JSON.parse(unitsJson), dictText, JSON.parse(posJson));
  return JSON.stringify(Object.keys(Speech.CHARACTERS).map(k => [k, Speech.CHARACTERS[k].label]));
}

function fm1SpeechCompile(requestJson) {
  const q = JSON.parse(requestJson);
  const diph = ({ glide: { diph: 'sweep', diphEdge: 0.15 }, all: { diph: 'sweep', diphAll: true, diphEdge: 0.15 }, off: {} })[q.diph] || {};
  const sliders = { tone: q.tone, base: q.key, speed: q.speed / 100, accent: q.accent, bright: q.bright,
    design: q.design, smooth: q.smooth, ...diph };
  const o = Speech.character(q.character, sliders);            // character preset on top of the sliders
  const info = Speech.speak(q.text, { ...o, tone: o.tone === 'follow' ? 'grammar' : o.tone });
  const notes = Speech.plan(info.frames, { ...o, emax: 0 });
  const events = Speech.toEvents(notes, { ...o, ch: q.ch, fxCh: q.fxCh });
  return JSON.stringify({
    events: events.map(e => [e.t, ...e.b]),
    notes: notes.length,
    lint: [
      ...(info.notes || []).map(n => n.m),
      ...(info.sentences || []).flatMap(s => s.words.filter(w => w.src === 'guess')
        .map(w => `"${w.text}" is not in the dictionary: pronunciation guessed`)),
    ],
  });
}
