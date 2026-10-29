'use strict';

function findScenarioDetail(details, condition, harness, scenario, version) {
  return details.find(group => group.condition === condition)?.profiles
    .find(profile => profile.harness === harness)?.rates
    .find(row => row.scenario === scenario && String(row.version) === String(version));
}

function scenarioDuration(seconds) {
  if (seconds == null) return 'Not reported';
  const rounded = Math.round(seconds);
  return rounded < 60 ? `${rounded}s` : `${Math.floor(rounded / 60)}m ${rounded % 60}s`;
}

function renderScenarioAttempts(byId, selected, run) {
  const attempts = byId('attempts');
  attempts.replaceChildren();
  byId('selected-run').hidden = !run;
  if (!run) return;
  byId('model-name').textContent = selected.label;
  byId('model-id').textContent = selected.model;
  byId('evidence').href = selected.evidence_url;
  byId('source').href = 'https://github.com/ducks/replaybook/blob/main/benchmark-data/releases/' + encodeURIComponent(selected.release) + '.json';
  byId('coverage').textContent = `${run.recorded_attempts}/${run.trials} attempt records · cost reported for ${run.cost_reported}/${run.trials} attempts.` +
    (run.cost_lower_bound ? ' Reported cost is a lower bound; missing or in-flight usage is not assumed to be zero.' : '');
  if (!run.attempts.length) {
    const empty = document.createElement('p');
    empty.textContent = 'Individual attempt records were not published for this configuration.';
    attempts.append(empty);
  }
  for (const attempt of run.attempts) {
    const article = document.createElement('article');
    article.className = 'scenario-attempt';
    const heading = document.createElement('div');
    heading.className = 'scenario-attempt-heading';
    const title = document.createElement('strong');
    title.textContent = `Attempt ${attempt.attempt}`;
    const state = document.createElement('span');
    const status = attempt.status !== 'evaluated' ? 'Unavailable' : attempt.passed ? 'Passed' : 'Failed';
    state.className = 'scenario-attempt-status status-' + status.toLowerCase();
    state.textContent = status;
    heading.append(title, state);
    const timing = document.createElement('p');
    timing.className = 'small muted';
    const cost = attempt.usage.cost_usd;
    timing.textContent = `${scenarioDuration(attempt.duration_seconds)} agent time · ${cost == null ? 'cost not reported' : '$' + cost.toFixed(4) + ' reported cost'}` +
      (attempt.failure_category === 'agent_timeout' ? ' · checkpointed usage' : '');
    const phases = document.createElement('div');
    phases.className = 'scenario-attempt-phases';
    for (const [key, label] of [['immediate_http', 'Immediate'], ['service_restart', 'Restart'], ['host_reboot', 'Reboot']]) {
      const confirmed = attempt.verification?.[key];
      const badge = document.createElement('span');
      badge.className = confirmed === true ? 'phase-passed' : 'phase-unconfirmed';
      badge.textContent = `${label}: ${confirmed === true ? 'passed' : confirmed === false ? 'not confirmed' : 'not reported'}`;
      phases.append(badge);
    }
    article.append(heading, timing, phases);
    if (!attempt.passed) {
      const failure = document.createElement('p');
      failure.className = 'scenario-attempt-failure';
      failure.textContent = attempt.failure || 'No failure message was recorded.';
      const category = document.createElement('code');
      category.className = 'scenario-attempt-category';
      category.textContent = attempt.failure_category || 'Category not recorded';
      article.append(failure, category);
    }
    const afterTimeout = attempt.verification?.after_agent_timeout;
    if (afterTimeout) {
      const post = document.createElement('p');
      post.className = 'small muted';
      post.textContent = `Post-timeout verification: ${afterTimeout.durable_repair ? 'durable repair confirmed' : 'no durable repair confirmed'}.` +
        (afterTimeout.failure ? ' ' + afterTimeout.failure : '') +
        (afterTimeout.failure_category ? ` (${afterTimeout.failure_category})` : '');
      article.append(post);
    }
    const execution = document.createElement('details');
    execution.className = 'scenario-attempt-execution';
    const summary = document.createElement('summary');
    summary.textContent = 'Execution details';
    const info = document.createElement('p');
    info.textContent = [
      attempt.model_rounds == null ? null : `${attempt.model_rounds} recorded model rounds`,
      attempt.tool_calls == null ? null : `${attempt.tool_calls} recorded tool calls`,
      attempt.usage.input_tokens == null ? null : `${attempt.usage.input_tokens.toLocaleString()} input tokens`,
      attempt.usage.output_tokens == null ? null : `${attempt.usage.output_tokens.toLocaleString()} output tokens`,
      attempt.usage.cache_read_tokens == null ? null : `${attempt.usage.cache_read_tokens.toLocaleString()} cached input tokens`,
      attempt.timeout_seconds == null ? null : `${attempt.timeout_seconds}s agent deadline`,
      attempt.usage.cost_source ? `Cost source: ${attempt.usage.cost_source}` : null,
      attempt.started_at ? `Started: ${attempt.started_at}` : null,
      attempt.finished_at ? `Finished: ${attempt.finished_at}` : null,
    ].filter(Boolean).join(' · ') || 'Execution metadata was not published.';
    execution.append(summary, info);
    article.append(execution);
    attempts.append(article);
  }
}

