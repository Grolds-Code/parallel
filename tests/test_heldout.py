"""All execution-path tests use in-process fakes; no live inference."""
from copy import deepcopy
import itertools
import json
import random

import pytest
from parallel_agent import heldout as h
from parallel_agent import heldout_analysis as analysis
from parallel_agent.benchmark import load_cases, parse, oracle, successful
from parallel_agent.heldout_cases import (DIRECTORY, construct, audit, roles, distance,
                                         visible_hash, gate, canonical, sha)
from parallel_agent.prompt_v3 import structured_task


@pytest.fixture
def fake_environment(monkeypatch):
    manifest, cases, rows = h.validate_freeze()
    manifest = deepcopy(manifest)
    manifest['execution_blockers'] = []
    manifest['endpoint_sha256'] = sha(b'https://example.invalid/v1')
    manifest['model_sha256'] = sha(b'offline-fake')
    monkeypatch.setattr(h, 'validate_freeze', lambda: (manifest, cases, rows))
    monkeypatch.setenv('PARALLEL_LLM_BASE_URL', 'https://example.invalid/v1')
    monkeypatch.setenv('PARALLEL_LLM_MODEL', 'offline-fake')
    monkeypatch.setenv('PARALLEL_LLM_API_KEY', 'never-serialize-secret')
    monkeypatch.setattr(analysis, 'BOOTSTRAPS', 100)
    return cases


def test_offline_reproducible_and_frozen(monkeypatch):
    def forbidden(*a, **k):
        pytest.fail('offline attempted configuration or transport')
    monkeypatch.setattr(h.LLMConfig, 'from_env', forbidden)
    first = h.run(connector_factory=forbidden)
    assert first == h.run(connector_factory=forbidden)
    assert first['requests'] == 0 and not first['model_results']
    assert first['maximum_model_calls'] == 84
    cases, _ = load_cases(DIRECTORY/'cases.json')
    assert construct() == cases
    assert sum(r['reference_gate_accepted'] for r in first['offline_feasibility']) == 24
    assert len(first['rows']) == 28


def test_selection_and_balanced_orders():
    m, cases, _ = h.validate_freeze()
    order = [c['id'] for c in construct()]
    random.Random(m['sampling_seed']).shuffle(order)
    assert m['case_order'] == order
    for family in {c['category'] for c in cases}:
        orders = [m['arm_orders'][c['id']] for c in cases if c['category'] == family]
        assert orders.count(['guided','retry']) == orders.count(['retry','guided']) == 2


def test_uniqueness_and_development_exclusion():
    cases, _ = load_cases(DIRECTORY/'cases.json')
    prior, _ = load_cases()
    assert len({visible_hash(c) for c in cases}) == 28
    for flags in (True, False):
        assert len({canonical(sorted(roles(c, flags=flags).items())) for c in cases}) == 28
    assert min(distance(roles(c, flags=False),roles(d, flags=False)) for c in cases for d in prior) > 1
    with pytest.raises(ValueError): audit(cases + [deepcopy(cases[0])])
    disguised = deepcopy(prior[-1]); disguised['task'] = 'Different wording'
    for record in disguised['initial'] + disguised['target']:
        record['customer_id'] = 'renamed'
    for op in disguised['policy']['allowed_operations']:
        op['customer']['customer_id'] = 'renamed'
    with pytest.raises(ValueError, match='development'): audit([disguised])


def test_exact_visible_hash_boundary():
    case = construct()[0]
    changed = deepcopy(case)
    for k in ('id','category','reference','expected','probe'):
        changed[k] = 'EVALUATOR_ONLY_SENTINEL'
    assert visible_hash(case) == visible_hash(changed)
    assert 'EVALUATOR_ONLY' not in json.dumps(structured_task(changed))
    changed['target'] = changed['target'][:-1]
    assert visible_hash(case) != visible_hash(changed)


