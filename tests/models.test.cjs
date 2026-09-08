const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const template = fs.readFileSync('site/templates/benchmark-models.html', 'utf8');
const script = template.match(/<script>([\s\S]*?)<\/script>/)[1];
const context = vm.createContext({URLSearchParams});
vm.runInContext(script.slice(0, script.indexOf('async function initialize')), context);
const lanes = [
  {model: 'vendor/model', model_label: 'Model', provider: 'A', reasoning_effort: 'high'},
  {model: 'vendor/model', model_label: 'Model', provider: 'B', reasoning_effort: 'low'},
  {model: 'other/model', model_label: 'Model', provider: 'C', reasoning_effort: 'high'},
];
context.lanes = lanes;
assert.equal(vm.runInContext('groupModels(lanes).length', context), 2);
assert.equal(vm.runInContext('groupModels(lanes).find(g => g.model === "vendor/model").lanes.length', context), 2);
context.cell = {...lanes[0], release: '20260906.0.0'};
const url = vm.runInContext('compareUrl(cell)', context);
assert.ok(url.startsWith('benchmark-evidence.html?'));
const params = new URLSearchParams(url.split('?')[1]);
assert.equal(params.get('model'), lanes[0].model);
assert.equal(params.get('provider'), 'A');
assert.equal(params.get('reasoning'), 'high');
assert.ok(!template.includes('model-family-id'));
assert.ok(template.includes('class="model-family-grid"'));
console.log('Model grouping checks passed');
