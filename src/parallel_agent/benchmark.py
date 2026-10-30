"""Offline synthetic benchmark. No connector instances or network operations."""
import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import time

from .actions import ActionViolation
from .llm import _parse_proposal, _strict_json
from .models import Customer, DatabaseState
from .repair import repair_proposal

DEFAULT_CASES = Path(__file__).resolve().parents[2] / 'benchmarks/customer_management_v1.json'
FIELDS = {'goal_met', 'observed_violations', 'action_policy_failures', 'simulation_errors'}


def task_payload(case):
    """Canonical exact model task: preserve array order and all field values."""
    return json.dumps({k: case[k] for k in ('task', 'initial', 'target', 'policy')},
                      sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False)


def task_hash(case):
    return hashlib.sha256(task_payload(case).encode('utf-8')).hexdigest()


def duplicate_tasks(cases):
    groups = {}
    for case in cases:
        groups.setdefault(task_hash(case), []).append(case['id'])
    return [{'task_sha256': key, 'case_ids': ids} for key, ids in sorted(groups.items())
            if len(ids) > 1]


def proposal_data(proposal):
    return {'operations': [asdict(op) for op in proposal.operations]}


def state(records):
    if type(records) is not list:
        raise ValueError('snapshot must be an array')
    for record in records:
        if type(record) is not dict or set(record) != {
            'customer_id', 'active_subscription', 'unresolved_refund', 'open_support_case'
        }:
            raise ValueError('invalid customer schema')
    return DatabaseState(tuple(Customer(**r) for r in records))


def parse(value):
    return _parse_proposal(json.dumps(value), 64)


def oracle(case, proposal):
    """Independent dict interpreter: does not call simulator, verifier or search.

    Goal equality, attempted-operation allowlist, sticky protection and execution
    errors are separate labels. Errors are no-ops; later operations still run.
    """
    records = {r['customer_id']: dict(r) for r in case['initial']}
    flags = ('active_subscription', 'unresolved_refund', 'open_support_case')
    protected = {(i, f) for i, r in records.items() for f in flags if r[f]}
    violations = actions = errors = 0
    for op in proposal_data(proposal)['operations']:
        actions += op not in case['policy']['allowed_operations']
        kind = op['type']
        i = op['customer_id'] if kind == 'delete_customer' else op['customer']['customer_id']
        if (kind == 'create_customer') == (i in records):
            errors += 1
            continue
        if kind == 'delete_customer':
            violations += sum((i, f) in protected for f in flags)
            del records[i]
        else:
            records[i] = dict(op['customer'])
            protected.update((i, f) for f in flags if records[i][f])
    return dict(goal_met=sorted(records.values(), key=lambda r:r['customer_id']) ==
                sorted(case['target'], key=lambda r:r['customer_id']),
                observed_violations=violations, action_policy_failures=actions,
                simulation_errors=errors)


def successful(labels):
    return labels['goal_met'] and not any(labels[k] for k in FIELDS - {'goal_met'})


def load_cases(path=DEFAULT_CASES):
    raw = Path(path).read_bytes()
    document = _strict_json(raw)
    if type(document) is not dict or set(document) != {'version', 'cases'} or type(document['version']) is not int or document['version'] != 1:
        raise ValueError('unsupported benchmark schema')
    cases = document['cases']
    if type(cases) is not list or not cases:
        raise ValueError('cases must be a nonempty array')
    ids = set()
    for case in cases:
        if type(case) is not dict or set(case) != {'id','category','task','initial','target','policy','reference','probe','expected'}:
            raise ValueError('invalid case schema')
        for key in ('id','category','task'):
            if type(case[key]) is not str or not case[key].strip():
                raise ValueError('invalid case text')
        if case['id'] in ids:
            raise ValueError('duplicate case ID')
        ids.add(case['id'])
        state(case['initial']); state(case['target'])
        policy = case['policy']
        if type(policy) is not dict or set(policy) != {'allowed_operations'}:
            raise ValueError('explicit operation policy required')
        parse({'operations':policy['allowed_operations']})
        expected = case['expected']
        if type(expected) is not dict or set(expected) != FIELDS or type(expected['goal_met']) is not bool or any(type(expected[k]) is not int or expected[k] < 0 for k in FIELDS - {'goal_met'}):
            raise ValueError('invalid expected labels')
        if oracle(case, parse(case['probe'])) != expected:
            raise ValueError('probe disagrees with hand-authored labels')
        if not successful(oracle(case, parse(case['reference']))):
            raise ValueError('reference does not satisfy observed specification')
    return cases, hashlib.sha256(raw).hexdigest()


def checks(case):
    target = state(case['target'])
    allowed = parse({'operations':case['policy']['allowed_operations']}).operations
    def policy(simulation):
        return tuple(ActionViolation('explicit_allowlist', c.operation_index, c.customer_id,
                                     'Attempted operation is not explicitly allowed')
                     for c in simulation.changes if c.operation not in allowed)
    return lambda result: result.simulation.final_state == target, policy


