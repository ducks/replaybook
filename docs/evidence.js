
'use strict';
const data=JSON.parse(document.getElementById('data').textContent);
const $=id=>document.getElementById(id);
const releaseMap=new Map(data.releases.map((r,i)=>[r.version,{...r,order:i}]));
const key=r=>JSON.stringify([r.release,r.provider??null,r.model,r.reasoning_effort??null]);
const cellKey=r=>JSON.stringify([key(r),r.scenario,r.scenario_version]);
const records=new Map(data.records.map(r=>[cellKey(r),r]));
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const pct=n=>n==null?'—':`${Math.round(n*100)}%`;
const time=n=>n==null?'—':`${Math.floor(Math.round(n)/60)}:${String(Math.round(n)%60).padStart(2,'0')}`;
const money=r=>r.cost_reported_trials?`$${r.known_cost_usd.toFixed(4)}${r.cost_reported_trials<r.trials?' + unreported':''}`:'Not reported';
function resultClass(c){
 if(!c.evaluated||c.unavailable||c.evaluated!==c.trials)return 'result-incomplete';
 if(c.passed===c.trials)return 'result-green';
 return c.passed/c.trials>=2/3?'result-yellow':'result-red';
}
let mode='cohort', metric='outcomes', selected=null, visible=[];
const pinned = new Set();
let onlyPinned = false;
let exactModel = null;
const filterIds = ['provider', 'harness', 'reasoning', 'tier', 'input', 'scenario', 'search'];

function restoreState() {
 const params = new URLSearchParams(window.location.search);
 mode = params.get('view') === 'browse' ? 'browse' : 'cohort';
 if (releaseMap.has(params.get('release'))) $('cohort').value = params.get('release');
 for (const id of filterIds) if (params.has(id)) $(id).value = params.get(id);
 exactModel = params.get('model');
 if (exactModel) $('search').value = exactModel;
 for (const value of params.getAll('pin')) {
  if (data.lanes.some(r => key(r) === value)) pinned.add(value);
 }
 // Old Compare URLs omit provider. Preserve every matching lane rather than
 // silently picking one provider when a legacy identity is ambiguous.
 for (const value of params.getAll('lane')) {
  for (const r of data.lanes) {
   if ([r.release, r.model, r.reasoning_effort || ''].join('|||') === value) pinned.add(key(r));
  }
 }
 onlyPinned = params.get('selected') === 'true' || params.has('lane');
 if (onlyPinned) mode = 'browse';
 metric = params.get('metric') === 'duration' ? 'duration' : 'outcomes';
 selected = records.has(params.get('cell')) ? params.get('cell') : null;
}

function updateUrl() {
 const params = new URLSearchParams({view: mode, release: $('cohort').value});
 for (const id of filterIds) if ($(id).value) params.set(id, $(id).value);
 if (exactModel) params.set('model', exactModel);
 for (const value of pinned) params.append('pin', value);
 if (onlyPinned) params.set('selected', 'true');
 if (metric !== 'outcomes') params.set('metric', metric);
 if (selected) params.set('cell', selected);
 history.replaceState(null, '', '?' + params.toString());
}

