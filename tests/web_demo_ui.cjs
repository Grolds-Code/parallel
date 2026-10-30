// Minimal DOM harness: exercises the shipped renderer offline, with real API fixtures.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
class Element {
  constructor() { this.children = []; this.attributes = {}; this.textContent = ''; this.value = 'delete'; }
  append(el) { this.children.push(el); }
  replaceChildren() { this.children = []; this.textContent = ''; }
  setAttribute(k, v) { this.attributes[k] = v; }
  removeAttribute(k) { delete this.attributes[k]; }
  text() { return this.textContent + ' ' + this.children.map(c => c.text()).join(' '); }
}
const ids = Object.fromEntries(['steps','content','outcomes','historical','previous','next','position','scenario','refresh','status'].map(id => [id, new Element()]));
const fixtures = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
let pending = [];
const context = vm.createContext({document: {getElementById: id => ids[id], createElement: () => new Element()}, fetch: url => new Promise(resolve => pending.push({url, resolve}))});
vm.runInContext(fs.readFileSync('src/parallel_agent/web_assets/app.js', 'utf8'), context);
const flush = () => new Promise(resolve => setImmediate(resolve));
async function respond(data, ok = true) { pending.shift().resolve({ok, json: async () => data}); await flush(); }
(async () => {
  await respond(fixtures.delete);
  assert.match(ids.outcomes.text(), /Unsafe · rejected/);
  assert.match(ids.outcomes.text(), /Bounded checks passed/);
  assert.match(ids.outcomes.text(), /active_subscription/);
  assert.match(ids.outcomes.text(), /original_customer_untouched/);
  assert.match(ids.historical.text(), /1 model repair attempt · 4 search evaluations · 63.73 seconds/);
  const fixed = ids.outcomes.text();
  for (const scenario of ['replacement', 'create', 'delete']) {
    ids.scenario.value = scenario; ids.scenario.onchange();
    assert.doesNotMatch(ids.outcomes.text(), /Bounded checks passed/);
    assert.match(pending[0].url, new RegExp(`scenario=${scenario}`));
    await respond(fixtures[scenario]);
    assert.equal(ids.outcomes.text(), fixed);
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
  assert.doesNotMatch(ids.outcomes.text(), /Bounded checks passed/);
  assert.equal(ids.content.text().trim(), '');
  ids.refresh.onclick(); await respond({});
  assert.match(ids.status.text(), /No verdict available/);
  ids.refresh.onclick(); ids.refresh.onclick();
  await respond(fixtures.delete); // stale request cannot restore a verdict
  assert.doesNotMatch(ids.outcomes.text(), /Bounded checks passed/);
  await respond(fixtures.create);
  assert.match(ids.outcomes.text(), /Bounded checks passed/);
  assert.equal(ids.outcomes.attributes['aria-busy'], 'false');
  // A changed API result must change the headline verdict, not keep a scripted pass.
  const altered = structuredClone(fixtures.create);
  altered.repair.disposition = 'repair_limit';
  altered.repair.history.at(-1).observed_evaluation.is_safe = false;
  altered.repair.history.at(-1).goal_met = false;
  ids.refresh.onclick(); await respond(altered);
  assert.doesNotMatch(ids.outcomes.text(), /Bounded checks passed/);
  assert.match(ids.outcomes.text(), /Not satisfied/);
  // Rendering malformed nested evidence must also clear partial results.
  ids.refresh.onclick(); await respond({...fixtures.create, repair: {history: [{}]}});
  assert.match(ids.status.text(), /No verdict available/);
  assert.doesNotMatch(ids.outcomes.text(), /Bounded checks passed/);
})().catch(error => { console.error(error); process.exitCode = 1; });
