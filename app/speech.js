// Speech for the FM-1: typed text or a recording -> frame tracks (energy, voicing, formants) -> FM-1 notes, one DX7
// voice per 20 ms frame (algorithm 22 formant voices, feedback-noise voices for hiss) -> timed MIDI events that play the
// same on the hardware and on the software FM-1.
//
// Text: normalised and "linted" (numbers, abbreviations, acronyms, unknown words, sentence type), looked up in CMUdict,
// then stitched from diphone units cut from analysed Windows TTS speech (speech/build_units.py). Intonation comes from
// the grammar: content-word accents, downstep, declination, phrase-final rises and falls, question type.
// Measured facts it relies on (FINDINGS.md): a parameter change applies to the NEXT note only; changing TRNP while a
// note is held hangs it, so pitch moves by key and TRNP stays 24; consecutive notes alternate between two keys an
// octave apart (upper key uses halved ratios) so a new frame never retriggers the key that is still releasing.
'use strict';
const Speech = (() => {
  const HOP = 10;                                   // analysis frame (ms)
  let units = null, dict = null, pos = null;

  // ---------------------------------------------------------------- data
  function setData(u, dictText, posData) {
    units = u; pos = posData || null;
    dict = new Map();
    const lines = dictText.split(/\r?\n/), syms = lines[0].split(',');
    let prev = '';
    for (let i = 1; i < lines.length; i++) {
      const ln = lines[i], sp = ln.indexOf(' ');
      if (sp < 0) continue;
      const w = prev.slice(0, +ln[0]) + ln.slice(1, sp);
      const ph = [];
      for (let k = sp + 1; k < ln.length; k++) ph.push(syms[ln.charCodeAt(k) - 48]);
      dict.set(w, ph);
      prev = w;
    }
  }
  async function load(base = 'speech/') {
    if (units) return;
    const [u, d, p] = await Promise.all([fetch(base + 'units.json').then(r => r.json()), fetch(base + 'cmudict.txt').then(r => r.text()),
      fetch(base + 'pos.json').then(r => r.json())]);
    setData(u, d, p);
  }
  const ready = () => !!units;

  // ---------------------------------------------------------------- text normalisation + lint
  const ONES = ['zero', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine', 'ten', 'eleven', 'twelve',
    'thirteen', 'fourteen', 'fifteen', 'sixteen', 'seventeen', 'eighteen', 'nineteen'];
  const TENS = ['', '', 'twenty', 'thirty', 'forty', 'fifty', 'sixty', 'seventy', 'eighty', 'ninety'];
  function under1000(n) {
    const w = [];
    if (n >= 100) { w.push(ONES[Math.floor(n / 100)], 'hundred'); n %= 100; }
    if (n >= 20) { w.push(TENS[Math.floor(n / 10)]); n %= 10; if (n) w.push(ONES[n]); }
    else if (n || !w.length) w.push(ONES[n]);
    return w;
  }
  function numWords(n) {
    if (n === 0) return ['zero'];
    const w = [];
    for (const [v, name] of [[1e12, 'trillion'], [1e9, 'billion'], [1e6, 'million'], [1e3, 'thousand']]) {
      if (n >= v) { w.push(...under1000(Math.floor(n / v)), name); n %= v; }
    }
    if (n) w.push(...under1000(n));
    return w;
  }
  function yearWords(n) {
    if (n >= 2000 && n < 2010) return numWords(n);
    const hi = Math.floor(n / 100), lo = n % 100;
    return [...under1000(hi), ...(lo === 0 ? ['hundred'] : lo < 10 ? ['oh', ONES[lo]] : under1000(lo))];
  }
  function ordinal(words) {
    const last = words[words.length - 1];
    const irr = { one: 'first', two: 'second', three: 'third', five: 'fifth', eight: 'eighth', nine: 'ninth', twelve: 'twelfth' };
    words[words.length - 1] = irr[last] || (last.endsWith('y') ? last.slice(0, -1) + 'ieth' : last + 'th');
    return words;
  }
  const ABBR = { 'mr.': 'mister', 'mrs.': 'missus', 'ms.': 'miz', 'dr.': 'doctor', 'st.': 'street', 'vs.': 'versus', 'etc.': 'et cetera',
    'e.g.': 'for example', 'i.e.': 'that is', 'jr.': 'junior', 'sr.': 'senior', 'prof.': 'professor', 'mt.': 'mount', 'approx.': 'approximately' };
  const SYMBOLS = { '&': 'and', '%': 'percent', '+': 'plus', '=': 'equals', '@': 'at', '#': 'number', '/': 'slash' };
  const LETTERS = { a: 'ey1', b: 'b iy1', c: 's iy1', d: 'd iy1', e: 'iy1', f: 'eh1 f', g: 'jh iy1', h: 'ey1 ch', i: 'ay1', j: 'jh ey1',
    k: 'k ey1', l: 'eh1 l', m: 'eh1 m', n: 'eh1 n', o: 'ow1', p: 'p iy1', q: 'k y uw1', r: 'aa1 r', s: 'eh1 s', t: 't iy1', u: 'y uw1',
    v: 'v iy1', w: 'd ah1 b ax0 l y uw0', x: 'eh1 k s', y: 'w ay1', z: 'z iy1' };
  const FUNC = new Set(('a an the and or but nor if of to in on at by for with from as is am are was were be been being do does did ' +
    'have has had i you he she it we they me him her us them my your his its our their this that these those not no so than then ' +
    'there here will would can could shall should may might must just very what who whom whose when where why how which up out ' +
    'into about oh').split(' '));
  const WH = new Set('what who whom whose when where why how which'.split(' '));
  const AUX = new Set('is am are was were do does did have has had can could will would shall should may might must'.split(' '));

  // Letter-to-sound guess for words CMUdict does not know (rough English spelling rules; the first vowel is stressed).
  const LTS = [
    [/^tion/, 'sh ax n'], [/^sion/, 'zh ax n'], [/^ough/, 'ao'], [/^augh/, 'ao'], [/^eigh/, 'ey'], [/^igh/, 'ay'], [/^tch/, 'ch'],
    [/^dge/, 'jh'], [/^sh/, 'sh'], [/^ch/, 'ch'], [/^th/, 'th'], [/^ph/, 'f'], [/^wh/, 'w'], [/^ck/, 'k'], [/^ng/, 'ng'], [/^qu/, 'k w'],
    [/^ee/, 'iy'], [/^ea/, 'iy'], [/^oo/, 'uw'], [/^ou/, 'aw'], [/^ow/, 'ow'], [/^oa/, 'ow'], [/^ai/, 'ey'], [/^ay/, 'ey'], [/^oi/, 'oy'],
    [/^oy/, 'oy'], [/^au/, 'ao'], [/^aw/, 'ao'], [/^ew/, 'uw'], [/^ie/, 'iy'], [/^ei/, 'ey'], [/^ar/, 'aa r'], [/^er/, 'er'], [/^ir/, 'er'],
    [/^ur/, 'er'], [/^or/, 'ao r'], [/^([bdfgklmnprstz])\1/, '$1'], [/^x/, 'k s'], [/^c(?=[eiy])/, 's'], [/^c/, 'k'], [/^g(?=[eiy])/, 'jh'],
    [/^y(?=[aeiou])/, 'y'], [/^y$/, 'iy'], [/^y/, 'ih'], [/^j/, 'jh'], [/^q/, 'k'], [/^h/, 'hh'],
    [/^a(?=[^aeiou]e$)/, 'ey'], [/^i(?=[^aeiou]e$)/, 'ay'], [/^o(?=[^aeiou]e$)/, 'ow'], [/^u(?=[^aeiou]e$)/, 'uw'], [/^e$/, ''],
    [/^a/, 'ae'], [/^e/, 'eh'], [/^i/, 'ih'], [/^o/, 'aa'], [/^u/, 'ah'], [/^([bdfgklmnprstvwz])/, '$1'], [/^./, '']];
  const VOWEL = /^(aa|ae|ah|ao|aw|ax|ay|eh|er|ey|ih|iy|ow|oy|uh|uw)/;
  function guess(w) {
    const out = [];
    for (let i = 0; i < w.length;) {
      const rest = w.slice(i);
      for (const [re, ph] of LTS) {
        const m = rest.match(re);
        if (!m) continue;
        const s = ph.replace('$1', m[1] || '');
        if (s) out.push(...s.split(' '));
        i += Math.max(1, m[0].length);
        break;
      }
    }
    let stressed = false;
    return out.map(p => VOWEL.test(p) && p !== 'ax' ? p + (stressed ? '0' : (stressed = true, '1')) : p === 'ax' ? 'ah0' : p);
  }
  function lookup(w) {
    if (dict.has(w)) return { ph: dict.get(w), src: 'dict' };
    const strip = [['ing', ['ih0', 'ng']], ["'s", ['z']], ['s', ['z']], ['es', ['ih0', 'z']], ['ed', ['d']], ['ly', ['l', 'iy0']],
      ['er', ['er0']], ['ers', ['er0', 'z']], ['ness', ['n', 'ah0', 's']], ['ment', ['m', 'ah0', 'n', 't']], ['ful', ['f', 'ah0', 'l']]];
    for (const [suf, ph] of strip) {
      if (w.length > suf.length + 2 && w.endsWith(suf)) {
        const stem = w.slice(0, -suf.length);
        for (const s of [stem, stem + 'e']) if (dict.has(s)) return { ph: [...dict.get(s), ...ph], src: 'suffix' };
      }
    }
    for (let k = 3; k <= w.length - 3; k++) {
      const a = w.slice(0, k), b = w.slice(k);
      if (dict.has(a) && dict.has(b)) return { ph: [...dict.get(a), ...dict.get(b)], src: 'compound' };
    }
    return { ph: guess(w), src: 'guess' };
  }

  // ---------------------------------------------------------------- part of speech (HMM from the Brown corpus)
  // speech/build_pos.py: 94% on held-out Brown text. Emissions P(tag|word)/P(tag); unknown words by suffix.
  function guessTags(w) {
    if (/ly$/.test(w)) return ['ADV'];
    if (/(ing|ed|ize|ise|ate|en)$/.test(w)) return ['VERB', 'ADJ', 'NOUN'];
    if (/(ous|ful|ive|able|ible|al|ic|ish|less|ary|ent|ant)$/.test(w)) return ['ADJ'];
    return ['NOUN'];
  }
  function tag(words) {
    if (!pos || !words.length) return words.map(() => null);
    const T = pos.tags.length;
    const emit = w => {
      const c = pos.lex[w.toLowerCase()];
      if (!c) { const g = guessTags(w.toLowerCase()); return pos.tags.map(t => g.includes(t) ? 0 : -300); }
      const d = {};
      for (let i = 0; i < c.length; i += 2) d[c[i]] = +c[i + 1];
      if (pos.hetero[w.toLowerCase()]) { d.N = d.N || 1; d.V = d.V || 1; }   // noun/verb pairs: let context decide
      return pos.codes.map((k, t) => d[k] ? 100 * Math.log(d[k] / 10) - pos.prior[t] : -900);
    };
    let V = emit(words[0]).map((e, t) => pos.start[t] + e);
    const B = [];
    for (let i = 1; i < words.length; i++) {
      const e = emit(words[i]), row = [], back = [];
      for (let t = 0; t < T; t++) {
        let k = 0;
        for (let u = 1; u < T; u++) if (V[u] + pos.trans[u][t] > V[k] + pos.trans[k][t]) k = u;
        row.push(V[k] + pos.trans[k][t] + e[t]); back.push(k);
      }
      V = row; B.push(back);
    }
    let t = 0;
    for (let u = 1; u < T; u++) if (V[u] > V[t]) t = u;
    const path = [t];
    for (let i = B.length - 1; i >= 0; i--) { t = B[i][t]; path.push(t); }
    return path.reverse().map(k => pos.tags[k]);
  }
  const NEG = new Set(['not', "n't", 'never', 'no', 'nothing', 'nobody', 'none', 'nowhere']);
  const ACCENT = { NOUN: 1, NUM: 1, ADJ: 0.9, ADV: 0.8, VERB: 0.7 };      // accent strength by word class

  // text -> sentences of words with phones, plus lint notes
  function lint(text) {
    const notes = [];
    let t = ' ' + text.replace(/[‘’]/g, "'").replace(/[“”]/g, '"') + ' ';
    t = t.replace(/\$(\d[\d,]*)(?:\.(\d\d))?/g, (m, d, c) => { notes.push({ k: 'number', m: `"${m}" read as money` }); return ` ${d} dollars${c && +c ? ' and ' + (+c) + ' cents' : ''} `; });
    t = t.replace(/(\d+)(st|nd|rd|th)\b/gi, (m, d) => { notes.push({ k: 'number', m: `"${m}" read as an ordinal` }); return ' ' + ordinal(numWords(+d)).join(' ') + ' '; });
    t = t.replace(/\b[a-z]{1,5}\.(?:[a-z]\.)?/gi, m => { const a = ABBR[m.toLowerCase()]; if (!a) return m; notes.push({ k: 'abbr', m: `"${m}" → "${a}"` }); return a; });
    t = t.replace(/[&%+=@#/]/g, m => ` ${SYMBOLS[m]} `);
    t = t.replace(/(\d)-(?=\d)/g, '$1 to ');
    t = t.replace(/([A-Za-z])-?(\d)/g, '$1 $2').replace(/(\d)([A-Za-z])/g, '$1 $2');
    t = t.replace(/(\d[\d,]*)(\.\d+)?/g, (m, d, frac) => {
      const n = +d.replace(/,/g, '');
      let w;
      if (!frac && /^\d{4}$/.test(d) && n >= 1100 && n <= 2099) { w = yearWords(n); notes.push({ k: 'number', m: `"${m}" read as a year (${w.join(' ')})` }); }
      else {
        w = numWords(n);
        if (frac) w.push('point', ...[...frac.slice(1)].map(c => ONES[+c]));
        if (m.length > 1 || frac) notes.push({ k: 'number', m: `"${m}" → ${w.join(' ')}` });
      }
      return ' ' + w.join(' ') + ' ';
    });
    if (/[!?.]{2,}/.test(t)) notes.push({ k: 'style', m: 'Repeated punctuation reads the same as a single mark.' });
    const sentences = [];
    let cur = { words: [], end: '.' };
    const toks = t.match(/[A-Za-z']+|[.,!?;:]/g) || [];
    for (const tok of toks) {
      if (/^[.!?]$/.test(tok)) { if (cur.words.length) { cur.end = tok; sentences.push(cur); } cur = { words: [], end: '.' }; continue; }
      if (/^[,;:]$/.test(tok)) { if (cur.words.length) cur.words[cur.words.length - 1].brk = true; continue; }
      const w = tok.toLowerCase().replace(/^'+|'+$/g, '');
      if (!w) continue;
      const caps = tok.length >= 2 && tok === tok.toUpperCase() && /[A-Z]/.test(tok);
      if (caps && (!dict.has(w) || (!/[aeiouy]/.test(w)) || w.length <= 3) && w.length <= 6 && !FUNC.has(w)) {
        notes.push({ k: 'spell', m: `"${tok}" spelled out as letters` });
        for (const c of w) if (LETTERS[c]) cur.words.push({ text: c.toUpperCase(), ph: LETTERS[c].split(' '), src: 'letter', func: false });
        continue;
      }
      const { ph, src } = lookup(w);
      if (src === 'guess') notes.push({ k: 'guess', m: `"${tok}" is not in the dictionary: pronunciation guessed (${ph.join(' ')})` });
      cur.words.push({ text: tok, ph, src, func: FUNC.has(w) });
    }
    if (cur.words.length) { sentences.push(cur); if (!/[.!?]\s*$/.test(text.trim())) notes.push({ k: 'style', m: 'No final punctuation: read as a statement.' }); }
    for (const s of sentences) {
      // word classes: accents follow them, and noun/verb pairs like REcord / reCORD get their stress
      const tags = tag(s.words.map(w => w.text));
      s.words.forEach((w, i) => {
        w.pos = w.src === 'letter' ? 'NOUN' : tags[i];
        const lw = w.text.toLowerCase();
        // auxiliaries and modals are tagged VERB but are not accented ("will", "can", "is")
        w.weight = NEG.has(lw) ? 1 : (AUX.has(lw) || /^(be|been|being|'s|'re|'m|'ll|'d|'ve)$/.test(lw)) ? 0 : (ACCENT[w.pos] || 0);
        const het = pos && pos.hetero[lw];
        if (het && w.src === 'dict' && (w.pos === 'VERB' || w.pos === 'NOUN' || w.pos === 'ADJ')) {
          const pick = w.pos === 'VERB' ? het[1] : het[0];
          w.ph = pick.split(' ');
          const sy = pick.split(' ').filter(x => /\d/.test(x)), at = sy.findIndex(x => x.endsWith('1'));
          notes.push({ k: 'grammar', m: `"${w.text}" read as a ${w.pos === 'VERB' ? 'verb' : w.pos === 'ADJ' ? 'adjective' : 'noun'}: stress on syllable ${at + 1} of ${sy.length}` });
        }
      });
      const first = s.words[0].text.toLowerCase();
      s.type = s.end === '?' ? (WH.has(first) ? 'wh' : 'yn') : s.end === '!' ? 'excl' : 'stmt';
      const label = { wh: 'wh-question: falls at the end', yn: 'yes/no question: rises at the end', excl: 'exclamation: stronger accents, falls', stmt: 'statement: falls at the end' }[s.type];
      notes.push({ k: 'tone', m: `"${s.words.map(w => w.text).join(' ')}${s.end}" — ${label}` });
      let run = 0;
      for (const w of s.words) { run = w.brk ? 0 : run + 1; if (run === 22) notes.push({ k: 'style', m: 'Long stretch without a comma: add one so the pitch can reset and the voice can pause.' }); }
      if (s.type === 'yn' && s.words.length && !AUX.has(first)) notes.push({ k: 'tone', m: `Question without an auxiliary verb first ("${s.words[0].text} …"): intonation rise used anyway.` });
    }
    return { sentences, notes };
  }

  // ---------------------------------------------------------------- phones + intonation
  const phonesOf = w => w.ph.map(x => ({ p: x.toLowerCase() === 'ah0' ? 'ax' : x.replace(/\d/, '').toLowerCase(), st: /\d/.test(x) ? +x.slice(-1) : -1 }));
  const mainVowel = ph => { let m = ph.findIndex(x => x.st === 1); return m < 0 ? ph.findIndex(x => x.st >= 0) : m; };

  // Intonation from word classes (default). Each phrase (split at , ; :) has accents on content words — strength by
  // class, stepping down through the phrase — and a nuclear accent on its LAST accented word, where the tune turns:
  // statements / wh-questions / exclamations fall after it, yes/no questions dip on it and rise to the end,
  // non-final phrases rise (continuation). Pitch declines gently across the sentence; phrase-final syllables lengthen.
  // style: '' | 'uptalk' (statements rise like questions) | 'singsong' (accents alternate up and down) | 'drawl'
  // (only via vowelStretch/speed in the character). tone 'sing': each word on the next note of `melody` (semitones).
  function prosodyPos(sentences, { accent = 3, tone = 'grammar', style = '', melody = null, vowelStretch = 1 } = {}) {
    const seq = [{ p: '_', semi: 0, pause: 60 }], flat = tone === 'flat', sing = tone === 'sing' && melody && melody.length;
    let wordNo = 0;
    sentences.forEach((s0, si) => {
      const s = style === 'uptalk' && s0.type === 'stmt' ? { ...s0, type: 'yn' } : s0;
      const A = accent * (s.type === 'excl' ? 1.4 : 1), n = s.words.length;
      const phrases = [];
      let cur = [];
      s.words.forEach((w, i) => { cur.push(i); if (w.brk || i === n - 1) { phrases.push(cur); cur = []; } });
      phrases.forEach((phr, pi) => {
        const final = pi === phrases.length - 1;
        const wt = i => s.words[i].weight || (s.type === 'wh' && i === 0 ? 0.8 : 0);
        const acc = phr.filter(i => wt(i) > 0);
        const nuc = acc.length ? acc[acc.length - 1] : phr[phr.length - 1];
        let k = 0;
        phr.forEach(i => {
          const w = s.words[i], ph = phonesOf(w), main = mainVowel(ph);
          const decl = n > 1 ? 0.8 - 1.6 * i / (n - 1) : 0;
          const pre = i < nuc && wt(i) > 0 ? A * wt(i) * Math.pow(0.85, k) * (style === 'singsong' && k++ % 2 ? -0.8 : (style === 'singsong' ? 1 : (k++, 1))) : 0;
          const note = sing ? melody[wordNo++ % melody.length] : 0;
          ph.forEach((x, j) => {
            let semi = decl, semi2 = null;
            if (i < nuc) { if (j === main) semi += pre; else if (x.st === 2 && pre) semi += pre * 0.3; }
            else {
              // The tune turns ON the nuclear vowel (a ramp across it), so a one-syllable last word ("mat", "now",
              // "show") still falls or rises; anything after it continues at the end level.
              const on = i === nuc && j === main, past = i > nuc || j > main;
              const [from, to, after] = final && s.type === 'yn' ? [decl - 0.3 * A, (i === phr[phr.length - 1] ? 1.8 : 1) * A, A * 1.8]
                : final ? [decl + A, -A, -A] : [decl + 0.3 * A, decl + A, decl + A];
              if (on) { semi = from; semi2 = to; } else if (past) semi = after;
            }
            const lastWord = i === phr[phr.length - 1];
            const st = (lastWord && j >= main ? 1.3 : 1) * (x.st >= 0 ? vowelStretch : 1);
            if (sing) seq.push({ p: x.p, semi: note, semi2: null, stretch: st, w: w.text });
            else seq.push({ p: x.p, semi: flat ? 0 : semi, semi2: flat ? null : semi2, stretch: st, w: w.text });
          });
        });
        if (!final) seq.push({ p: '_', semi: 0, pause: 180 });
      });
      seq.push({ p: '_', semi: 0, pause: si === sentences.length - 1 ? 60 : 350 });
    });
    return seq;
  }

  // sentences -> [{p, semi, stretch, pause, w}]  ('_' = pause)
  function prosody(sentences, opts = {}) {
    if (opts.pos !== false && pos) return prosodyPos(sentences, opts);
    return prosodyList(sentences, opts);
  }
  // Earlier rules, kept for comparison (opts.pos = false): accents on words not in a function-word list, and the
  // final rise/fall starts at the last word's stressed syllable even when that word is "me" or "it".
  function prosodyList(sentences, { accent = 3, tone = 'grammar' } = {}) {
    const seq = [{ p: '_', semi: 0, pause: 60 }];
    sentences.forEach((s, si) => {
      const A = accent * (s.type === 'excl' ? 1.4 : 1);
      const n = s.words.length;
      let phraseStart = true;
      s.words.forEach((w, wi) => {
        const ph = w.ph.map(x => ({ p: x.toLowerCase() === 'ah0' ? 'ax' : x.replace(/\d/, '').toLowerCase(), st: /\d/.test(x) ? +x.slice(-1) : -1 }));
        let main = ph.findIndex(x => x.st === 1);
        if (main < 0) main = ph.findIndex(x => x.st >= 0);
        const last = wi === n - 1, phraseEnd = last || w.brk;
        const decl = n > 1 ? 1 - 2 * wi / (n - 1) : 0;
        ph.forEach((x, k) => {
          let semi = decl;
          if (!w.func) {
            if (k === main) semi += phraseStart ? A : A * 0.6;
            else if (x.st === 2) semi += 1;
          }
          if (last && k >= main && main >= 0) {
            if (s.type === 'yn') semi = k === main ? A : A * 1.8;
            else if (k > main) semi = -A;
          } else if (w.brk && k >= main && main >= 0) semi = k === main ? decl + A * 0.5 : decl + A;
          seq.push({ p: x.p, semi: tone === 'flat' ? 0 : semi, stretch: phraseEnd && k >= main ? 1.3 : 1, w: w.text });
        });
        if (!w.func) phraseStart = false;
        if (w.brk && !last) { seq.push({ p: '_', semi: 0, pause: 180 }); phraseStart = true; }
      });
      seq.push({ p: '_', semi: 0, pause: si === sentences.length - 1 ? 60 : 350 });
    });
    return seq;
  }

  // phones -> 10 ms frames by stitching diphone units: {e, v, F:[3], L:[3], cent, semi, p}
  function framesFromSeq(seq, { speed = 1, select = true } = {}) {
    const U = units.units, P = units.phones, raw = [];
    const push = (f, i) => raw.push({ e: f[0], v: f[1], F: [f[2], f[3], f[4]], L: [f[5], f[6], f[7]], cent: f[8], hf: f[9] ?? -30, i });
    // Candidates per diphone (units.json keeps up to 4; half-phones where the corpus has none)
    const cands = [];
    for (let i = 0; i < seq.length - 1; i++) {
      const a = seq[i].p, b = seq[i + 1].p, u = U[a + ' ' + b];
      if (u) cands.push(select ? [u, ...(u.alt || [])] : [u]);
      else {
        const pa = P[a] || P['_'], pb = P[b] || P['_'];
        cands.push([{ s: pa.f.length - pa.s, f: [...pa.f.slice(pa.s), ...pb.f.slice(0, pb.s)] }]);
      }
    }
    // Unit selection (Viterbi): consecutive units meet in the middle of the same phone, so their edge frames should
    // agree - join cost = formant jumps (log F1-F3, voiced frames), energy step and voicing flip; target cost = length
    // away from the most typical instance.
    const pick = cands.map(() => 0);
    if (select && cands.length) {
      const join = (x, y) => {
        const p = x.f[x.f.length - 1], q = y.f[0];
        let c = Math.abs(p[0] - q[0]) * 0.1 + (p[1] !== q[1] ? 1 : 0);
        if (p[1] && q[1]) for (let k = 2; k < 5; k++) c += Math.abs(Math.log(Math.max(1, p[k]) / Math.max(1, q[k]))) * 4;
        return c;
      };
      const target = (list, j) => Math.abs(list[j].f.length - list[0].f.length) * 0.15;
      let cost = cands[0].map((_, j) => target(cands[0], j));
      const back = [];
      for (let i = 1; i < cands.length; i++) {
        const row = [], bk = [];
        cands[i].forEach((y, j) => {
          let best = Infinity, bj = 0;
          cands[i - 1].forEach((x, k) => { const c = cost[k] + join(x, y); if (c < best) { best = c; bj = k; } });
          row.push(best + target(cands[i], j)); bk.push(bj);
        });
        cost = row; back.push(bk);
      }
      let j = cost.indexOf(Math.min(...cost));
      pick[cands.length - 1] = j;
      for (let i = cands.length - 1; i > 0; i--) { j = back[i - 1][j]; pick[i - 1] = j; }
    }
    for (let i = 0; i < seq.length - 1; i++) {
      const b = seq[i + 1].p, u = cands[i][pick[i]];
      u.f.forEach((f, k) => push(f, k < u.s ? i : i + 1));
      if (b === '_' && seq[i + 1].pause > 120) {           // long pause: extra silence
        for (let k = 0; k < (seq[i + 1].pause - 120) / HOP; k++) push([-90, 0, 500, 1500, 2500, 0, 0, 0, 0], i + 1);
      }
    }
    // tempo: phrase-final lengthening and overall speed, by repeating/skipping frames
    const out = [];
    let acc = 0;
    for (const f of raw) {
      acc += (seq[f.i].stretch || 1) / speed;
      while (acc >= 1) { out.push({ ...f, p: seq[f.i].p }); acc -= 1; }
    }
    // pitch target per frame; a phone with semi2 ramps from semi to semi2 across its frames
    const count = new Map(), seen = new Map();
    out.forEach(f => count.set(f.i, (count.get(f.i) || 0) + 1));
    out.forEach(f => {
      const q = seq[f.i], k = seen.get(f.i) || 0, n = count.get(f.i);
      seen.set(f.i, k + 1);
      f.target = q.semi2 == null ? (q.semi || 0) : q.semi + (q.semi2 - q.semi) * (n > 1 ? k / (n - 1) : 1);
    });
    // smooth the targets (±30 ms) into a contour
    for (let k = 0; k < out.length; k++) {
      let s = 0, n = 0;
      for (let j = Math.max(0, k - 3); j <= Math.min(out.length - 1, k + 3); j++) { s += out[j].target; n++; }
      out[k].semi = s / n;
    }
    // units come from different recordings: smooth formants and levels across the joins (voiced neighbours only)
    const sm = out.map((f, k) => {
      if (!f.v) return f;
      const nb = [out[k - 1], f, out[k + 1]].filter(x => x && x.v);
      const avg = get => [0, 1, 2].map(i => nb.reduce((s, x) => s + get(x)[i], 0) / nb.length);
      return { ...f, F: avg(x => x.F), L: avg(x => x.L) };
    });
    return sm;
  }

  function speak(text, opts = {}) {
    const { sentences, notes } = lint(text);
    const seq = prosody(sentences, opts);
    const frames = framesFromSeq(seq, opts);
    return { sentences, notes, seq, frames, emax: 0 };
  }

  // ---------------------------------------------------------------- recording analysis (JS port of speech/resynth.py)
  function fft(re, im) {                              // in-place radix-2
    const n = re.length;
    for (let i = 1, j = 0; i < n; i++) {
      let bit = n >> 1;
      for (; j & bit; bit >>= 1) j ^= bit;
      j ^= bit;
      if (i < j) { [re[i], re[j]] = [re[j], re[i]]; [im[i], im[j]] = [im[j], im[i]]; }
    }
    for (let len = 2; len <= n; len <<= 1) {
      const a = -2 * Math.PI / len, wr = Math.cos(a), wi = Math.sin(a);
      for (let i = 0; i < n; i += len) {
        let cr = 1, ci = 0;
        for (let k = 0; k < len / 2; k++) {
          const ur = re[i + k], ui = im[i + k], vr = re[i + k + len / 2] * cr - im[i + k + len / 2] * ci, vi = re[i + k + len / 2] * ci + im[i + k + len / 2] * cr;
          re[i + k] = ur + vr; im[i + k] = ui + vi; re[i + k + len / 2] = ur - vr; im[i + k + len / 2] = ui - vi;
          const t = cr * wr - ci * wi; ci = cr * wi + ci * wr; cr = t;
        }
      }
    }
  }
  function resample(x, from, to) {                    // windowed-sinc, low-passed at the new Nyquist
    if (from === to) return Float64Array.from(x);
    const ratio = from / to, n = Math.floor(x.length / ratio), out = new Float64Array(n), fc = Math.min(1, 1 / ratio) * 0.95, W = 16;
    for (let i = 0; i < n; i++) {
      const c = i * ratio, c0 = Math.floor(c);
      let s = 0, ws = 0;
      for (let k = c0 - W * Math.ceil(ratio); k <= c0 + W * Math.ceil(ratio); k++) {
        if (k < 0 || k >= x.length) continue;
        const d = (k - c) * fc, win = 0.5 + 0.5 * Math.cos(Math.PI * (k - c) / (W * Math.ceil(ratio) + 1));
        const h = (d === 0 ? 1 : Math.sin(Math.PI * d) / (Math.PI * d)) * win;
        s += x[k] * h; ws += h;
      }
      out[i] = ws ? s / ws : 0;
    }
    return out;
  }
  function lpc(x, order) {
    const r = new Float64Array(order + 1);
    for (let l = 0; l <= order; l++) { let s = 0; for (let i = l; i < x.length; i++) s += x[i] * x[i - l]; r[l] = s; }
    const a = new Float64Array(order + 1); a[0] = 1;
    if (r[0] <= 0) return a;
    let e = r[0];
    for (let i = 1; i <= order; i++) {
      let acc = r[i];
      for (let j = 1; j < i; j++) acc += a[j] * r[i - j];
      const k = -acc / e, prev = a.slice();
      for (let j = 1; j < i; j++) a[j] = prev[j] + k * prev[i - j];
      a[i] = k; e *= 1 - k * k;
      if (e <= 0) break;
    }
    return a;
  }
  function analyse(samples, sr) {
    const SR = 22050, a = resample(samples, sr, SR), lo = resample(a, SR, SR / 2), srl = SR / 2;
    const win = Math.floor(0.03 * SR), hop = Math.floor(HOP / 1000 * SR), frames = [];
    const ham = n => Float64Array.from({ length: n }, (_, i) => 0.54 - 0.46 * Math.cos(2 * Math.PI * i / (n - 1)));
    const hw = ham(win), hwl = ham(Math.floor(win / 2)), NF = 1024;
    let prevF = [500, 1500, 2500];
    for (let i = 0; i + win < a.length; i += hop) {
      const x = new Float64Array(win);
      let pow = 0;
      for (let k = 0; k < win; k++) { x[k] = a[i + k] * hw[k]; pow += x[k] * x[k]; }
      const e = 20 * Math.log10(Math.sqrt(pow / win) + 1e-9);
      // pitch: normalised autocorrelation, 70-320 Hz
      const lmin = Math.floor(SR / 320), lmax = Math.floor(SR / 70);
      // normalised by the energy of both overlapping parts (unbiased for long lags / low voices); voiced above 0.4
      const c2 = new Float64Array(win + 1);
      for (let k = 0; k < win; k++) c2[k + 1] = c2[k] + x[k] * x[k];
      let best = -1, bk = 0;
      for (let l = lmin; l < lmax; l++) {
        let s = 0; for (let k = l; k < win; k++) s += x[k] * x[k - l];
        const r = s / (Math.sqrt((c2[win] - c2[l]) * c2[win - l]) + 1e-12);
        if (r > best) { best = r; bk = l; }
      }
      const voiced = pow > 0 && best > 0.4, f0 = voiced ? SR / bk : 0;
      // formants: LPC-12 on the 11 kHz signal, peaks of the envelope
      const j = Math.floor(i / 2), nl = Math.floor(win / 2), xl = new Float64Array(nl);
      for (let k = 0; k < nl; k++) xl[k] = ((lo[j + k] || 0) - (k ? 0.97 * (lo[j + k - 1] || 0) : 0)) * hwl[k];
      const A = lpc(xl, 12), H = new Float64Array(512);
      for (let b = 0; b < 512; b++) {
        const w = Math.PI * b / 512;
        let re = 0, im = 0;
        for (let k = 0; k <= 12; k++) { re += A[k] * Math.cos(w * k); im -= A[k] * Math.sin(w * k); }
        H[b] = 1 / (Math.hypot(re, im) + 1e-9);
      }
      const cand = [];
      for (let b = 1; b < 511; b++) {
        const f = b * srl / 2 / 512;
        if (H[b] > H[b - 1] && H[b] >= H[b + 1] && f > 150 && f < 4500) cand.push(f);
      }
      const F = prevF.slice();
      [[200, 1000], [700, 2800], [1600, 3800]].forEach(([lf, hf], idx) => {
        const ok = cand.filter(f => f >= lf && f <= hf && (idx === 0 || f > F[idx - 1] + 150));
        if (ok.length) F[idx] = ok[0];
      });
      prevF = F;
      const L = F.map(f => 20 * Math.log10(H[Math.min(511, Math.floor(f / srl * 2 * 512))] + 1e-9));
      // spectral centroid
      const re = new Float64Array(NF), im = new Float64Array(NF);
      for (let k = 0; k < win; k++) re[k] = a[i + k] * (0.5 - 0.5 * Math.cos(2 * Math.PI * k / (win - 1)));
      fft(re, im);
      let num = 0, den = 0;
      let hiE = 0, allE = 1e-18;
      for (let b = 0; b <= NF / 2; b++) { const m = Math.hypot(re[b], im[b]), f = b * SR / NF; num += f * m; den += m; allE += m * m; if (f > 4000) hiE += m * m; }
      frames.push({ e, v: voiced ? 1 : 0, f0, F, L, cent: num / (den + 1e-9), hf: 10 * Math.log10(hiE / allE + 1e-18) });
    }
    return frames;
  }
  function framesFromRecording(frames, { tone = 'follow', speed = 1 } = {}) {
    const voicedF0 = frames.filter(f => f.v).map(f => f.f0).sort((x, y) => x - y);
    const med = voicedF0.length ? voicedF0[voicedF0.length >> 1] : 110;
    const out = [];
    let acc = 0;
    for (const f of frames) {
      acc += 1 / speed;
      while (acc >= 1) { out.push({ ...f, p: '', semi: tone === 'follow' && f.v ? 12 * Math.log2(f.f0 / med) : 0 }); acc -= 1; }
    }
    if (tone === 'follow') {                          // median-smooth the pitch, it jumps an octave at times
      const s = out.map(f => f.semi);
      out.forEach((f, k) => { const w = s.slice(Math.max(0, k - 3), k + 4).sort((x, y) => x - y); f.semi = Math.max(-12, Math.min(12, w[w.length >> 1])); });
    }
    return { frames: out, emax: Math.max(...frames.map(f => f.e)) };
  }

  // ---------------------------------------------------------------- FM-1 voices (same designs as speech/fm_speak.py)
  const A22 = 21;
  const ratioFor = (f, f0) => Math.min(31, Math.max(1, Math.round(f / f0)));
  function baseVoice(name) {
    const v = DX7.initVoice(name);
    Object.assign(v.g, { ALG: A22, FB: 0, OKS: 1, TRNP: 24, LFS: 40, LFD: 0, LPMD: 0, LAMD: 0 });
    v.ops.forEach(o => Object.assign(o, { OL: 0, R1: 95, R2: 60, R3: 60, R4: 72, L1: 99, L2: 99, L3: 99, L4: 0, KVS: 0, DT: 7, FC: 1, FF: 0 }));
    return v;
  }
  function voiced(f0, F, levels, body, bright) {
    const v = baseVoice('SPEECH V'), o = v.ops;
    Object.assign(o[2], { FC: ratioFor(F[0], f0), OL: levels[0] });
    Object.assign(o[3], { FC: ratioFor(F[1], f0), OL: levels[1] });
    Object.assign(o[4], { FC: ratioFor(F[2], f0), OL: levels[2] });
    Object.assign(o[5], { FC: 1, OL: bright });
    Object.assign(o[0], { FC: 1, OL: body });
    Object.assign(o[1], { FC: 1, OL: 40 });
    return v;
  }
  function fricative(f0, centre, withVoice) {
    const v = baseVoice('SPEECH U'), o = v.ops, r = ratioFor(centre, f0);
    Object.assign(o[3], { FC: r, OL: 90 });
    Object.assign(o[4], { FC: Math.min(31, r + 6), OL: 86 });
    Object.assign(o[2], { FC: Math.max(1, r - 5), OL: 70 });
    Object.assign(o[5], { FC: 1, OL: 96 });
    v.g.FB = 7;
    if (withVoice) { Object.assign(o[0], { FC: 1, OL: 82 }); Object.assign(o[1], { FC: 1, OL: 40 }); }
    return v;
  }
  // Same sound one key-octave up: every ratio halved exactly (odd ratios use the fine ratio: 3 -> 1 x 1.50).
  function octaveUp(v) {
    const w = DX7.clone(v);
    for (const o of w.ops) {
      if (o.MODE) continue;
      const r = (o.FC === 0 ? 0.5 : o.FC) * (1 + o.FF / 100) / 2;
      if (r < 1) { o.FC = 0; o.FF = Math.min(99, Math.round((r / 0.5 - 1) * 100)); }
      else { o.FC = Math.floor(r); o.FF = Math.min(99, Math.round((r / o.FC - 1) * 100)); }
    }
    return w;
  }
  const midiHz = n => 440 * Math.pow(2, (n - 69) / 12);

  // ---- alternative voiced designs (see FINDINGS.md "Speech: voice designs") ----
  // DX7 fixed-frequency operator (MODE 1): f = 10^(FC&3) * 10^(FF/100) Hz, FF steps of 2.3 %.
  function fixedFreq(o, f) {
    const l = Math.log10(Math.max(1, Math.min(9772, f)));
    let c = Math.floor(l), ff = Math.round((l - c) * 100);
    if (ff > 99) { c += 1; ff = 0; }
    Object.assign(o, { MODE: 1, FC: Math.max(0, Math.min(3, c)), FF: ff });
  }
  const lvAdd = (ol, db) => Math.max(0, Math.min(99, Math.round(ol + db / 0.75)));
  // Le Brun / Chafe: each formant from the two harmonics bracketing it, cross-faded by proximity, so the formant
  // centre moves smoothly instead of jumping a whole harmonic. DX7 algorithm 24 (6 -> 3,4,5; carriers 1-5):
  // F1 = pure-sine pair op1/op2, F2 = modulated pair op3/op4, F3 = modulated op5 (nearest harmonic).
  function voicedLeBrun(f0, F, levels, body, bright) {
    const v = baseVoice('SPEECH L'), o = v.ops;
    v.g.ALG = 23;                                       // algorithm 24 (0-based 23)
    const pair = (fc, lv, a, b) => {
      const r = Math.max(1, Math.min(30, fc / f0)), lo = Math.floor(r), w = r - lo;   // w: weight of the upper harmonic
      Object.assign(o[a], { FC: Math.max(1, lo), OL: lvAdd(lv, 20 * Math.log10(Math.max(1e-3, 1 - w))) });
      Object.assign(o[b], { FC: Math.min(31, lo + 1), OL: lvAdd(lv, 20 * Math.log10(Math.max(1e-3, w))) });
    };
    pair(F[0], Math.max(levels[0], body), 0, 1);
    pair(F[1], levels[1], 2, 3);
    Object.assign(o[4], { FC: ratioFor(F[2], f0), OL: levels[2] });
    Object.assign(o[5], { FC: 1, OL: bright });
    return v;
  }
  // Sine-wave speech (Remez & Rubin; also SAM): carriers at the EXACT formant frequencies (fixed-frequency operators),
  // plus a quiet fundamental for voicing. Algorithm 32: six carriers, no modulation.
  function voicedSine(f0, F, levels, body) {
    const v = baseVoice('SPEECH S'), o = v.ops;
    v.g.ALG = 31;
    [0, 1, 2].forEach(i => { fixedFreq(o[i], F[i]); o[i].OL = levels[i]; });
    Object.assign(o[3], { FC: 1, OL: Math.max(0, body - 20) });       // f0 (follows the key)
    return v;
  }
  // Exact formant centres with voicing: fixed-frequency carriers at F1-F3 modulated by op6 at f0 (ratio 1), so the
  // sidebands sit at F +- k*f0 - spaced by the pitch, centred on the true formant (algorithm 22 as before).
  function voicedFixedFM(f0, F, levels, body, bright) {
    const v = voiced(f0, F, levels, body, bright), o = v.ops;
    [2, 3, 4].forEach((k, i) => fixedFreq(o[k], F[i]));
    return v;
  }
  const DESIGNS = { harmonic: voiced, lebrun: voicedLeBrun, sine: voicedSine, fixedfm: voicedFixedFM };

  // ---- diphthongs as ONE note: the glide happens inside the voice, by staggered operator envelopes ----
  // Operator EG timing (software FM-1, speech/eg_table.cjs): ms for a 99 -> 0 fall to reach -20 dB at rate 33..80,
  // and for a 0 -> 99 rise to reach -6 dB at rate 22..80. A rise from 0 is a late onset: silent, then up quickly.
  const FALL20 = [1984, 1984, 1652, 1416, 1416, 1240, 1240, 992, 826, 826, 708, 620, 620, 496, 414, 414, 354, 310, 310, 248, 248, 208,
    178, 178, 156, 124, 124, 104, 90, 90, 78, 62, 62, 52, 52, 44, 40, 40, 32, 26, 26, 22, 20, 20, 16, 16, 14, 12];
  const RISE6 = [2230, 2230, 1910, 1672, 1672, 1338, 1338, 1114, 956, 956, 836, 668, 668, 558, 478, 478, 418, 418, 334, 278, 278, 238, 208,
    208, 168, 140, 140, 120, 104, 104, 84, 84, 70, 60, 60, 52, 42, 42, 34, 30, 30, 26, 20, 20, 18, 18, 14, 14, 14, 10, 8, 8, 8, 6, 6, 6, 6, 4, 4];
  const rateFor = (tab, r0, ms) => {
    let best = r0, err = 1e9;
    tab.forEach((t, k) => { const e = Math.abs(Math.log(t / Math.max(4, ms))); if (e < err) { err = e; best = r0 + k; } });
    return best;
  };
  const fade = (o, r) => Object.assign(o, { R1: 99, L1: 99, R2: r, L2: 0, R3: 99, L3: 0 });
  const grow = (o, r) => Object.assign(o, { R1: 99, L1: 0, R2: r, L2: 99, R3: 99, L3: 99 });
  const DIPH = /^(ay|aw|oy|ey|ow)/;
  // single: one voice (algorithm 22, op6 -> 3,4,5 and op2 -> 1): op3 = F1 start, op4 = F2 start fade out;
  // op5 = F2 end comes in late; op1 = F1 end (late) when F1 moves, else F3. The f0 modulators stay on throughout.
  function diphSingle(f0, A, B, body, bright, fallR, riseR) {
    const v = baseVoice('SPEECH D'), o = v.ops;
    const f1moves = Math.abs(Math.log(B.F[0] / A.F[0])) > 0.18;
    fixedFreq(o[2], A.F[0]); o[2].OL = A.lv[0]; if (f1moves) fade(o[2], fallR);
    fixedFreq(o[3], A.F[1]); o[3].OL = A.lv[1]; fade(o[3], fallR);
    fixedFreq(o[4], B.F[1]); o[4].OL = B.lv[1]; grow(o[4], riseR);
    if (f1moves) { fixedFreq(o[0], B.F[0]); o[0].OL = B.lv[0]; grow(o[0], riseR); }
    else { fixedFreq(o[0], Math.sqrt(A.F[2] * B.F[2])); o[0].OL = Math.round((A.lv[2] + B.lv[2]) / 2); }
    Object.assign(o[1], { FC: 1, OL: bright });
    Object.assign(o[5], { FC: 1, OL: bright });
    return v;
  }
  // sweep: a real glide inside one note. On the FM-1 the pitch EG moves ratio-mode operators but NOT fixed-frequency
  // ones (test_peg_sweep.py), so: op4 = F2 in ratio mode, swept by the pitch EG from F2 start to F2 end; the f0
  // modulator op6 and op2 are FIXED at the voice pitch (the pitch stays put); op5 = F3 fixed; F1 by staggered onset:
  // op3 = F1 start fades, op1 = F1 end comes in late. The sweep is two-stage: a slow drift through the vowel's onset
  // part, then the main glide. PEG speed on the FM-1 (1/32-octave steps per ms at PR 40..85, step 5):
  const PEG_SPEED = [0.041, 0.051, 0.062, 0.077, 0.089, 0.105, 0.12, 0.147, 0.185, 0.218];
  const pegRate = perMs => {
    if (perMs <= 0) return 99;
    const t = PEG_SPEED, x = Math.log(perMs);
    if (perMs <= t[0]) return Math.max(1, Math.round(40 + (x - Math.log(t[0])) / Math.log(t[1] / t[0]) * 5));
    if (perMs >= t[t.length - 1]) return Math.min(99, Math.round(85 + (x - Math.log(t[9])) / Math.log(t[9] / t[8]) * 5));
    let k = 0; while (t[k + 1] < perMs) k++;
    return Math.round(40 + 5 * (k + (x - Math.log(t[k])) / Math.log(t[k + 1] / t[k])));
  };
  const ratioOf = (o, r) => {                          // ratio-mode frequency, ~1 % resolution
    r = Math.max(0.5, Math.min(31.9, r));
    if (r < 1) Object.assign(o, { MODE: 0, FC: 0, FF: Math.min(99, Math.round((r / 0.5 - 1) * 100)) });
    else { const c = Math.floor(r); Object.assign(o, { MODE: 0, FC: c, FF: Math.min(99, Math.round((r / c - 1) * 100)) }); }
  };
  const fixedHz = o => Math.pow(10, (o.FC & 3) + o.FF / 100);
  function diphSweep(keyHz, f0, A, B, body, bright, fallR, riseR, D, onset = 0.35, glideEnd = 0.8, f1 = true) {
    const v = baseVoice('SPEECH W'), o = v.ops;
    const steps = Math.max(-23, Math.min(35, Math.round(32 * Math.log2(B.F[1] / A.F[1]))));
    ratioOf(o[3], A.F[1] / keyHz); o[3].OL = A.lv[1];
    const s1 = Math.round(steps * 0.15), t1 = Math.max(10, D * onset), t2 = Math.max(10, D * (glideEnd - onset));
    Object.assign(v.g, { PL4: 50, PR1: pegRate(Math.abs(s1) / t1), PL1: 50 + s1, PR2: pegRate(Math.abs(steps - s1) / t2), PL2: 50 + steps,
      PR3: 99, PL3: 50 + steps, PR4: 99 });
    fixedFreq(o[5], f0); o[5].OL = bright;
    fixedFreq(o[1], f0); o[1].OL = bright;
    fixedFreq(o[4], Math.sqrt(A.F[2] * B.F[2])); o[4].OL = Math.round((A.lv[2] + B.lv[2]) / 2);
    const f1moves = f1 && Math.abs(Math.log(B.F[0] / A.F[0])) > 0.18;
    fixedFreq(o[2], A.F[0]); o[2].OL = A.lv[0];
    if (f1moves) { fade(o[2], fallR); fixedFreq(o[0], B.F[0]); o[0].OL = B.lv[0]; grow(o[0], riseR); }
    else { fixedFreq(o[0], f0); o[0].OL = body; o[1].OL = 40; }   // F1 holds: op1 is the body (fundamental) again
    return v;
  }
  // pair: two full vowel voices on two overlapping notes started together (octave keys): the start vowel's carriers
  // fade out, the end vowel's carriers come in late. Keeps F3 and the body; uses two of the FM-1's voices.
  function diphPair(makeVoiced, f0, A, B, body, bright, fallR, riseR) {
    const a = makeVoiced(f0, A.F, A.lv, body, bright), b = makeVoiced(f0, B.F, B.lv, body, bright);
    for (const k of [0, 2, 3, 4]) { fade(a.ops[k], fallR); grow(b.ops[k], riseR); }
    return [a, b];
  }

  // Fricative as a noise band (FINDINGS.md "Consonants"): the FM-1's feedback is only 1/4 of the DX7's, so the old
  // design's feedback "noise" was really a comb on the key's harmonics - metallic. Here the carriers sit at the exact
  // measured centre (fixed frequency) and are driven by a cascade of INHARMONIC fixed-frequency modulators at high
  // index, so the sidebands are dense and irregular instead of spaced by the pitch. DX7 algorithm 1: 6>5>4>3 (fb 6)
  // and 2>1; carriers op3 (main band) and op1 (upper band).
  function fricNoise(centre, { depth = 88, upper = -8, spread = 1 } = {}) {
    const v = baseVoice('SPEECH N'), o = v.ops;
    v.g.ALG = 0; v.g.FB = 7;
    fixedFreq(o[2], centre); o[2].OL = 92;                         // op3: main band
    fixedFreq(o[3], centre * 0.371 * spread); o[3].OL = depth;              // op4 -> op3
    fixedFreq(o[4], centre * 0.613 * spread); o[4].OL = depth - 4;          // op5 -> op4
    fixedFreq(o[5], centre * 1.137 * spread); o[5].OL = depth;              // op6 -> op5, with feedback
    fixedFreq(o[0], centre * 1.43); o[0].OL = lvAdd(92, upper);    // op1: upper band
    fixedFreq(o[1], centre * 0.529 * spread); o[1].OL = depth;              // op2 -> op1
    return v;
  }
  // Fricative as a band-limited hiss: carriers spread across the band (fixed frequencies around the measured centre),
  // each lightly modulated by LOW inharmonic modulators, so the sidebands stay inside the band (a real "s" is ~35 dB
  // down below 2 kHz) but are not spaced by the pitch. DX7 algorithm 22: 6 -> 3,4,5 (fb 6), 2 -> 1.
  // Aspiration (h, the breath after p/t/k): noise shaped by the vowel's formants - fixed-frequency carriers at the
  // frame's F1-F3 driven by a low inharmonic modulator with feedback (algorithm 22), no voiced body.
  function aspirate(F, levels, { depth = 80 } = {}) {
    const v = baseVoice('SPEECH H'), o = v.ops;
    v.g.ALG = 21; v.g.FB = 7;
    [2, 3, 4].forEach((k, i) => { fixedFreq(o[k], F[i]); o[k].OL = levels[i]; });
    fixedFreq(o[5], 137); o[5].OL = depth;                 // inharmonic: sidebands at F +- k*137 Hz, not the pitch's comb
    return v;
  }
  // Voiced fricative (z, zh, jh frames with a strong high band): a voiced bar at F1 and the fundamental, plus a hiss
  // band at the measured centre. Algorithm 22: op1 body (f0), op3 = F1, op4/op5 = hiss band, all driven by op6.
  function voicedFric(f0, F, levels, body, centre, { depth = 76 } = {}) {
    const v = baseVoice('SPEECH Z'), o = v.ops;
    v.g.ALG = 21; v.g.FB = 7;
    Object.assign(o[0], { FC: 1, OL: body });
    fixedFreq(o[2], F[0]); o[2].OL = levels[0];
    fixedFreq(o[3], centre); o[3].OL = lvAdd(levels[0], -6);
    fixedFreq(o[4], centre * 1.28); o[4].OL = lvAdd(levels[0], -9);
    fixedFreq(o[5], centre * 0.093); o[5].OL = depth;
    return v;
  }
  function fricBand(centre, { depth = 78, width = 1 } = {}) {
    const v = baseVoice('SPEECH B'), o = v.ops;
    v.g.ALG = 21; v.g.FB = 7;
    const w = k => 1 + (k - 1) * width;
    fixedFreq(o[2], centre * w(0.8)); o[2].OL = 86;
    fixedFreq(o[3], centre); o[3].OL = 92;
    fixedFreq(o[4], centre * w(1.28)); o[4].OL = 87;
    fixedFreq(o[5], centre * 0.093); o[5].OL = depth;       // shared modulator, inharmonic, with feedback
    fixedFreq(o[0], centre * w(1.62)); o[0].OL = 80;        // top of the band
    fixedFreq(o[1], centre * 0.137); o[1].OL = depth - 4;
    return v;
  }

  // frames -> notes [{v, key, start, dur}] (one per hop)
  function plan(frames, { emax = 0, hop = 20, silenceDb = -42, base = 45, bright = 54, slope = 0.8, release = 85, design = 'fixedfm', vib = 0,
    fric = 'band', fricDepth = 84, fricUpper = -8, fricSpread = 1, hfVoiced = -16, hfAspirate = -20, burst = true, burstDecay = 72, hfVowel = -20, vowelDb = -18,
    formant = 1, whisper = false, stutter = 0, inharm = 0,
    glide = 0, fine = false, attack = 0, overlap = 8, releaseRate = 0,
    softStart = 0, softRise = 70, softVoiced = true,
    diph = 'off', diphAll = false, diphFall = 1, diphRise = 0.55, diphEdge = 0.25, diphOnset = 0.35, diphGlide = 0.8 } = {}) {
    const fs = f => Math.min(5000, f * formant), fsc = c => c * (1 + (formant - 1) * 0.5);
    const makeVoiced = DESIGNS[design] || voiced;
    const step = Math.round(hop / HOP), notes = [];
    let lastKey = base, quietBefore = true, lastPitch = null, prevVoiced = false;
    // partial-depth soft start: jump to -softStart dB, then ease up to full at softRise (works on the FM-1:
    // test_eg_softstart.py). Only operators with the plain envelope; with softVoiced only voiced -> voiced joins.
    const soften = (v, isV) => {
      if (!softStart || (softVoiced && !(isV && prevVoiced))) return;
      const l1 = Math.max(0, 99 - Math.round(softStart / 0.75));
      for (const o of v.ops) if (o.L1 === 99 && o.L2 === 99 && o.L3 === 99) Object.assign(o, { R1: 99, L1: l1, R2: softRise });
    };
    // DX7 pitch-EG level for an offset in semitones (1/32 octave per step around 50; linear for levels 27-85)
    const pegLevel = st => Math.max(27, Math.min(85, 50 + Math.round(st * 32 / 12)));
    for (let n = 0; n + step <= frames.length; n += step) {
      // a diphthong (or, with diphAll, any vowel) as one long note whose formants move inside the voice
      if (diph !== 'off' && !whisper && frames[n].p && (diphAll ? VOWEL : DIPH).test(frames[n].p)) {
        let end = n; while (end < frames.length && frames[end].i === frames[n].i) end++;
        const run = frames.slice(n, end), vr = run.filter(f => f.v), e = Math.max(...run.map(f => f.e));
        if (vr.length >= 6 && vr.length * 4 >= run.length * 3 && e >= emax + silenceDb) {
          const k = Math.max(2, Math.round(vr.length * diphEdge));
          const med = (fr, fn) => { const s = fr.map(fn).sort((a, b) => a - b); return s.length % 2 ? s[s.length >> 1] : (s[s.length / 2 - 1] + s[s.length / 2]) / 2; };
          const gain = Math.round((e - emax) / 0.75 * 0.7);
          const end3 = fr => {
            const F = [0, 1, 2].map(i => fs(med(fr, f => f.F[i]))), L = [0, 1, 2].map(i => med(fr, f => f.L[i])), top = Math.max(...L);
            return { F, lv: L.map(l => Math.max(20, Math.min(99, Math.trunc(99 + (l - top) / 0.75 * slope + gain)))) };
          };
          const A = end3(vr.slice(0, k)), B = end3(vr.slice(-k));
          const semi = run.reduce((s2, f) => s2 + (f.semi || 0), 0) / run.length, pitch = Math.max(30, Math.min(72, base + semi));
          const key = Math.max(30, Math.min(72, base + Math.round(semi))); lastKey = key;
          const D = run.length * HOP, body = Math.max(0, Math.min(99, 70 + gain));
          const fallR = rateFor(FALL20, 33, D * diphFall), riseR = rateFor(RISE6, 22, D * diphRise);
          let notePitch = fine ? pitch : key;
          const vs = diph === 'pair' ? diphPair(makeVoiced, midiHz(key), A, B, body, bright, fallR, riseR)
            : diph === 'sweep' ? [diphSweep(midiHz(key), midiHz(pitch), A, B, body, bright, fallR, riseR, D, diphOnset, diphGlide)]
            : [diphSingle(midiHz(key), A, B, body, bright, fallR, riseR)];
          if (diph === 'sweep') notePitch = 69 + 12 * Math.log2(fixedHz(vs[0].ops[5]) / 440);   // the fixed f0 sets the pitch
          for (const v of vs) {
            v.ops.forEach(o => o.R4 = releaseRate || release);
            soften(v, true);
            if ((fine || glide) && diph !== 'sweep') {
              const hold = fine ? pegLevel(pitch - key) : 50, from = glide && lastPitch != null ? pegLevel(lastPitch - key) : hold;
              Object.assign(v.g, { PL4: from, PL1: hold, PL2: hold, PL3: hold, PR1: glide || 99, PR2: 99, PR3: 99, PR4: 0 });
            }
            notes.push({ v, key, start: n * HOP, dur: D + overlap, pitch: notePitch, diph: true });
          }
          lastPitch = pitch; quietBefore = false; prevVoiced = true;
          n = n + Math.ceil((end - n) / step) * step - step;
          continue;
        }
      }
      const g = frames.slice(n, n + step);
      const e = Math.max(...g.map(f => f.e));
      if (e < emax + silenceDb) { quietBefore = true; lastPitch = null; prevVoiced = false; continue; }
      const afterQuiet = quietBefore; quietBefore = e < emax - 30;
      const vo = whisper ? [] : g.filter(f => f.v || (fric === 'band' && (f.hf ?? -30) < hfVowel && f.e > emax + vowelDb));
      const gain = Math.round((e - emax) / 0.75 * 0.7);
      const semi = g.reduce((s, f) => s + (f.semi || 0), 0) / g.length;
      let key, v;
      let pitch = base + semi;
      if (vo.length * 2 >= g.length) {
        key = Math.max(30, Math.min(72, base + Math.round(semi))); lastKey = key;
        const med = k => { const s = vo.map(k).sort((a, b) => a - b); return s.length % 2 ? s[s.length >> 1] : (s[s.length / 2 - 1] + s[s.length / 2]) / 2; };
        const F = [0, 1, 2].map(i => fs(med(f => f.F[i]))), L = [0, 1, 2].map(i => med(f => f.L[i]));
        const top = Math.max(...L);
        const lv = L.map(l => Math.max(20, Math.min(99, Math.trunc(99 + (l - top) / 0.75 * slope + gain))));
        const hfv = med(f => f.hf ?? -30);
        v = fric === 'band' && hfv > hfVoiced
          ? voicedFric(midiHz(key), F, lv, Math.max(0, Math.min(99, 70 + gain)), Math.max(1200, Math.min(7000, fsc(med(f => f.cent)))))
          : makeVoiced(midiHz(key), F, lv, Math.max(0, Math.min(99, 70 + gain)), bright);
        if (inharm) v.ops[5].FF = inharm;                // modulator off the harmonic grid: a bell / cyborg colour
        if (vib) Object.assign(v.g, { LPMD: vib, LPMS: 3, LFS: 40, LFW: 4 });   // Chowning: pitch wobble fuses the voice
      } else {
        const c = g.map(f => f.cent).sort((a, b) => a - b)[g.length >> 1];
        key = lastKey;
        const hfu = g.map(f => f.hf ?? -30).sort((a, b) => a - b)[g.length >> 1];
        if (fric === 'band' && hfu < hfAspirate) {
          // aspiration: noise on this frame's own formants
          const mid = k => g.map(k).sort((a, b) => a - b)[g.length >> 1];
          const F = [0, 1, 2].map(i => fs(mid(f => f.F[i]))), L = [0, 1, 2].map(i => mid(f => f.L[i])), top = Math.max(...L);
          v = aspirate(F, L.map(l => Math.max(20, Math.min(99, Math.trunc(92 + (l - top) / 0.75 * slope + gain)))), { depth: fricDepth });
        } else if (fric === 'band') {
          v = fricBand(Math.max(1200, Math.min(7000, fsc(c))), { depth: fricDepth, width: fricSpread });
          for (const i of [0, 2, 3, 4]) v.ops[i].OL = Math.max(0, Math.min(99, v.ops[i].OL + gain));
        } else if (fric === 'noise') {
          v = fricNoise(Math.max(1200, Math.min(7000, c)), { depth: fricDepth, upper: fricUpper, spread: fricSpread });
          for (const i of [0, 2]) v.ops[i].OL = Math.max(0, Math.min(99, v.ops[i].OL + gain));
        } else {
          v = fricative(midiHz(key), Math.max(1500, Math.min(3400, c)), false);
          for (const i of [2, 3, 4]) v.ops[i].OL = Math.max(0, Math.min(99, v.ops[i].OL + gain));
        }
      }
      v.ops.forEach(o => o.R4 = releaseRate || release);
      if (attack) v.ops.forEach(o => { o.R1 = Math.min(o.R1, attack); });
      if (fine || glide) {
        const isV = vo.length * 2 >= g.length, p = isV ? Math.max(30, Math.min(72, pitch)) : key;
        const hold = fine ? pegLevel(p - key) : 50;
        const from = glide && lastPitch != null ? pegLevel(lastPitch - key) : hold;
        Object.assign(v.g, { PL4: from, PL1: hold, PL2: hold, PL3: hold, PR1: glide || 99, PR2: 99, PR3: 99, PR4: 0 });
        if (isV) lastPitch = p;
      }
      const notePitch = vo.length * 2 >= g.length ? (fine ? Math.max(30, Math.min(72, pitch)) : key) : null;
      // Stop release (unvoiced frame straight after silence / a closure): a percussive burst - instant attack, then a
      // fast decay to nothing - instead of a held hiss ("tip" was heard as "ship").
      if (burst && fric === 'band' && !(vo.length * 2 >= g.length) && afterQuiet)
        v.ops.forEach(o => Object.assign(o, { R1: 99, L1: 99, R2: burstDecay, L2: 0, R3: 99, L3: 0 }));
      soften(v, notePitch != null);
      prevVoiced = notePitch != null;
      notes.push({ v, key, start: n * HOP, dur: stutter ? hop * stutter : hop + overlap, pitch: notePitch });
    }
    return notes;
  }

  // notes -> timed MIDI [{t (ms), b (bytes)}]: param diffs right after the previous note-on, alternating octave keys.
  // sync: start each voiced note a whole number of pitch periods after the previous voiced note, so the new note's
  // oscillators (reset to phase 0 at note-on) line up with the one it overlaps instead of cancelling it - the 50 Hz
  // "gravel" of a chain of 20 ms notes. The octave-alternating key keeps the same frequencies, so it stays aligned.
  function toEvents(notes, { ch = 0, lead = 40, msPerParam = 0.17, fx = null, fxCh = 1, sync = true } = {}) {
    const ev = [], dev = { buf: null };
    if (fx && fx.length) fx.forEach(([c, val]) => ev.push({ t: 0, b: [0xB0 | fxCh, c, val] }));
    let flip = 0, lastOn = -1e9, lastVoicedOn = null, lastPeriod = 0;
    for (const nt of notes) {
      flip ^= 1;
      const key = nt.key + 12 * flip, v = flip ? octaveUp(nt.v) : nt.v;
      const want = DX7.toVced(v); want[144] = 24; want.push(63);
      const start = nt.start + lead;
      const at = Math.max(lastOn + 1, start - 15);
      let k = 0;
      for (let p = 0; p <= 155; p++) {
        if (p > 144 && p < 155) continue;
        if (dev.buf && dev.buf[p] === want[p]) continue;
        ev.push({ t: at + k * msPerParam, b: DX7.paramSysex(p, want[p], ch) }); k++;
      }
      dev.buf = want;
      let on = Math.max(start, at + k * msPerParam + 0.5);
      if (sync && nt.pitch != null && lastVoicedOn != null && lastPeriod > 0) {
        const earliest = at + k * msPerParam + 0.5;
        let m = Math.round((on - lastVoicedOn) / lastPeriod);
        let t = lastVoicedOn + m * lastPeriod;
        while (t < earliest) t += lastPeriod;
        on = t;
      }
      if (nt.pitch != null) { lastVoicedOn = on; lastPeriod = 1000 / midiHz(nt.pitch); } else lastVoicedOn = null;
      ev.push({ t: on, b: [0x90 | ch, key, 110] });
      ev.push({ t: on + nt.dur, b: [0x80 | ch, key, 0] });
      lastOn = on;
    }
    if (fx && fx.length && ev.length) {
      const end = Math.max(...ev.map(e => e.t)) + 400;                // after the tail (reverb, delay)
      fx.filter(([c]) => c % 4 === 0).forEach(([c]) => ev.push({ t: end, b: [0xB0 | fxCh, c, 0] }));
    }
    return ev.sort((a, b) => a.t - b.t);
  }

  // ---- characters: voice presets on top of the user's settings ----
  // keyShift / speedMul are relative to the Pitch / Speed sliders; the rest override. Effects use the FM-1's own
  // effect section (CC 0-23 on the FX channel) and are switched off again at the end.
  const CHARACTERS = {
    natural: { label: 'Natural' },
    feminine: { label: 'Feminine', keyShift: 10, formant: 1.17, bright: 50 },
    child: { label: 'Child', keyShift: 15, formant: 1.3, speedMul: 1.08 },
    giant: { label: 'Giant', keyShift: -10, formant: 0.82, speedMul: 0.82 },
    whisper: { label: 'Whisper', whisper: true },
    robot: { label: 'Robot', tone: 'flat', design: 'harmonic', bright: 66, hop: 30, release: 99 },
    stutter: { label: 'Glitch robot', tone: 'flat', design: 'harmonic', bright: 66, hop: 40, stutter: 0.5 },
    cyborg: { label: 'Cyborg (bell)', inharm: 41, bright: 62 },
    alien: { label: 'Alien', keyShift: -6, formant: 1.35, vib: 45, bright: 60 },
    singer: { label: 'Singer', tone: 'sing', melody: [0, 4, 7, 12, 7, 4, 2, 0], vowelStretch: 1.8, speedMul: 0.8, vib: 10 },
    radio: { label: 'Old radio', fx: [[0, 1], [1, 1], [2, 55], [3, 4], [12, 1], [13, 30], [14, 40], [15, 50]] },
    cathedral: { label: 'Cathedral', speedMul: 0.9, fx: [[4, 1], [5, 1], [6, 90], [7, 50]] },
    choir: { label: 'Chorus', vib: 8, fx: [[16, 1], [17, 30], [18, 80], [19, 80], [4, 1], [5, 1], [6, 70], [7, 35]] },
    echo: { label: 'Echo', fx: [[8, 1], [9, 40], [10, 35], [11, 45]] },
    uptalk: { label: 'Uptalk (intonation)', style: 'uptalk' },
    singsong: { label: 'Sing-song (intonation)', style: 'singsong', accent: 5 },
    drawl: { label: 'Drawl (intonation)', speedMul: 0.85, vowelStretch: 1.5, accent: 2 },
    excited: { label: 'Excited', accent: 6, keyShift: 3, speedMul: 1.12 },
  };
  // Smoothness (FINDINGS.md "Gravel"): sync = phase-aligned note onsets (default); smooth = + crossfaded frames and a
  // continuous pitch (fine pitch + glide through the pitch envelope); off = the plain 20 ms note chain.
  const SMOOTHING = {
    // soft (default): phase-aligned onsets + a crossfade at voiced -> voiced joins only (attack 80 from silence via the
    // soft-start path, 20 ms overlap, release 75); consonant onsets stay sharp. FM-1: 50 Hz line 6-7.6 -> 2.7-3.4 dB,
    // >2.5 kHz clicks 6-7 -> 3.6-4.4 dB, loudness +0.5 dB, words within noise (FINDINGS.md "Soft joins").
    soft: { sync: true, softStart: 99, softRise: 80, overlap: 20, releaseRate: 75 },
    sync: { sync: true },
    smooth: { sync: true, fine: true, glide: 99, attack: 80, overlap: 20, releaseRate: 75 },
    off: { sync: false },
  };
  function character(name, o = {}) {
    const c = CHARACTERS[name] || {};
    const { label, keyShift = 0, speedMul = 1, ...rest } = c;
    return { ...SMOOTHING[o.smooth || 'soft'], ...o, ...rest, base: (o.base ?? 45) + keyShift, speed: (o.speed ?? 1) * speedMul };
  }

  return { load, setData, ready, lint, prosody, framesFromSeq, speak, analyse, framesFromRecording, plan, toEvents, octaveUp,
    voiced, fricative, diphSingle, diphPair, diphSweep, pegRate, numWords, guess, lookup: w => lookup(w), HOP, CHARACTERS, SMOOTHING, character };
})();
