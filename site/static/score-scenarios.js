'use strict';

function findScenarioDetail(details, condition, harness, scenario, version) {
  return details.find(group => group.condition === condition)?.profiles
    .find(profile => profile.harness === harness)?.rates
    .find(row => row.scenario === scenario && String(row.version) === String(version));
}

function initializeScenarioDialog(selector, harness) {
  const dialog = document.getElementById('score-scenario-dialog');
  if (!dialog || typeof dialog.showModal !== 'function') return;
  const details = JSON.parse(document.getElementById('score-scenario-data').textContent);
  const byId = id => document.getElementById('scenario-dialog-' + id);
  let opener = null;

  document.addEventListener('click', event => {
    const trigger = event.target.closest('.scenario-trigger');
    if (!trigger || event.button > 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    const row = findScenarioDetail(details, selector.value, harness.value, trigger.dataset.scenario, trigger.dataset.version);
    if (!row) return;
    event.preventDefault();
    opener = trigger;
    byId('title').textContent = row.label;
    byId('meta').textContent = `${row.scenario} · v${row.version}`;
    byId('rate').textContent = row.failure_rate == null ? '—' : `${row.failure_rate.toFixed(1)}%`;
    byId('trials').textContent = `${row.unsuccessful} / ${row.trials}`;
    byId('configurations').textContent = row.configurations;
    const benchmark = details.find(group => group.condition === selector.value).benchmark;
    const harnessLabel = harness.value ? harness.selectedOptions[0].textContent : 'All harnesses';
    byId('scope').textContent = `Core ${benchmark} · ${harnessLabel}. Each configuration entry belongs to its own published release; repeated entries count separately.`;
    byId('unavailable').textContent = `${row.unavailable} unavailable attempts, included as unsuccessful. Select a configuration to inspect its attempt evidence.`;
    const outcomes = byId('outcomes');
    outcomes.replaceChildren();
    for (const outcome of row.outcomes) {
      const selected = outcome.evidence_url === trigger.getAttribute('href');
      const link = document.createElement('a');
      link.className = 'scenario-dialog-model' + (selected ? ' is-selected' : '');
      link.href = outcome.evidence_url;
      const identity = document.createElement('div');
      const name = document.createElement('strong');
      name.textContent = outcome.label;
      const meta = document.createElement('span');
      meta.textContent = [outcome.provider, outcome.harness, `${outcome.reasoning} reasoning`, outcome.release].join(' · ');
      identity.append(name, meta);
      if (selected) {
        const badge = document.createElement('small');
        badge.className = 'scenario-dialog-selected';
        badge.textContent = 'Selected configuration';
        identity.append(badge);
      }
      const result = document.createElement('strong');
      result.className = 'scenario-dialog-result outcome-' + outcome.outcome;
      result.textContent = `${outcome.passed}/${outcome.trials}`;
      const arrow = document.createElement('span');
      arrow.className = 'scenario-dialog-arrow';
      arrow.textContent = '↗';
      arrow.setAttribute('aria-hidden', 'true');
      link.append(identity, result, arrow);
      link.setAttribute('aria-label', `${outcome.label}, ${outcome.provider}, ${outcome.harness}, ${outcome.reasoning} reasoning, release ${outcome.release}: ${outcome.passed} of ${outcome.trials} durable repairs; ${outcome.unavailable} unavailable. View attempt evidence.`);
      outcomes.append(link);
    }
    byId('history').href = 'benchmark-scenario.html?' + new URLSearchParams({scenario: row.scenario});
    document.documentElement.classList.add('scenario-dialog-open');
    dialog.showModal();
    dialog.scrollTop = 0;
  });
  byId('close').addEventListener('click', () => dialog.close());
  dialog.addEventListener('click', event => {
    if (event.target !== dialog) return;
    const rect = dialog.getBoundingClientRect();
    if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) dialog.close();
  });
  dialog.addEventListener('close', () => {
    document.documentElement.classList.remove('scenario-dialog-open');
    opener?.focus();
  });
}