def test_references_and_diagnostic_impossibility():
    cases, _ = load_cases(DIRECTORY/'cases.json')
    for c in cases:
        reference = parse(c['reference'])
        assert successful(oracle(c, reference))
        accepted = gate(c, reference).accepted_proposal is not None
        assert accepted == (c['category'] != 'counterfactual_contraction')
        if not accepted:
            removal = [op for op in c['policy']['allowed_operations'] if op['type'] == 'delete_customer']
            assert len(removal) == 1
            i = removal[0]['customer_id']
            assert i == max(r['customer_id'] for r in c['initial'])
            assert i not in {r['customer_id'] for r in c['target']}
            assert all(op == removal[0] or op['customer']['customer_id'] != i
                       for op in c['policy']['allowed_operations'])
            # All allowed operations are necessary exactly once for observed success.
            # Check every permutation for the smallest diagnostic, plus reverse for all.
            plans = itertools.permutations(reference.operations) if c['id'] == 'H25' else [tuple(reversed(reference.operations))]
            from parallel_agent.models import Proposal
            for plan in plans:
                result = gate(c, Proposal(plan))
                assert result.accepted_proposal is None
                assert result.history[0].search.witness is not None
                assert result.search_evaluations == 3


def test_shared_initial_arm_and_reference_isolation(fake_environment):
    captured = []
    cases = fake_environment
    class Fake:
        def __init__(self, config): self.config = config
        def _request(self, task, state, context):
            c = next(c for c in cases if structured_task(c) == task)
            captured.append((c['id'], deepcopy(task), deepcopy(context)))
            self.metadata = {'usage': {'prompt_tokens': 100, 'completion_tokens': 20}}
            return parse({'operations': []} if context['mode'] == 'initial' else c['reference'])
    report = h.run(execute=True, connector_factory=Fake)
    assert report['requests'] == 84
    assert report['complete']
    assert 'never-serialize-secret' not in json.dumps(report)
    for row in report['rows']:
        g,r = row['arms']['guided'], row['arms']['retry']
        assert g['initial_proposal'] == r['initial_proposal'] == row['initial']
        assert g['repair_calls'] == r['repair_calls'] == 1
        assert g['accepted'] == r['accepted'] == (row['analysis_group'] == 'satisfiable')
        assert g['search_evaluations'] <= 32 and r['search_evaluations'] <= 32
    for _, task, context in captured:
        assert set(task) == {'description','original_state','exact_target_state','full_operation_allowlist'}
        if context['mode'] == 'simple_retry':
            assert set(context) == {'mode','previous_proposal'}
    assert report['analysis']['satisfiable']['paired']['recovery']['n'] == 24
    assert report['analysis']['diagnostic']['paired']['recovery']['n'] == 0
    assert report['analysis']['physical_inference_calls'] == 84
    assert report['analysis']['physical_tokens']['prompt_tokens'] == 8400
    assert report['analysis']['satisfiable']['paired']['accepted_success']['difference'] == 0


def test_initial_acceptance_skips_both_arms(fake_environment):
    class Fake:
        def __init__(self, config): pass
        def _request(self, task, state, context):
            c = next(c for c in fake_environment if structured_task(c) == task)
            return parse(c['reference'])
    report = h.run(execute=True, connector_factory=Fake)
    assert report['requests'] == 36  # 24 + 4 * (initial + two revisions)
    assert report['analysis']['satisfiable']['paired']['recovery']['n'] == 0
    assert report['analysis']['physical_tokens']['completion_tokens'] is None
    assert all(r['arms']['guided']['disposition'] == 'repeated_proposal'
               for r in report['rows'] if r['analysis_group'] == 'diagnostic')


def test_failed_calls_charged_and_redacted(fake_environment):
    class Fake:
        def __init__(self, config): pass
        def _request(self, *a): raise TimeoutError('never-serialize-secret')
    report = h.run(execute=True, connector_factory=Fake)
    assert report['requests'] == 28
    assert report['status'] == 'incomplete'
    assert 'never-serialize-secret' not in json.dumps(report)
    primary = report['analysis']['satisfiable']['paired']['accepted_success']
    assert primary['n'] == 24 and primary['guided_mean'] == 0
    assert report['analysis']['satisfiable']['initial_unmeasured'] == 24


def test_budget_ceiling(fake_environment, monkeypatch):
    budget = h.CallBudget()
    for _ in range(84): budget.charge()
    with pytest.raises(h.BudgetExceeded): budget.charge()
    monkeypatch.setattr(h, 'MAX_CALLS', 2)
    class Fake:
        def __init__(self, config): pass
        def _request(self, *a): return parse({'operations': []})
    report = h.run(execute=True, connector_factory=Fake)
    assert report['requests'] == 2 and not report['complete']


