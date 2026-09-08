// Dependency-free DOM shim exercises state and rendering; not a layout test.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const catalog = JSON.parse(fs.readFileSync('benchmark-data/catalog.json', 'utf8'));
const source = fs.readFileSync('site/static/evidence.js', 'utf8');
const template = fs.readFileSync('site/templates/benchmark-evidence.html', 'utf8');

function boot(search = '', data = catalog) {
  const elements = new Map([...template.matchAll(/id="([^"]+)"/g)].map(([, id]) => [id, {
    value: '', textContent: '', innerHTML: '', attributes: {}, listeners: {},
    classList: {toggle() {}},
    setAttribute(name, value) { this.attributes[name] = value; },
    addEventListener(name, fn) { this.listeners[name] = fn; },
    querySelectorAll() { return []; },
    querySelector() { return null; },
  }]));
  elements.get('data').textContent = JSON.stringify(data);
  const context = vm.createContext({
    URLSearchParams, console,
    document: {getElementById(id) {
      assert.ok(elements.has(id), 'Missing element: ' + id);
      return elements.get(id);
    }},
    window: {location: {search}},
    history: {replaceState(_state, _title, url) { context.url = url; }},
  });
  vm.runInContext(source, context);
  return {elements, run: code => vm.runInContext(code, context), context};
}

const app = boot();
assert.ok(app.run('visible.length > 0'));
for (const [passed, color] of [[0, 'red'], [1, 'red'], [2, 'yellow'], [3, 'green']]) {
  assert.equal(app.run('resultClass({passed:' + passed + ', trials:3, evaluated:3, unavailable:0})'), 'result-' + color);
}
assert.equal(app.run('resultClass({passed:2,trials:3,evaluated:2,unavailable:1})'), 'result-incomplete');
assert.equal(app.run('time(119.8)'), '2:00');
assert.equal(app.run('money({cost_reported_trials:0,known_cost_usd:0,trials:3})'), 'Not reported');
assert.ok(!app.elements.get('summary').innerHTML.includes('undefined'));
assert.equal(app.elements.get('cohort-tab').attributes['aria-pressed'], true);

const first = catalog.records.find(r => r.release === catalog.current_version);
const params = new URLSearchParams({release: first.release, model: first.model, scenario: first.scenario});
const linked = boot('?' + params);
assert.equal(linked.run('visible.every(r => r.model === exactModel)'), true);
assert.equal(linked.run('records.get(selected).scenario'), first.scenario);
assert.equal(new URLSearchParams(linked.context.url.slice(1)).get('model'), first.model);

// Legacy Compare keys must never collapse distinct providers.
const fixture = structuredClone(catalog);
const lane = fixture.lanes[0];
fixture.lanes.push({...lane, provider: 'Synthetic second provider'});
fixture.records.push(...fixture.records.filter(r => r.release === lane.release && r.model === lane.model &&
  r.reasoning_effort === lane.reasoning_effort && r.provider === lane.provider)
  .map(r => ({...r, provider: 'Synthetic second provider'})));
const legacy = new URLSearchParams({lane: [lane.release, lane.model, lane.reasoning_effort || ''].join('|||')});
const comparison = boot('?' + legacy, fixture);
assert.equal(comparison.run('pinned.size'), 2);
assert.equal(comparison.run('visible.length'), 2);
assert.equal(comparison.run('mode'), 'browse');
assert.equal(comparison.run('new Set(visible.map(key)).size'), 2);
const shared = boot(comparison.context.url, fixture);
assert.equal(shared.run('pinned.size'), 2);
assert.equal(shared.run('visible.length'), 2);
comparison.elements.get('clear-pins').listeners.click();
assert.equal(comparison.run('pinned.size'), 0);
assert.equal(comparison.run('onlyPinned'), false);

app.elements.get('search').value = 'no-such-model-zzzz';
app.elements.get('search').listeners.input();
assert.equal(app.run('visible.length'), 0);
assert.equal(app.run('selected'), null);
assert.equal(app.elements.get('summary').innerHTML, '');
app.elements.get('reset').listeners.click();
assert.ok(app.run('visible.length > 0'));
assert.equal(boot('?release=not-a-release').run("$('cohort').value"), catalog.current_version);
assert.ok(app.run('esc("<script>")').includes('&lt;script&gt;'));
console.log('Evidence interaction checks passed');
