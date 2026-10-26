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
for (const name of ['eyebrow', 'title', 'meta', 'rate', 'trials', 'configurations', 'scope',
  'unavailable', 'outcomes', 'history', 'close', 'rate-label', 'trials-label', 'configurations-label',
  'selected-run', 'attempts', 'model-name', 'model-id', 'coverage', 'evidence', 'source',
  'comparison', 'comparison-title']) ids['scenario-dialog-' + name] = element();
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
// Exact model/release selection makes attempt evidence primary.
dialog.close();
const run = {passed: 2, trials: 3, recorded_attempts: 3, cost_reported: 2,
  median_duration_seconds: 53, known_cost_usd: 0.0123, cost_lower_bound: true,
  attempts: [{attempt: 1, passed: false, status: 'evaluated',
    failure: '<unsafe> repair did not survive host reboot', failure_category: 'database_pool_exhausted',
    duration_seconds: 53, timeout_seconds: 900, model_rounds: 14, tool_calls: 19,
    verification: {immediate_http: true, service_restart: true, host_reboot: false},
    usage: {cost_usd: 0, input_tokens: 100, output_tokens: 20, cache_read_tokens: 0, cost_source: 'provider'}}]};
ids['score-scenario-data'].textContent = JSON.stringify({groups: data, runs: {[outcome.evidence_url]: run}});
context.initializeScenarioDialog(selector, harness);
selector.value = 'one';
trigger.dataset.version = '1';
click();
assert.equal(ids['scenario-dialog-rate'].textContent, '2/3');
assert.equal(ids['scenario-dialog-trials'].textContent, '53s');
assert.equal(ids['scenario-dialog-configurations'].textContent, '≥$0.0123');
assert.equal(ids['scenario-dialog-model-name'].textContent, outcome.label);
assert.equal(ids['scenario-dialog-evidence'].href, outcome.evidence_url);
assert.equal(ids['scenario-dialog-selected-run'].hidden, false);
assert.equal(ids['scenario-dialog-comparison'].open, false);
const attempt = ids['scenario-dialog-attempts'].children[0];
assert.equal(attempt.children[0].children[1].textContent, 'Failed');
assert.ok(attempt.children[1].textContent.includes('$0.0000 reported cost'));
assert.deepEqual(attempt.children[2].children.map(e => e.textContent),
  ['Immediate: passed', 'Restart: passed', 'Reboot: not confirmed']);
assert.equal(attempt.children[3].textContent, '<unsafe> repair did not survive host reboot');
assert.equal(attempt.children[4].textContent, 'database_pool_exhausted');
assert.ok(attempt.children[5].children[1].textContent.includes('19 recorded tool calls'));
// Selecting a different configuration inside the modal preserves the original opener.
const comparisonLink = ids['scenario-dialog-outcomes'].children[1];
comparisonLink.closest = () => comparisonLink;
comparisonLink.attrs.href = comparisonLink.href;
click({target: comparisonLink});
assert.equal(ids['scenario-dialog-selected-run'].hidden, true); // no attempt payload for this entry
assert.equal(ids['scenario-dialog-comparison'].open, true);
dialog.close();
assert.equal(comparisonLink.focused, undefined);
assert.equal(trigger.focused, true);
console.log('Scenario overlay checks passed');
