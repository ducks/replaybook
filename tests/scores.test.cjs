const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const template = fs.readFileSync('site/templates/benchmark-scores.html', 'utf8');
const dialogScript = fs.readFileSync('site/static/score-scenarios.js', 'utf8');
const script = dialogScript + '\n' + template.match(/<script>([\s\S]*?)<\/script>/)[1];
function select(values) {
  return {value: values[0], options: values.map(value => ({value})), listeners: {},
    add(option) { this.options.push(option); },
    addEventListener(event, callback) { this.listeners[event] = callback; },
    change(value) { this.value = value; this.listeners.change(); }};
}
function card(name, score, cost, complete = true, harness = 'a') {
  return {dataset: {name, score: String(score), cost: String(cost),
    costPerRepair: String(cost), costComplete: String(complete), duration: '',
    harness, harnessLabel: harness}};
}
function run(search = '') {
  const controls = {'score-cohort': select(['one', 'two']), 'score-harness': select(['']),
    'score-sort': select(['score', 'cost', 'costPerRepair', 'duration', 'name']),
    'score-direction': select(['desc', 'asc'])};
  const cards = [card('Alpha', 20, 2), card('Beta', 90, 8),
    card('Partial', 50, 1, false), card('Unknown', 0, '', false, 'b')];
  const grid = {children: [...cards], append(card) {
    this.children.splice(this.children.indexOf(card), 1); this.children.push(card);
  }};
  const provider = {querySelectorAll: () => cards};
  const profiles = ['', 'a', 'b'].map(harness => ({dataset: {harness}, hidden: Boolean(harness)}));
  const emptyProfile = {hidden: true};
  const section = {dataset: {condition: 'one', releases: 'old'}, querySelectorAll(selector) {
    return selector === '.score-card' ? cards : selector === '.score-grid' ? [grid] :
      selector === '.score-provider' ? [provider] : selector === '.scenario-profile' ? profiles : [];
  }, querySelector: () => emptyProfile};
  const other = {dataset: {condition: 'two', releases: 'new'}, querySelectorAll: () => [], querySelector: () => null};
  let url;
  vm.runInNewContext(script, {URLSearchParams, location: {search},
    history: {replaceState: (_, __, value) => { url = value; }},
    Option: function(text, value) { this.value = value; },
    document: {getElementById: id => controls[id],
      querySelectorAll: selector => selector === '.score-card' ? cards : [section, other]}});
  return {controls, cards, section, other, profiles, emptyProfile, names: () => grid.children.map(c => c.dataset.name), url: () => url};
}
let page = run();
assert.deepEqual(page.names(), ['Beta', 'Partial', 'Alpha', 'Unknown']);
assert.equal(page.other.hidden, true);
assert.deepEqual(page.profiles.map(profile => profile.hidden), [false, true, true]);
page.controls['score-sort'].change('cost');
assert.deepEqual(page.names(), ['Alpha', 'Beta', 'Partial', 'Unknown']);
page.controls['score-direction'].change('desc');
assert.deepEqual(page.names(), ['Beta', 'Alpha', 'Partial', 'Unknown']);
assert.equal(new URLSearchParams(page.url().slice(1)).get('direction'), 'desc');
page.controls['score-harness'].change('a');
assert.equal(page.cards[3].hidden, true);
assert.deepEqual(page.profiles.map(profile => profile.hidden), [true, false, true]);
page.controls['score-sort'].change('name');
assert.deepEqual(page.names(), ['Alpha', 'Beta', 'Partial', 'Unknown']);
page = run('?sort=costPerRepair&direction=asc&harness=b&release=old');
assert.deepEqual(page.names(), ['Alpha', 'Beta', 'Partial', 'Unknown']);
assert.equal(page.cards[0].hidden, true);
assert.equal(page.cards[3].hidden, false);
assert.deepEqual(page.profiles.map(profile => profile.hidden), [true, true, false]);
page.controls['score-harness'].change('missing');
assert.equal(page.emptyProfile.hidden, false);
assert.ok(page.profiles.every(profile => profile.hidden));
page = run('?sort=duration&direction=desc');
assert.deepEqual(page.names(), ['Alpha', 'Beta', 'Partial', 'Unknown']);
page = run('?sort=invalid&direction=invalid');
assert.deepEqual(page.names(), ['Beta', 'Partial', 'Alpha', 'Unknown']);
console.log('Score sorting checks passed');