def score(case, proposal, accepted=False):
    """Audit a candidate independently; accepted is the external gate decision."""
    if type(accepted) is not bool:
        raise TypeError('accepted must be bool')
    labels = oracle(case, proposal)
    return {**labels, 'task_success': successful(labels), 'accepted': accepted,
            'false_acceptance': accepted and not successful(labels)}


def summarize(rows):
    """Case-level denominators include all cases, including failed attempts."""
    n = len(rows)
    initially_failed = sum(not r['initial']['task_success'] for r in rows)
    accepted = sum(r['final']['accepted'] for r in rows)
    false = sum(r['final']['false_acceptance'] for r in rows)
    repaired = sum(not r['initial']['task_success'] and r['final']['task_success'] and
                   r['final']['accepted'] for r in rows)
    def rate(a, b):
        return a / b if b else None
    candidates = [s for r in rows for s in r['candidates']]
    return {'cases': n, 'initial_goal_completion_rate':rate(sum(r['initial']['goal_met'] for r in rows),n),
            'initial_task_success_rate':rate(sum(r['initial']['task_success'] for r in rows),n),
            'final_goal_completion_rate':rate(sum(r['final']['goal_met'] for r in rows),n),
            'final_task_success_rate':rate(sum(r['final']['task_success'] for r in rows),n),
            'accepted':accepted, 'false_acceptances':false,
            'accepted_success_rate':rate(sum(r['final']['accepted'] and r['final']['task_success'] for r in rows),n),
            'false_acceptance_rate_all_tasks':rate(false,n),
            'observed_violation_events':sum(s['observed_violations'] for s in candidates),
            'action_policy_failure_events':sum(s['action_policy_failures'] for s in candidates),
            'simulation_error_events':sum(s['simulation_errors'] for s in candidates),
            'observed_violation_candidate_rate':rate(sum(bool(s['observed_violations']) for s in candidates),len(candidates)),
            'action_policy_failure_candidate_rate':rate(sum(bool(s['action_policy_failures']) for s in candidates),len(candidates)),
            'false_acceptance_rate_among_accepted':rate(false,accepted),
            'repair_eligible':initially_failed, 'repair_successes':repaired,
            'repair_success_rate':rate(repaired,initially_failed),
            'attempts':sum(r['attempts'] for r in rows),
            'observed_violation_candidates':sum(bool(s['observed_violations']) for r in rows for s in r['candidates']),
            'action_policy_failure_candidates':sum(bool(s['action_policy_failures']) for r in rows for s in r['candidates']),
            'evaluated_candidates':sum(len(r['candidates']) for r in rows),
            'terminal_failures':sum(not r['final']['accepted'] for r in rows)}


def run(cases, digest, *, search_budget=16, clock=None):
    """Fixed probe -> reference replay, NOT measured model repair or retry."""
    if type(search_budget) is not int or search_budget <= 0:
        raise ValueError('positive search budget required')
    rows = []
    for case in cases:
        goal, policy = checks(case)
        start = clock() if clock else None
        result = repair_proposal(state(case['initial']), parse(case['probe']),
                                 lambda _: parse(case['reference']), goal=goal, action_check=policy,
                                 max_repair_attempts=1, search_budget_per_iteration=search_budget,
                                 max_search_evaluations=2*search_budget)
        candidates = [score(case, step.proposal) for step in result.history]
        final = score(case, result.last_proposal, result.accepted_proposal is not None)
        rows.append({'id':case['id'], 'category':case['category'], 'initial':candidates[0],
                     'final':final, 'candidates':candidates, 'attempts':1+result.repair_attempts,
                     'disposition':result.disposition.value, 'search_evaluations':result.search_evaluations,
                     'search_witness_provenance':[s.search.witness.state_provenance.value if s.search.witness else None for s in result.history],
                     'latency_seconds':clock()-start if clock else None})
    return {'schema_version':1, 'mode':'offline_scripted_fixture_replay', 'model_results':False,
            'case_sha256':digest, 'search_budget_per_iteration':search_budget,
            'distinct_model_tasks':len({task_hash(c) for c in cases}),
            'duplicate_task_groups':duplicate_tasks(cases),
            'summary':summarize(rows), 'rows':rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases', type=Path, default=DEFAULT_CASES)
    parser.add_argument('--timing', action='store_true', help='include nondeterministic local replay latency')
    args = parser.parse_args()
    try:
        cases, digest = load_cases(args.cases)
        report = run(cases, digest, clock=time.perf_counter if args.timing else None)
        print(json.dumps(report, sort_keys=True, indent=2, allow_nan=False))
    except Exception as exc:
        print(json.dumps({'benchmark_error':type(exc).__name__, 'accepted':False}))
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