function initializeScenarioDialog(selector, harness) {
  const dialog = document.getElementById('score-scenario-dialog');
  if (!dialog || typeof dialog.showModal !== 'function') return;
  const payload = JSON.parse(document.getElementById('score-scenario-data').textContent);
  const details = Array.isArray(payload) ? payload : payload.groups;
  const runs = payload.runs || {};
  const byId = id => document.getElementById('scenario-dialog-' + id);
  let opener = null;

  document.addEventListener('click', event => {
    const trigger = event.target.closest('.scenario-trigger');
    if (!trigger || event.button > 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    const row = findScenarioDetail(details, selector.value, harness.value, trigger.dataset.scenario, trigger.dataset.version);
    if (!row) return;
    event.preventDefault();
    if (!dialog.open) opener = trigger;
    const selected = row.outcomes.find(outcome => outcome.evidence_url === trigger.getAttribute('href'));
    const run = selected ? runs[selected.evidence_url] : null;
    byId('eyebrow').textContent = run ? `${selected.label} / scenario run` : 'Scenario / durable repair';
    byId('title').textContent = row.label;
    byId('meta').textContent = `${row.scenario} · v${row.version}`;
    byId('rate').textContent = row.failure_rate == null ? '—' : `${row.failure_rate.toFixed(1)}%`;
    byId('trials').textContent = `${row.unsuccessful} / ${row.trials}`;
    byId('configurations').textContent = row.configurations;
    const group = details.find(group => group.condition === selector.value);
    const benchmark = `${group.label || 'Core'} ${group.benchmark}`;
    const harnessLabel = harness.value ? harness.selectedOptions[0].textContent : 'All harnesses';
    byId('scope').textContent = `${benchmark} · ${harnessLabel}. Each configuration entry belongs to its own published release; repeated entries count separately.`;
    byId('unavailable').textContent = `${row.unavailable} unavailable attempts, included as unsuccessful. Select a configuration to inspect its attempt evidence.`;
    byId('rate-label').textContent = 'Observed failure rate';
    byId('trials-label').textContent = 'Unsuccessful / recorded';
    byId('configurations-label').textContent = 'Configuration entries';
    if (run) {
      byId('rate').textContent = `${run.passed}/${run.trials}`;
      byId('rate-label').textContent = 'Durable repairs';
      byId('trials').textContent = scenarioDuration(run.median_duration_seconds);
      byId('trials-label').textContent = 'Median agent time';
      byId('configurations').textContent = run.known_cost_usd == null ? 'Unknown' :
        (run.cost_lower_bound ? '≥' : '') + '$' + run.known_cost_usd.toFixed(4);
      byId('configurations-label').textContent = 'Reported scenario cost';
      byId('scope').textContent = `${benchmark} · release ${selected.release} · ${selected.provider} · ${selected.harness} · ${selected.reasoning} reasoning.`;
      byId('unavailable').textContent = `${selected.unavailable} unavailable attempts in this configuration, included as unsuccessful.`;
    }
    byId('unavailable').hidden = Boolean(run && !selected.unavailable);
    renderScenarioAttempts(byId, selected, run);
    byId('comparison').open = !run;
    byId('comparison-title').textContent = `Compare ${row.configurations} configurations · ${row.failure_rate == null ? 'failure rate not measured' : row.failure_rate.toFixed(1) + '% observed scenario failure rate'}`;
    const outcomes = byId('outcomes');
    outcomes.replaceChildren();
    for (const outcome of row.outcomes) {
      const selected = outcome.evidence_url === trigger.getAttribute('href');
      const link = document.createElement('a');
      link.className = 'scenario-dialog-model scenario-trigger' + (selected ? ' is-selected' : '');
      link.href = outcome.evidence_url;
      link.dataset.scenario = row.scenario;
      link.dataset.version = String(row.version);
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
      link.setAttribute('aria-label', `${outcome.label}, ${outcome.provider}, ${outcome.harness}, ${outcome.reasoning} reasoning, release ${outcome.release}: ${outcome.passed} of ${outcome.trials} durable repairs; ${outcome.unavailable} unavailable. Open this configuration’s attempt details.`);
      outcomes.append(link);
    }
    byId('history').href = 'benchmark-scenario.html?' + new URLSearchParams({scenario: row.scenario});
    document.documentElement.classList.add('scenario-dialog-open');
    if (!dialog.open) dialog.showModal();
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
