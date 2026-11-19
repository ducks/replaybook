const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('site/templates/benchmark-scores.html', 'utf8');
const fn = source.slice(source.indexOf('  function showCohort()'), source.indexOf('  function changed()'));
function card(model, release, harness = 'claux') {
  return {dataset: {model, release, harness}, hidden: false};
}
function section(suite, condition, cards) {
  return {dataset: {suite, condition}, hidden: false,
    querySelector: () => null,
    querySelectorAll: selector => selector === '.score-card' ? cards : []};
}
const old = card('same-model', '20261117.0.0');
const latest = card('same-model', '20261118.0.0', 'codex');
const other = card('other-model', '20261101.0.0');
const hard = card('same-model', '20261119.0.0');
const sections = [section('core', 'old', [old, other]), section('core', 'new', [latest]), section('hard', 'hard', [hard])];
const selector = {value: 'all-core'}, harness = {value: ''};
const context = vm.createContext({selector, harness, compareCards: () => 0,
 document: {querySelectorAll: () => sections}});
vm.runInContext(fn, context);
context.showCohort();
assert.equal(old.hidden, true);
assert.equal(latest.hidden, false);
assert.equal(other.hidden, false);
assert.equal(sections[2].hidden, true);
harness.value = 'claux'; context.showCohort();
assert.equal(old.hidden, false); // latest within the selected harness
assert.equal(latest.hidden, true);
selector.value = 'old'; harness.value = ''; context.showCohort();
assert.equal(old.hidden, false); // explicit historical conditions remain available
assert.equal(sections[1].hidden, true);
console.log('Latest-per-model score selection passed');