function renderSummary() {
 $('summary').innerHTML = visible.map(r => {
  const rel = releaseMap.get(r.release);
  const repairCost = r.cost_per_repair_usd == null || !r.cost_reported_trials
   ? '—' : '$' + r.cost_per_repair_usd.toFixed(4) + (r.cost_reported_trials < r.trials ? ' + unreported' : '');
  const tokens = r.input_tokens == null || r.output_tokens == null
   ? '—' : (r.input_tokens + r.output_tokens).toLocaleString();
  return '<tr><th scope="row">' + esc(r.model_label) + '<span class="row-meta">' +
   esc([r.provider || 'Provider not reported', r.reasoning_effort || 'Unspecified reasoning',
    rel.agent_harness.label, r.release].join(' · ')) + '</span></th>' +
   [r.passed + '/' + r.evaluated, pct(r.pass_rate_95_low) + '–' + pct(r.pass_rate_95_high),
    time(r.median_duration_seconds), tokens, money(r), repairCost,
    r.cost_reported_trials + '/' + r.trials].map(v => '<td>' + esc(v) + '</td>').join('') + '</tr>';
 }).join('');
}
const option=(v,l)=>`<option value="${esc(v)}">${esc(l)}</option>`;
function options(id,values,label){$(id).innerHTML=option('',label)+[...new Set(values)].sort().map(v=>option(v,v||'Unspecified')).join('');}
$('cohort').innerHTML=[...data.releases].reverse().map(r=>option(r.version,`${r.title} · ${r.version}`)).join('');
$('cohort').value=data.current_version;
options('provider',data.lanes.map(r=>r.provider||'Not reported'),'All providers');
options('harness',data.releases.map(r=>r.agent_harness?.id||'Not reported'),'All harnesses');
options('reasoning',data.lanes.map(r=>r.reasoning_effort||'Unspecified'),'All reasoning');
options('tier',data.releases.map(r=>r.tier),'All tiers');
options('input',data.releases.map(r=>r.input_mode),'All inputs');
options('scenario',data.records.map(r=>r.scenario),'All scenarios');
$('record-count').textContent=data.records.length;
$('source').textContent=`Catalog ${data.current_version}`;
function fact(label,value){return `<div class="boundary-item"><small>${esc(label)}</small><strong>${esc(value)}</strong></div>`;}
function render(){
 for (const id of ['cohort', ...filterIds.filter(id => id !== 'scenario')]) $(id).disabled = onlyPinned;
 $('cohort-tab').setAttribute('aria-pressed', mode === 'cohort');
 $('browse-tab').setAttribute('aria-pressed', mode === 'browse');
 $('cohort-field').classList.toggle('hidden', mode !== 'cohort');
 $('tier-field').classList.toggle('hidden', mode === 'cohort');
 $('input-field').classList.toggle('hidden', mode === 'cohort');
 $('outcome-mode').setAttribute('aria-pressed', metric === 'outcomes');
 $('duration-mode').setAttribute('aria-pressed', metric === 'duration');
 $('only-pinned').setAttribute('aria-pressed', onlyPinned);
 $('pinned-count').textContent = pinned.size;
 const release=releaseMap.get($('cohort').value);
 const query=$('search').value.toLowerCase().trim();
 visible=data.lanes.filter(r=>{
  if (onlyPinned) return pinned.has(key(r));
  if (exactModel && r.model !== exactModel) return false;
  const rel=releaseMap.get(r.release);
  return (mode!=='cohort'||r.release===release.version)&&(!$('provider').value||(r.provider||'Not reported')===$('provider').value)&&(!$('harness').value||(rel.agent_harness?.id||'Not reported')===$('harness').value)&&(!$('reasoning').value||(r.reasoning_effort||'Unspecified')===$('reasoning').value)&&(!query||`${r.model_label} ${r.model}`.toLowerCase().includes(query))&&(mode==='cohort'||((!$('tier').value||rel.tier===$('tier').value)&&(!$('input').value||rel.input_mode===$('input').value)));
 }).sort((a,b)=>a.model_label.localeCompare(b.model_label)||a.model.localeCompare(b.model)||releaseMap.get(b.release).order-releaseMap.get(a.release).order||(a.provider||'').localeCompare(b.provider||''));
 let scenarios;
 if(mode==='cohort')scenarios=release.scenarios.map(s=>({scenario:s.id,scenario_version:s.version}));
 else{const keys=new Set(visible.map(key));scenarios=[...new Map(data.records.filter(r=>keys.has(key(r))).map(r=>[JSON.stringify([r.scenario,r.scenario_version]),{scenario:r.scenario,scenario_version:r.scenario_version}])).values()].sort((a,b)=>a.scenario.localeCompare(b.scenario)||a.scenario_version-b.scenario_version);}
 if ($('scenario').value) scenarios = scenarios.filter(s => s.scenario === $('scenario').value);
 $('boundary').innerHTML=mode==='cohort'?fact('Tier / input',`${release.tier} / ${release.input_mode}`)+fact('Agent',`${release.agent_harness.label} ${release.agent_harness.version||'(version not reported)'}`)+fact('Controller',`v${release.harness_versions.join(', v')}`)+fact('Per scenario',`${release.attempts} attempts · ${time(release.agent_timeout_seconds)} deadline`)+`<div class="boundary-note">One published cohort. Exact configuration and scenario versions stay attached to every cell.</div>`:`${fact('View','Evidence inventory')}${fact('Grouping','Configuration × release')}${fact('Comparison','Separate cohorts')}<div class="boundary-note">Rows from different releases are not a pooled leaderboard. Select a cohort for a controlled comparison.</div>`;
 $('view-count').textContent=`${visible.length} configurations · ${scenarios.length} scenario versions`;
 $('head').innerHTML=`<tr><th class="model">MODEL / CONFIGURATION</th>${scenarios.map(s=>{const record=data.records.find(r=>r.scenario===s.scenario&&r.scenario_version===s.scenario_version);return `<th class="scenario" title="${esc(record?.scenario_label||s.scenario)}"><b>${esc(s.scenario.split('-')[0])} <small>v${s.scenario_version}</small></b><span>${esc((record?.scenario_label||s.scenario).replace(/^\d+[- ]/,''))}</span></th>`;}).join('')}<th class="score">COHORT REPAIRS<br>95% Wilson interval</th></tr>`;
 $('body').innerHTML=visible.map((r,i)=>{
  const rel=releaseMap.get(r.release);
  const cells=scenarios.map(s=>records.get(cellKey({...r,...s})));
  return `<tr><th scope="row"><span class="model-name"><input class="pin" type="checkbox" data-index="${i}" aria-label="Select ${esc(r.model_label)} · ${esc(r.release)} · ${esc(r.provider)}" ${pinned.has(key(r))?'checked':''}>${esc(r.model_label)}</span><span class="row-meta">${esc(r.provider||'Provider not reported')} · ${esc(r.reasoning_effort||'reasoning unspecified')}</span>${mode==='browse'?`<span class="row-meta">${esc(rel.agent_harness.label)} · ${esc(r.release)} · ${esc(rel.tier)} / ${esc(rel.input_mode)}</span>`:''}</th>${cells.map((c,j)=>{
   if(!c)return '<td class="cell"><span class="muted" aria-label="Not run">—</span></td>';
   const active=selected===cellKey(c);const title=`${r.model_label}: ${c.scenario_label}; ${c.passed}/${c.evaluated} repaired; ${c.unavailable} unavailable`;
   return `<td class="cell"><button class="cell-button ${resultClass(c)}" data-row="${i}" data-scenario="${j}" aria-label="${esc(title)}" aria-pressed="${active}">${metric==='outcomes'?swatches(c):`<strong class="mono">${time(c.median_duration_seconds)}</strong>`}<span class="fraction">${c.passed}/${c.evaluated}${c.unavailable?' · '+c.unavailable+' NA':''}</span></button></td>`;
  }).join('')}<td class="score"><div class="rate">${r.passed}<span class="muted" style="font-size:11px;font-weight:400"> / ${r.evaluated}</span> <span style="font-size:11px">${pct(r.pass_rate)}</span></div>${r.pass_rate_95_low==null?'':`<div class="interval" title="95% Wilson interval: ${pct(r.pass_rate_95_low)}–${pct(r.pass_rate_95_high)}"><i style="left:${r.pass_rate_95_low*100}%;width:${(r.pass_rate_95_high-r.pass_rate_95_low)*100}%"></i><b style="left:calc(${r.pass_rate*100}% - 4px)"></b></div>`}<span class="row-meta">${r.unavailable?r.unavailable+' unavailable':'All attempts evaluated'}</span></td></tr>`;
 }).join('');
 $('empty').classList.toggle('hidden',visible.length>0);
 $('table-note').textContent='Cell color: red below 2/3 repaired, yellow at least 2/3, green all repaired. Incomplete evidence is gray. Each mark is one attempt. Unavailable attempts remain visible and are excluded from repair-rate denominators. Time is the median across evaluated attempts; it is not a median time to successful repair.';
 $('body').querySelectorAll('button').forEach(button=>button.addEventListener('click',()=>{
  const {row: rowIndex, scenario: columnIndex} = button.dataset;
  const row=visible[Number(rowIndex)];
  selected=cellKey({...row,...scenarios[Number(columnIndex)]});
  render();
  $('body').querySelector('[data-row="' + rowIndex + '"][data-scenario="' + columnIndex + '"]')?.focus();
 }));
 $('body').querySelectorAll('.pin').forEach(input => input.addEventListener('change', () => {
  const lane = key(visible[Number(input.dataset.index)]);
  if (input.checked) pinned.add(lane); else pinned.delete(lane);
  if (onlyPinned) render();
  else { $('pinned-count').textContent = pinned.size; updateUrl(); }
 }));
 const selectedRecord=selected&&records.get(selected);
 const visibleKeys=new Set(visible.map(key));
 if(!selectedRecord||!visibleKeys.has(key(selectedRecord))||!scenarios.some(s=>s.scenario===selectedRecord.scenario&&s.scenario_version===selectedRecord.scenario_version)){
  const first = data.records.find(r => visibleKeys.has(key(r)) && scenarios.some(s => s.scenario === r.scenario && s.scenario_version === r.scenario_version));
  selected = first ? cellKey(first) : null;
 }
 inspect(selected?records.get(selected):null);
 if(selected){const c=records.get(selected);const row=visible.findIndex(r=>key(r)===key(c));const column=scenarios.findIndex(s=>s.scenario===c.scenario&&s.scenario_version===c.scenario_version);$('body').querySelector(`[data-row="${row}"][data-scenario="${column}"]`)?.setAttribute('aria-pressed','true');}
 renderSummary();
 updateUrl();
}
function swatches(c){if(!c.outcomes?.length)return `<span class="mono">${pct(c.pass_rate)}</span>`;return `<span class="swatches">${c.outcomes.map(o=>{const type=o.trial_status!=='evaluated'?'unavailable':o.reward===1?'pass':'fail';return `<i class="dot ${type}" aria-hidden="true">${type==='pass'?'✓':type==='fail'?'×':'–'}</i>`;}).join('')}</span>`;}
function inspect(c){
 if(!c){$('inspector').innerHTML='<p class="eyebrow">Evidence inspector</p><h2>Select an observation</h2><p class="detail-help">Clear a filter to show matching configurations.</p>';return;}
 const rel=releaseMap.get(c.release);
 $('inspector').innerHTML=`<p class="eyebrow">Observation / ${esc(c.scenario.split('-')[0])}</p><h2>${esc(c.scenario_label)}</h2><span class="status ${c.failed?'bad':''}">${c.passed} repaired · ${c.failed} failed · ${c.unavailable} unavailable</span><dl class="facts"><dt>Model</dt><dd>${esc(c.model_label)}</dd><dt>Provider</dt><dd>${esc(c.provider||'Not reported')}</dd><dt>Harness</dt><dd>${esc(rel.agent_harness.label)}</dd><dt>Reasoning</dt><dd>${esc(c.reasoning_effort||'Unspecified')}</dd><dt>Median duration</dt><dd>${time(c.median_duration_seconds)}</dd><dt>Known API spend</dt><dd>${esc(money(c))}</dd><dt>Cost coverage</dt><dd>${c.cost_reported_trials}/${c.trials} attempts</dd></dl><h3>Attempt evidence</h3>${(c.outcomes||[]).map(o=>{const type=o.trial_status!=='evaluated'?'unavailable':o.reward===1?'pass':'fail';return `<div class="attempt"><span class="attempt-main"><i class="dot ${type}">${type==='pass'?'✓':type==='fail'?'×':'–'}</i>Attempt ${o.attempt}</span><span>${time(o.duration_seconds)}</span></div>${o.failure_category?`<div class="detail-help">${esc(o.failure_category.replaceAll('_',' '))}</div>`:''}`;}).join('')||'<p class="detail-help">Per-attempt outcomes were not published for this observation.</p>'}<p class="detail-help">${Object.entries(c.failure_categories||{}).map(([k,v])=>`${esc(k.replaceAll('_',' '))} (${v})`).join('<br>')}</p><div class="provenance"><strong>Exact comparison conditions</strong><p>${esc(c.release)} · ${esc(c.tier)} · ${esc(c.input_mode)}</p><p>Scenario v${c.scenario_version} · controller v${rel.harness_versions.join(', v')}</p><p>${esc(rel.agent_harness.version||'Agent version not reported')} · ${time(rel.agent_timeout_seconds)} deadline</p><p class="mono">${esc(c.model)}</p></div><a class="source-link" href="https://github.com/ducks/replaybook/blob/main/benchmark-data/releases/${encodeURIComponent(c.release)}.json" target="_blank" rel="noopener">Inspect tracked source JSON ↗</a>`;
}
function switchMode(value){onlyPinned=false;mode=value;$('cohort-tab').setAttribute('aria-pressed',value==='cohort');$('browse-tab').setAttribute('aria-pressed',value==='browse');$('cohort-field').classList.toggle('hidden',value!=='cohort');$('tier-field').classList.toggle('hidden',value==='cohort');$('input-field').classList.toggle('hidden',value==='cohort');render();}
$('cohort-tab').addEventListener('click',()=>switchMode('cohort'));$('browse-tab').addEventListener('click',()=>switchMode('browse'));
for(const id of ['cohort','provider','harness','reasoning','tier','input','scenario'])$(id).addEventListener('change',render);
$('search').addEventListener('input',()=>{exactModel=null;render();});
$('only-pinned').addEventListener('click',()=>{onlyPinned=!onlyPinned;if(onlyPinned)mode='browse';render();});
$('clear-pins').addEventListener('click',()=>{pinned.clear();onlyPinned=false;render();});
$('reset').addEventListener('click',()=>{for(const id of filterIds)$(id).value='';pinned.clear();onlyPinned=false;exactModel=null;$('cohort').value=data.current_version;selected=null;render();});
for(const value of ['outcomes','duration'])$(value==='outcomes'?'outcome-mode':'duration-mode').addEventListener('click',()=>{metric=value; $('outcome-mode').setAttribute('aria-pressed',value==='outcomes');$('duration-mode').setAttribute('aria-pressed',value==='duration');render();});
restoreState();
render();