@pytest.mark.parametrize('interrupted', [True,False])
def test_deadline_interrupt_and_clamping(fake_environment, interrupted):
    now = [0]
    class Fake:
        def __init__(self, config): self.config = config
        def _request(self, *a):
            assert self.config.timeout_seconds <= 180
            assert self.config.total_timeout_seconds <= 2400
            assert self.config.modal_result_redirects
            if interrupted: raise KeyboardInterrupt('never-serialize-secret')
            now[0] = 9601
            return parse({'operations': []})
    report = h.run(execute=True, connector_factory=Fake, clock=lambda: now[0])
    assert report['requests'] == 1
    assert report['error'] == ('interrupted' if interrupted else 'global_deadline')
    assert not report['complete']


def test_configuration_and_limits_fail_closed(fake_environment, monkeypatch):
    def forbidden(*a): pytest.fail('connector should not be constructed')
    monkeypatch.setenv('PARALLEL_LLM_MODEL', 'different')
    report = h.run(execute=True, connector_factory=forbidden)
    assert report['requests'] == 0 and report['error'] == 'configuration'
    with pytest.raises(ValueError): h.run(max_tokens=1024)
    with pytest.raises(ValueError): h.run(execute=1)


def test_checksum_tampering_detected(monkeypatch):
    from pathlib import Path
    original = Path.read_bytes
    def altered(path):
        data = original(path)
        return data + b' ' if path.name == 'cases.json' else data
    monkeypatch.setattr(Path, 'read_bytes', altered)
    with pytest.raises(ValueError, match='checksum'): h.validate_freeze()


def test_frozen_connector_wire_payload_reference_isolation(fake_environment):
    """Exercise actual V3 request serialization with a fake transport."""
    bodies = []
    class Fake(h.PilotConnector):
        def _transport(self, request):
            body = json.loads(request.data)
            bodies.append(body)
            return json.dumps({'choices':[{'finish_reason':'stop','message':{
                'role':'assistant','content':'{"operations":[]}'}}]}).encode()
    report = h.run(execute=True, connector_factory=Fake)
    assert report['requests'] == 84
    for body in bodies:
        data = json.loads(body['messages'][1]['content'])
        assert set(data) == {'task','supplied_state','state_provenance','evidence'}
        assert set(data['task']) == {'description','original_state','exact_target_state','full_operation_allowlist'}
        assert body['max_tokens'] == 512 and body['temperature'] == 0
        assert not any(k in data['task'] for k in ('reference','probe','expected','category'))
        if data['evidence']['mode'] == 'simple_retry':
            assert set(data['evidence']) == {'mode','previous_proposal'}


def test_paired_analysis_reproducible_and_known_discordance(monkeypatch):
    monkeypatch.setattr(analysis, 'BOOTSTRAPS', 1000)
    pairs = [(1,0),(1,0),(0,1),(0,0)]
    result = analysis.paired(pairs, binary=True)
    assert result == analysis.paired(pairs, binary=True)
    assert result['difference'] == .25
    assert result['guided_only'] == 2 and result['retry_only'] == 1
    assert result['mcnemar_exact_two_sided'] == 1
    assert analysis.paired([])['ci95'] is None


def test_evaluator_sentinels_and_guided_failure_do_not_cross_arms(fake_environment, monkeypatch):
    manifest, cases, rows = h.validate_freeze()
    altered = deepcopy(cases)
    for c in altered:
        c['reference'] = {'EVALUATOR_ONLY_REFERENCE': 'never-serialize-secret'}
        c['expected'] = {'EVALUATOR_ONLY_LABELS': 'never-serialize-secret'}
        c['probe'] = {'EVALUATOR_ONLY_PROBE': 'never-serialize-secret'}
    monkeypatch.setattr(h, 'validate_freeze', lambda: (manifest, altered, rows))
    captured = []
    class Fake:
        def __init__(self, config): pass
        def _request(self, task, state, context):
            captured.append((deepcopy(task), deepcopy(context)))
            if context['mode'] == 'repair':
                raise RuntimeError('never-serialize-secret')
            if context['mode'] == 'simple_retry':
                c = next(c for c in fake_environment if structured_task(c) == task)
                assert context['previous_proposal'] == {'operations': []}
                return parse(c['reference'])
            return parse({'operations': []})
    report = h.run(execute=True, connector_factory=Fake)
    assert report['requests'] == 84
    assert 'EVALUATOR_ONLY' not in json.dumps(captured)
    assert 'never-serialize-secret' not in json.dumps(report)
    primary = report['analysis']['satisfiable']['paired']['accepted_success']
    assert primary['difference'] == -1
    assert primary['retry_mean'] == 1
    assert report['analysis']['satisfiable']['paired']['recovery']['n'] == 24
