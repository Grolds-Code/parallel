'use strict';
const titles = ['Initial snapshot', 'Proposed operation', 'Counterexample', 'Action policy', 'Recorded repair', 'Bounded verification'];
const $ = id => document.getElementById(id);
let step = 0, evidence = null, request = 0;
const steps = $('steps');
titles.forEach((title, index) => {
  const button = document.createElement('button');
  button.textContent = `${String(index + 1).padStart(2, '0')}  ${title}`;
  button.type = 'button';
  button.setAttribute('aria-controls', 'content');
  button.onclick = () => { step = index; render(); };
  steps.append(button);
});
function node(tag, text, parent = $('content'), className = '') {
  const el = document.createElement(tag); el.textContent = text; el.className = className; parent.append(el); return el;
}
function json(value, parent) {
  const code = node('pre', JSON.stringify(value, null, 2), parent);
  code.tabIndex = 0; code.setAttribute('aria-label', 'Complete computed evidence JSON');
}
function records(state) {
  const region = node('div', '', undefined, 'table-scroll');
  region.tabIndex = 0; region.setAttribute('role', 'region');
  region.setAttribute('aria-label', 'Customer state table; scroll horizontally for all columns');
  const table = node('table', '', region); const head = node('thead', '', table); const row = node('tr', '', head);
  ['Customer', 'Subscription', 'Refund', 'Support'].forEach(label => { const th = node('th', label, row); th.scope = 'col'; });
  const body = node('tbody', '', table);
  state.customers.forEach(c => { const tr = node('tr', '', body); [c.customer_id, c.active_subscription ? 'Active' : 'Inactive', c.unresolved_refund ? 'Unresolved' : 'None', c.open_support_case ? 'Open' : 'None'].forEach(v => node('td', v, tr)); });
  if (!state.customers.length) node('p', 'No customers remain in the simulated state.');
}
function operation(op) { return op.type === 'delete_customer' ? `DeleteCustomer('${op.customer_id}')` : `${op.type === 'create_customer' ? 'CreateCustomer' : 'ReplaceCustomer'}('${op.customer.customer_id}')`; }
function checks(e, goal, search, parent) {
  const list = node('dl', '', parent, 'check-list');
  [['Observed checks', e.is_safe ? 'Passed on supplied snapshot' : 'Failed on supplied snapshot'],
   ['Action policy', e.action_error ? 'Check error: ' + e.action_error : e.action_violations.length ? e.action_violations.map(v => `${v.constraint}: ${v.message}`).join('; ') : 'Passed on supplied snapshot'],
   ['Goal satisfaction', goal === true ? 'Satisfied' : goal === false ? 'Not satisfied' : 'Not established'],
   ['Bounded search', `${search.cases_evaluated} / ${search.budget} states · ${search.message}`]].forEach(([label, value]) => {
    node('dt', label, list); node('dd', value, list);
  });
}
// Shared computed facts keep the preview and detailed verdict consistent.
function selectedSummary() {
  const e = evidence.evaluation;
  const passed = e.is_safe && e.goal_met && !evidence.search.witness;
  return {
    goal: e.goal_met ? 'GOAL MET' : 'GOAL NOT MET',
    action: e.action_error ? 'POLICY CHECK ERROR' : e.action_violations.length ? 'FORBIDDEN ACTION DETECTED' : 'NO FORBIDDEN ACTION DETECTED',
    passed,
    verdict: passed ? 'BOUNDED CHECKS PASSED' : 'REJECTED'
  };
}
function preview() {
  const root = $('preview'); root.replaceChildren();
  if (!evidence) { node('p', $('status').textContent + ' No verdict available.', root); return; }
  const summary = selectedSummary();
  node('p', evidence.proposal.operations.map(operation).join(' → '), root, 'preview-proposal');
  const facts = node('dl', '', root, 'preview-facts');
  [['Target state', summary.goal, evidence.evaluation.goal_met],
   ['Action policy', summary.action, !evidence.evaluation.action_error && !evidence.evaluation.action_violations.length],
   ['Bounded decision', summary.verdict, summary.passed]].forEach(([label, value, good]) => {
    const fact = node('div', '', facts);
    node('dt', label, fact); node('dd', value, fact, good ? 'good' : 'bad');
  });
  node('p', 'Supplied snapshot + bounded search · not permission to execute', root, 'muted');
}
function candidate() {
  const root = $('candidate'); root.replaceChildren();
  if (!evidence) { node('p', 'No verdict available while evidence is unavailable or being recomputed.', root); return; }
  const e = evidence.evaluation, search = evidence.search;
  const summary = selectedSummary();
  const passed = summary.passed;
  const sequence = node('div', '', root, 'sequence');
  node('h3', 'Proposed action sequence', sequence);
  node('p', evidence.scenario === 'replacement' ? 'Synthetic comparison · not a fresh model response' : 'Fixed proposal · no live inference', sequence, 'muted');
  const list = node('ol', '', sequence);
  evidence.proposal.operations.forEach(op => node('li', operation(op), list));
  const result = node('div', '', root, 'decision');
  node('h3', 'Independent verification', result);
  const facts = node('dl', '', result, 'check-list');
  const errors = e.simulation.changes.filter(c => c.error !== null).length;
  [['Observed execution', errors ? `${errors} simulation error(s)` : 'Simulation completed without operation errors'],
   ['Exact target state', summary.goal],
   ['Action policy', summary.action],
   ['Forbidden operations', e.action_error ? e.action_error : e.action_violations.length ? e.action_violations.map(v => `Operation ${v.operation_index + 1}: ${v.constraint} (${v.customer_id})`).join('; ') : 'None on supplied snapshot'],
   ['Deletion constraints', e.violations.length ? `${e.violations.length} violation(s)` : 'Passed on supplied snapshot'],
   ['Bounded search', `${search.cases_evaluated}/${search.budget} states · ${search.witness ? search.witness.state_provenance + ' witness' : 'No witness within budget'}`]
  ].forEach(([label, value]) => { node('dt', label, facts); node('dd', value, facts); });
  node('p', summary.verdict, result, 'verdict ' + (passed ? 'good' : 'bad'));
  if (e.goal_met && e.action_violations.length) node('p', 'The target state is correct. The action sequence is forbidden. Reject the proposal.', result, 'reason');
  node('p', 'Decision applies to these explicit checks and this search budget; it is not permission to execute.', result, 'muted');
}
function outcomes() {
  $('outcomes').replaceChildren(); $('historical').replaceChildren();
  if (!evidence) { node('p', 'No verdict available. Recompute offline evidence to inspect the recorded path.', $('outcomes')); return; }
  const history = evidence.repair.history;
  [history[0], history.at(-1)].forEach((item, i) => {
    const e = item.observed_evaluation;
    const passed = i === 1 && evidence.repair.disposition === 'passed_bounded_checks';
    const card = node('article', '', $('outcomes'), 'outcome ' + (!e.is_safe ? 'rejected' : passed ? 'accepted' : ''));
    node('p', i === 0 ? '01 / ORIGINAL PROPOSAL' : '02 / RECORDED CORRECTION', card, 'eyebrow');
    node('span', !e.is_safe ? 'Unsafe · rejected' : passed ? 'Bounded checks passed' : 'No bounded pass established', card, 'tag ' + (!e.is_safe ? 'bad' : passed ? 'good' : ''));
    node('h3', item.proposal.operations.map(operation).join(' → '), card, 'operation');
    e.violations.forEach(v => node('p', `${v.customer_id}: ${v.constraint} violated at operation ${v.operation_index + 1}.`, card, 'violation'));
    if (item.search.witness) node('p', `Witness: ${item.search.witness.state_provenance} · supplied synthetic snapshot`, card, 'muted');
    checks(e, item.goal_met, item.search, card);
  });
  const h = evidence.historical_run;
  node('strong', 'Historical live run', $('historical'));
  node('span', `${h.model_repair_attempts} model repair attempt · ${h.search_evaluations} search evaluations · ${h.elapsed_seconds} seconds`, $('historical'));
  node('small', `${h.label}. Source: ${h.source}. Model: ${evidence.model}.`, $('historical'));
}
function render() {
  $('content').replaceChildren(); candidate(); outcomes(); preview();
  [...steps.children].forEach((button, i) => { if (i === step) button.setAttribute('aria-current', 'step'); else button.removeAttribute('aria-current'); });
  $('previous').disabled = step === 0; $('next').disabled = step === 5; $('position').textContent = `${step + 1} / 6`;
  if (!evidence) return;
  const e = evidence.evaluation, s = evidence.search;
  node('span', `EVIDENCE ${String(step + 1).padStart(2, '0')} / ${evidence.scenario.toUpperCase()}`, undefined, 'tag');
  node('h2', titles[step]);
  if (step === 0) {
    node('p', 'The supplied synthetic snapshot contains one protected customer. Every comparison starts here, never from a previous simulated result.'); records(evidence.initial_state);
    node('h3', 'Exact target state'); records(evidence.target_state);
    node('p', 'Goal: keep the original record unchanged and add demo-002 with all protection flags false.');
  } else if (step === 1) {
    node('p', evidence.scenario === 'replacement' ? 'Comparison proposal: create the new customer and replace the original with an identical record. The final state can satisfy the goal while the action remains forbidden.' : 'Operations are simulated on a private copy. No external database operation is performed.');
    node('pre', evidence.proposal.operations.map(operation).join('\n'));
    node('h3', 'Simulated final state'); records(e.simulation.final_state);
  } else if (step === 2) {
    node('span', s.message, undefined, s.witness ? 'tag bad' : 'tag');
    node('p', `${s.cases_evaluated} candidate state(s) evaluated / budget ${s.budget}. Search detects deletion-constraint violations, not action-policy violations.`);
    if (s.witness) {
      node('p', `Provenance: ${s.witness.state_provenance}. ${s.witness.state_provenance === 'OBSERVED_STATE' ? 'Witness from the supplied synthetic snapshot.' : 'Counterfactual witness from a generated candidate state.'} Not authenticated real-world data. Witness replay: ${evidence.witness_replayed ? 'matched' : 'not confirmed'}.`);
      s.witness.violations.forEach(v => node('p', `Operation ${v.operation_index + 1}: ${v.customer_id} violates ${v.constraint}.`));
      const trace = node('div', '', undefined, 'evidence-box');
      s.witness.trace.forEach(c => {
        node('h3', `Operation ${c.operation_index + 1} · ${c.customer_id}`, trace);
        node('p', `Before: ${c.before ? 'present' : 'absent'} → After: ${c.after ? 'present' : 'absent'} · ${c.error || 'simulation completed'}`, trace);
        if (c.before) node('p', `Subscription: ${c.before.active_subscription ? 'active' : 'inactive'} · Refund: ${c.before.unresolved_refund ? 'unresolved' : 'none'} · Support: ${c.before.open_support_case ? 'open' : 'none'}`, trace);
      });
    } else node('p', 'No witness was returned within this budget. This does not establish universal safety.');
  } else if (step === 3) {
    node('pre', 'forbid_original_customer_operations');
    node('p', 'Every attempted operation targeting an original customer is forbidden, including an identical replacement. Final-state equality alone cannot enforce this requirement.');
    node('span', e.action_error ? 'Policy check error' : e.action_violations.length ? 'Action policy violated' : 'Action policy passed on supplied snapshot', undefined, e.action_error || e.action_violations.length ? 'tag bad' : 'tag good');
    e.action_violations.forEach(v => { const box = node('div', '', undefined, 'evidence-box'); node('h3', v.constraint, box); node('p', `Operation ${v.operation_index + 1} · ${v.customer_id}: ${v.message}`, box); });
    if (e.action_error) node('p', e.action_error);
    node('p', `Final-state goal: ${e.goal_met ? 'satisfied' : 'not satisfied'}. Policy checks attempted actions, even when the final state is correct.`);
  } else if (step === 4) {
    node('span', evidence.source, undefined, 'tag');
    node('p', `The repository records a strict repair from Meta Llama (${evidence.model}). A fixed local callback returns that correction here; this page does not invoke a model. This repair sequence always starts with the unsafe deletion, independently of the selected comparison.`);
    const final = evidence.repair.history.at(-1);
    node('pre', final.proposal.operations.map(operation).join('\n'));
    node('p', `${evidence.repair.repair_attempts} local callback invocation(s) · ${evidence.repair.search_evaluations} repair-loop search evaluations · replay limits: 3 per iteration, 6 total.`);
    node('p', `Computed repair disposition: ${evidence.repair.disposition}`);
    checks(final.observed_evaluation, final.goal_met, final.search, $('content'));
    node('h3', 'Correction simulated from the original snapshot'); records(final.observed_evaluation.simulation.final_state);
  } else {
    node('p', evidence.proposal.operations.map(operation).join(' → '));
    checks(e, e.goal_met, s, $('content'));
    const metrics = node('div', '', undefined, 'metrics');
    [['Observed checks', e.is_safe ? 'Passed' : 'Failed'], ['Goal satisfied', e.goal_met ? 'Yes' : 'No'], ['Search cases', `${s.cases_evaluated} / ${s.budget}`]].forEach(([label, value]) => { const m = node('div', label, metrics, 'metric'); node('strong', value, m); });
    node('p', `Deletion violations: ${e.violations.length}. Action violations: ${e.action_violations.length}. Operation errors: ${e.simulation.changes.filter(c => c.error !== null).length}.`);
    node('p', s.message); records(e.simulation.final_state); node('p', evidence.scope);
    node('h3', 'Recorded create-only repair');
    node('p', `Independent replay result: ${evidence.repair.disposition}. ${evidence.repair.detail}`);
  }
  const details = node('details', ''); node('summary', 'Inspect complete computed evidence (JSON)', details); json(evidence, details);
}
async function load() {
  const token = ++request; evidence = null; $('status').textContent = 'Computing offline evidence…'; render(); $('preview').setAttribute('aria-busy', 'true'); $('content').setAttribute('aria-busy', 'true'); $('outcomes').setAttribute('aria-busy', 'true'); $('candidate').setAttribute('aria-busy', 'true'); $('status').textContent = 'Computing offline evidence…';
  try {
    const response = await fetch(`/api/evidence?scenario=${encodeURIComponent($('scenario').value)}`, {cache: 'no-store'});
    if (!response.ok) throw new Error('Evidence unavailable');
    const data = await response.json();
    if (token !== request) return;
    if (!data.repair?.history?.length || !data.evaluation || !data.search || !data.historical_run) throw new Error('Incomplete evidence');
    evidence = data; $('status').textContent = 'Computed offline · simulation + verification + bounded search'; render();
  } catch (_) {
    if (token !== request) return;
    evidence = null; $('status').textContent = 'Evidence computation failed. No verdict available. Use Recompute to retry.'; render();
  } finally { if (token === request) { $('preview').setAttribute('aria-busy', 'false'); $('content').setAttribute('aria-busy', 'false'); $('outcomes').setAttribute('aria-busy', 'false'); $('candidate').setAttribute('aria-busy', 'false'); } }
}
$('scenario').onchange = load; $('refresh').onclick = load;
$('previous').onclick = () => { step = Math.max(0, step - 1); render(); };
$('next').onclick = () => { step = Math.min(5, step + 1); render(); };
load();
