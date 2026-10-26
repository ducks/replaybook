const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('site/static/score-scenarios.js', 'utf8');

function element() {
  return {children: [], attrs: {}, listeners: {}, dataset: {}, textContent: '',
    append(...children) { this.children.push(...children); },
    replaceChildren(...children) { this.children = children; },
    setAttribute(name, value) { this.attrs[name] = value; },
    getAttribute(name) { return this.attrs[name]; },
    addEventListener(name, listener) { this.listeners[name] = listener; },
    focus() { this.focused = true; }};
}
const outcome = {label: '<img src=x onerror=alert(1)>', provider: 'Provider A', harness: 'Claux',
  reasoning: 'high', release: 'first', passed: 2, trials: 3, unavailable: 0,
  outcome: 'mixed', evidence_url: 'benchmark-evidence.html?release=first&scenario=a'};
const row = {scenario: 'a', version: 1, label: 'A scenario', failure_rate: 100 / 3,
  unsuccessful: 2, trials: 6, configurations: 2, unavailable: 1,
  outcomes: [outcome, {...outcome, label: 'Other configuration', release: 'second',
    evidence_url: 'benchmark-evidence.html?release=second&scenario=a'}]};
const other = {...row, configurations: 1, failure_rate: 100, outcomes: [row.outcomes[1]]};
const data = [{condition: 'one', benchmark: '1.0', profiles: [
  {harness: '', rates: [row]}, {harness: 'b', rates: [other]}]},
  {condition: 'two', profiles: [{harness: '', rates: [{...row, version: 2}]}]}];
const ids = {};
for (const name of ['title', 'meta', 'rate', 'trials', 'configurations', 'scope',
  'unavailable', 'outcomes', 'history', 'close']) ids['scenario-dialog-' + name] = element();
const classes = new Set();
const dialog = element();
dialog.showModal = () => { dialog.open = true; };
dialog.close = () => { dialog.open = false; dialog.listeners.close(); };
dialog.getBoundingClientRect = () => ({left: 10, right: 100, top: 10, bottom: 100});
ids['score-scenario-dialog'] = dialog;
ids['score-scenario-data'] = {textContent: JSON.stringify(data)};
const document = {getElementById: id => ids[id], createElement: element, listeners: {},
  addEventListener(name, listener) { this.listeners[name] = listener; },
  documentElement: {classList: {add: name => classes.add(name), remove: name => classes.delete(name)}}};
const context = vm.createContext({document, URLSearchParams});
vm.runInContext(source, context);
const selector = {value: 'one'};
const harness = {value: '', selectedOptions: [{textContent: 'Harness B'}]};
context.initializeScenarioDialog(selector, harness);
const trigger = element();
trigger.dataset = {scenario: 'a', version: '1'};
trigger.attrs.href = outcome.evidence_url;
trigger.closest = () => trigger;
function click(extra = {}) {
  const event = {target: trigger, button: 0, prevented: false,
    preventDefault() { this.prevented = true; }, ...extra};
  document.listeners.click(event);
  return event;
}
assert.equal(click().prevented, true);
assert.equal(dialog.open, true);
assert.equal(ids['scenario-dialog-rate'].textContent, '33.3%');
assert.equal(ids['scenario-dialog-trials'].textContent, '2 / 6');
assert.equal(ids['scenario-dialog-outcomes'].children.length, 2);
const selected = ids['scenario-dialog-outcomes'].children[0];
assert.ok(selected.className.includes('is-selected'));
assert.equal(selected.children[0].children[0].textContent, outcome.label);
assert.equal(selected.href, outcome.evidence_url);
assert.ok(selected.attrs['aria-label'].includes('Provider A'));
assert.ok(classes.has('scenario-dialog-open'));
ids['scenario-dialog-close'].listeners.click();
assert.equal(dialog.open, false);
assert.equal(trigger.focused, true);
assert.equal(classes.has('scenario-dialog-open'), false);
assert.equal(click({ctrlKey: true}).prevented, false);
assert.equal(dialog.open, false);
harness.value = 'b';
click();
assert.equal(ids['scenario-dialog-rate'].textContent, '100.0%');
assert.equal(ids['scenario-dialog-outcomes'].children.length, 1);
// Backdrop clicks close; clicks inside the dialog do not.
dialog.listeners.click({target: dialog, clientX: 50, clientY: 50});
assert.equal(dialog.open, true);
dialog.listeners.click({target: dialog, clientX: 0, clientY: 0});
assert.equal(dialog.open, false);
harness.value = 'unknown';
assert.equal(click().prevented, false);
harness.value = '';
selector.value = 'two';
assert.equal(click().prevented, false); // scenario v1 never opens v2 evidence
trigger.dataset.version = '2';
assert.equal(click().prevented, true);
console.log('Scenario overlay checks passed');
