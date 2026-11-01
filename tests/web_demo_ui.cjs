// Minimal DOM harness: exercises the shipped renderer offline, with real API fixtures.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
class Element {
  constructor(tagName = '') { this.tagName = tagName; this.children = []; this.attributes = {}; this.textContent = ''; this.value = ''; }
  append(el) { this.children.push(el); }
  replaceChildren() { this.children = []; this.textContent = ''; }
  setAttribute(k, v) { this.attributes[k] = v; }
  removeAttribute(k) { delete this.attributes[k]; }
  text() { return this.textContent + ' ' + this.children.map(c => c.text()).join(' '); }
}
const ids = Object.fromEntries(['preview','candidate','steps','content','outcomes','historical','previous','next','position','scenario','refresh','status'].map(id => [id, new Element()]));
const html = fs.readFileSync('src/parallel_agent/web_assets/index.html', 'utf8');
const scenarioSelect = html.match(/<select[^>]*id="scenario"[^>]*>([\s\S]*?)<\/select>/)[1];
const options = [...scenarioSelect.matchAll(/<option([^>]*)value="([^"]+)"([^>]*)>/g)];
ids.scenario.value = (options.find(option => /selected/.test(option[1] + option[3])) || options[0])[2];
assert.equal(ids.scenario.value, 'replacement');
const fixtures = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
let pending = [];
const context = vm.createContext({document: {getElementById: id => ids[id], createElement: tag => new Element(tag)}, fetch: url => new Promise(resolve => pending.push({url, resolve}))});
vm.runInContext(fs.readFileSync('src/parallel_agent/web_assets/app.js', 'utf8'), context);
const flush = () => new Promise(resolve => setImmediate(resolve));
async function respond(data, ok = true) { pending.shift().resolve({ok, json: async () => data}); await flush(); }
(async () => {
  assert.match(pending[0].url, /scenario=replacement/);
  await respond(fixtures.replacement);
  assert.match(ids.outcomes.text(), /Unsafe · rejected/);
  assert.match(ids.outcomes.text(), /Bounded checks passed/);
  assert.match(ids.outcomes.text(), /active_subscription/);
  assert.match(ids.outcomes.text(), /original_customer_untouched/);
  assert.match(ids.historical.text(), /1 model repair attempt · 4 search evaluations · 63.73 seconds/);
  const fixed = ids.outcomes.text();
  function assertPreview(scenario) {
    const e = scenario.evaluation;
    const expected = [e.goal_met ? 'GOAL MET' : 'GOAL NOT MET',
      e.action_error ? 'POLICY CHECK ERROR' : e.action_violations.length ? 'FORBIDDEN ACTION DETECTED' : 'NO FORBIDDEN ACTION DETECTED',
      e.is_safe && e.goal_met && !scenario.search.witness ? 'BOUNDED CHECKS PASSED' : 'REJECTED'];
    const facts = ids.preview.children.find(el => el.className === 'preview-facts');
    assert.deepEqual(facts.children.map(el => el.children[0].textContent), ['Target state', 'Action policy', 'Bounded decision']);
    assert.deepEqual(facts.children.map(el => el.children[1].textContent), expected);
    assert.deepEqual(facts.children.map(el => el.children[1].className), [e.goal_met ? 'good' : 'bad', !e.action_error && !e.action_violations.length ? 'good' : 'bad', e.is_safe && e.goal_met && !scenario.search.witness ? 'good' : 'bad']);
    assert.match(ids.preview.text(), /not permission to execute/);
    for (const value of expected) assert.ok(ids.candidate.text().includes(value));
    assert.equal(ids.preview.attributes['aria-busy'], 'false');
    const sequence = ids.candidate.children.find(el => el.className === 'sequence').children[2];
    assert.equal(ids.preview.children[0].textContent, sequence.children.map(op => op.textContent).join(' → '));
  }
  assertPreview(fixtures.replacement);
  // Mobile DOM contracts: natural reading order, keyboard scroll regions,
  // complete evidence, and navigation independent of pointer hover.
  const descendants = el => el.children.flatMap(child => [child, ...descendants(child)]);
  assert.deepEqual(ids.candidate.children.map(el => el.className), ['sequence', 'decision']);
  assert.match(ids.outcomes.children[0].text(), /01 \/ ORIGINAL PROPOSAL/);
  assert.match(ids.outcomes.children[1].text(), /02 \/ RECORDED CORRECTION/);
  const regions = descendants(ids.content).filter(el => el.className === 'table-scroll');
  assert.equal(regions.length, 2);
  for (const region of regions) {
    assert.equal(region.tabIndex, 0);
    assert.equal(region.attributes.role, 'region');
    assert.match(region.attributes['aria-label'], /scroll horizontally/);
    assert.equal(region.children[0].tagName, 'table');
    assert.equal(descendants(region).filter(el => el.tagName === 'th').length, 4);
  }
  const json = descendants(ids.content).find(el => el.tagName === 'pre');
  assert.equal(json.tabIndex, 0);
  assert.deepEqual(JSON.parse(json.textContent), fixtures.replacement);
  assert.match(html, /width=device-width,initial-scale=1/);
  assert.ok(options.every(option => option[2].length > 0));
  for (const button of ids.steps.children) assert.equal(button.attributes['aria-controls'], 'content');
  ids.previous.onclick(); assert.equal(ids.position.textContent, '1 / 6');
  assert.equal(ids.previous.disabled, true);
  for (let i = 0; i < 8; i++) ids.next.onclick();
  assert.equal(ids.position.textContent, '6 / 6');
  assert.equal(ids.next.disabled, true);
  for (let i = 0; i < 8; i++) ids.previous.onclick();
  assert.equal(ids.position.textContent, '1 / 6');

  assert.match(html, /Selected candidate · computed evidence/);
  const hero = html.slice(html.indexOf('<section class="intro"'), html.indexOf('<section class="candidate-section"'));
  assert.match(hero, /aria-labelledby="hero-title"/);
  assert.doesNotMatch(hero, /<br|class="ribbon"/);
  assert.ok(hero.indexOf('hero-narrative') < hero.indexOf('evidence-preview'));
  assert.ok(hero.indexOf('id="preview"') < hero.indexOf('href="#candidate-controls"'));
  assert.match(hero, /Prerecorded Meta Llama inference; verification freshly computed offline/);
  assert.match(html, /href="#candidate-controls"/);
  assert.match(html, /id="candidate-controls" tabindex="-1"/);
  for (const scenario of ['replacement', 'create', 'delete']) {
    ids.scenario.value = scenario; ids.scenario.onchange();
    assert.doesNotMatch(ids.outcomes.text(), /Bounded checks passed/);
    assert.match(ids.preview.text(), /Computing offline evidence/);
    assert.doesNotMatch(ids.preview.text(), /GOAL MET|REJECTED|BOUNDED CHECKS PASSED/);
    assert.equal(ids.preview.attributes['aria-busy'], 'true');
    assert.match(pending[0].url, new RegExp(`scenario=${scenario}`));
    await respond(fixtures[scenario]);
    assertPreview(fixtures[scenario]);
    assert.equal(ids.outcomes.text(), fixed);
    assert.match(ids.candidate.text(), /Simulation completed without operation errors/);
    if (scenario === 'replacement') {
      assert.match(ids.candidate.text(), /GOAL MET/);
      assert.match(ids.candidate.text(), /REJECTED/);
      assert.match(ids.candidate.text(), /Operation 2: original_customer_untouched/);
      assert.match(ids.candidate.text(), /No witness within budget/);
    } else if (scenario === 'create') {
      assert.match(ids.candidate.text(), /BOUNDED CHECKS PASSED/);
    } else {
      assert.match(ids.candidate.text(), /GOAL NOT MET/);
      assert.match(ids.candidate.text(), /REJECTED/);
      assert.match(ids.candidate.text(), /OBSERVED_STATE witness/);
    }
    for (let i = 0; i < 6; i++) {
      ids.steps.children[i].onclick();
      assert.equal(ids.steps.children[i].attributes['aria-current'], 'step');
      assert.equal(ids.steps.children.filter(b => b.attributes['aria-current']).length, 1);
      assert.match(ids.content.text(), /Inspect complete computed evidence/);
      if (i === 3 && scenario === 'replacement') {
        assert.match(ids.content.text(), /Action policy violated/);
        assert.match(ids.content.text(), /Final-state goal: satisfied/);
      }
    }
  }
  ids.refresh.onclick(); await respond({}, false);
  assert.match(ids.status.text(), /No verdict available/);
  assert.match(ids.preview.text(), /Evidence computation failed/);
  assert.doesNotMatch(ids.preview.text(), /GOAL MET|REJECTED|BOUNDED CHECKS PASSED/);
  assert.match(ids.candidate.text(), /No verdict available/);
  assert.doesNotMatch(ids.candidate.text(), /GOAL MET|BOUNDED CHECKS PASSED|REJECTED/);
  assert.doesNotMatch(ids.outcomes.text(), /Bounded checks passed/);
  assert.equal(ids.content.text().trim(), '');
  ids.refresh.onclick(); await respond({});
  assert.match(ids.status.text(), /No verdict available/);
  assert.match(ids.preview.text(), /Evidence computation failed/);
  assert.doesNotMatch(ids.preview.text(), /GOAL MET|REJECTED|BOUNDED CHECKS PASSED/);
  assert.match(ids.candidate.text(), /No verdict available/);
  assert.doesNotMatch(ids.candidate.text(), /GOAL MET|BOUNDED CHECKS PASSED|REJECTED/);
  ids.refresh.onclick(); ids.refresh.onclick();
  await respond(fixtures.delete); // stale request cannot restore a verdict
  assert.doesNotMatch(ids.preview.text(), /GOAL MET|REJECTED|BOUNDED CHECKS PASSED/);
  assert.doesNotMatch(ids.outcomes.text(), /Bounded checks passed/);
  await respond(fixtures.create);
  assert.match(ids.outcomes.text(), /Bounded checks passed/);
  assertPreview(fixtures.create);
  assert.equal(ids.outcomes.attributes['aria-busy'], 'false');
  // A changed API result must change the headline verdict, not keep a scripted pass.
  const altered = structuredClone(fixtures.create);
  altered.repair.disposition = 'repair_limit';
  altered.repair.history.at(-1).observed_evaluation.is_safe = false;
  altered.repair.history.at(-1).goal_met = false;
  ids.refresh.onclick(); await respond(altered);
  assert.doesNotMatch(ids.outcomes.text(), /Bounded checks passed/);
  assert.match(ids.outcomes.text(), /Not satisfied/);
  // A witness prevents a bounded pass even with a correct observed result.
  const witnessed = structuredClone(fixtures.create);
  witnessed.search.witness = fixtures.delete.search.witness;
  ids.refresh.onclick(); await respond(witnessed);
  assertPreview(witnessed);
  assert.match(ids.candidate.text(), /REJECTED/);
  // Policy-check failure must replace a previous pass in both views.
  const policyError = structuredClone(fixtures.create);
  policyError.evaluation.action_error = 'Policy unavailable';
  policyError.evaluation.is_safe = false;
  ids.refresh.onclick(); await respond(policyError);
  assertPreview(policyError);
  assert.match(ids.preview.text(), /POLICY CHECK ERROR/);
  assert.match(ids.preview.text(), /REJECTED/);
  // Stress long unbroken API annotations: preserve text and verdict consistency.
  const longEvidence = structuredClone(fixtures.replacement);
  const annotation = 'source_' + 'x'.repeat(512);
  longEvidence.historical_run.source = annotation;
  longEvidence.evaluation.action_violations[0].constraint = annotation;
  ids.refresh.onclick(); await respond(longEvidence);
  assertPreview(longEvidence);
  assert.ok(ids.historical.text().includes(annotation));
  assert.ok(ids.candidate.text().includes(annotation));
  ids.steps.children[3].onclick();
  assert.ok(ids.content.text().includes(annotation));
  // Rendering malformed nested evidence must also clear partial results.
  ids.refresh.onclick(); await respond({...fixtures.create, repair: {history: [{}]}});
  assert.match(ids.status.text(), /No verdict available/);
  assert.match(ids.preview.text(), /Evidence computation failed/);
  assert.doesNotMatch(ids.preview.text(), /GOAL MET|REJECTED|BOUNDED CHECKS PASSED/);
  assert.match(ids.candidate.text(), /No verdict available/);
  assert.doesNotMatch(ids.candidate.text(), /GOAL MET|BOUNDED CHECKS PASSED|REJECTED/);
  assert.doesNotMatch(ids.outcomes.text(), /Bounded checks passed/);
  console.log('Frontend harness passed: 3 candidates × 6 steps; mobile reading order, keyboard scroll regions, pagination bounds, long annotations, selection/loading/error/race and changed-result consistency.');
})().catch(error => { console.error(error); process.exitCode = 1; });
