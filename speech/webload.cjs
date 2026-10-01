// Load the web app's browser scripts (dx7.js, fm1-synth.js, speech.js) into one Node context, as the page does.
const fs = require('fs'), vm = require('vm'), path = require('path');
module.exports = function load(files = ['dx7.js', 'fm1-synth.js']) {
  const ctx = { console, Math, performance: { now: () => Date.now() } };
  vm.createContext(ctx);
  for (const f of files) vm.runInContext(fs.readFileSync(path.join(__dirname, '..', 'app', f), 'utf8'), ctx, { filename: f });
  vm.runInContext('this.DX7 = typeof DX7 !== "undefined" ? DX7 : null; this.FM1Core = typeof FM1Core !== "undefined" ? FM1Core : null; this.FM1T = typeof FM1T !== "undefined" ? FM1T : null;' +
    'this.Speech = typeof Speech !== "undefined" ? Speech : null;', ctx);
  return ctx;
};
