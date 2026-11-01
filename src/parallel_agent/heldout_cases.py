"""Offline construction and novelty audit for the first held-out freeze.

Construction is explicit and deterministic; it never calls a model.
"""
from collections import Counter
import hashlib
import json
from pathlib import Path

from .benchmark import load_cases, oracle, parse, checks, state, successful
from .prompt_v3 import structured_task
from .repair import repair_proposal

ROOT = Path(__file__).resolve().parents[2]
DIRECTORY = ROOT / 'benchmarks/heldout_v1'
FLAGS = ('active_subscription', 'unresolved_refund', 'open_support_case')


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False)


def sha(value):
    return hashlib.sha256(value).hexdigest()


def visible_hash(case):
    # Includes exactly the task facts passed to V3Connector, preserving array order.
    return sha(canonical(structured_task(case)).encode())


def roles(case, *, flags=True):
    """Identifier/order/wording invariant multiset of complete per-record roles.

    Operations only act on one ID, so this retains all cross-snapshot linkage.
    The coarse form removes Boolean values but retains operation multiplicity.
    """
    initial = {r['customer_id']: r for r in case['initial']}
    target = {r['customer_id']: r for r in case['target']}
    ops = case['policy']['allowed_operations']
    ids = set(initial) | set(target) | {
        op.get('customer_id', op.get('customer', {}).get('customer_id')) for op in ops}
    def mask(record):
        if record is None:
            return None
        return tuple(record[f] for f in FLAGS) if flags else 'present'
    result = []
    for i in ids:
        allowed = sorted(canonical((op['type'], mask(op.get('customer')))) for op in ops
                         if op.get('customer_id', op.get('customer', {}).get('customer_id')) == i)
        result.append(canonical((mask(initial.get(i)), mask(target.get(i)),
                                 initial.get(i) == target.get(i), allowed)))
    return Counter(result)


def distance(a, b):
    # Minimum insert/delete/substitute record roles: a substitution costs one.
    return max(sum((a-b).values()), sum((b-a).values()))


def gate(case, proposal):
    goal, policy = checks(case)
    return repair_proposal(state(case['initial']), proposal, lambda _: None,
        goal=goal, action_check=policy, max_repair_attempts=0,
        search_budget_per_iteration=16, max_search_evaluations=32)


def construct():
    cases = []
    def record(i, mask):
        return dict(customer_id=i, **{f: bool(mask & (1 << j)) for j, f in enumerate(FLAGS)})
    def operation(kind, r):
        return {'type': kind + '_customer', 'customer': r}
    families = ('flag_reconciliation', 'mixed_expansion', 'staged_onboarding',
                'optional_action_selection', 'mixed_staged_expansion', 'bulk_preservation')
    for family in families:
        for variant in range(4):
            prefix = f'{family[:3]}{variant}'
            initial = [record(prefix + '-keep0', 0), record(prefix + '-keep1', 7)]
            target = list(initial)
            plan, allowed = [], []
            def replace_many(n):
                for k in range(n):
                    old = record(f'{prefix}-edit{k}', (k + variant + 1) % 8)
                    new = record(old['customer_id'], (k + variant + 4) % 8)
                    initial.append(old); target.append(new)
                    plan.append(operation('replace', new))
            def create_many(n, staged=False):
                for k in range(n):
                    new = record(f'{prefix}-add{k}', (k + variant + 2) % 8)
                    target.append(new)
                    if staged:
                        intermediate = record(new['customer_id'], (k + variant + 5) % 8)
                        plan.extend([operation('create', intermediate), operation('replace', new)])
                    else:
                        plan.append(operation('create', new))
            if family == 'flag_reconciliation':
                replace_many(3 + variant)
            elif family == 'mixed_expansion':
                replace_many(1 + variant); create_many(2)
            elif family == 'staged_onboarding':
                create_many(2 + variant, True)
            elif family == 'optional_action_selection':
                replace_many(3 + variant)
                allowed.extend([operation('replace', record(initial[0]['customer_id'], 3)),
                                operation('create', record(prefix + '-optional', 6))])
            elif family == 'mixed_staged_expansion':
                replace_many(2); create_many(1 + variant, True)
                extra = record(prefix + '-direct', 4)
                target.append(extra); plan.append(operation('create', extra))
            else:
                create_many(3 + variant)
                initial.append(record(prefix + '-keep2', 2)); target.append(initial[-1])
            allowed.extend(reversed(plan))  # Deliberately not a reference sequence.
            cases.append(dict(id=f'H{len(cases)+1:02d}', category=family,
                task='Reach the exact target snapshot using only the exhaustive operation allowlist. '
                     'Preserve all target records and their exact Boolean flags. Allowed actions are optional; '
                     'choose and order the actions needed. Protection remains sticky throughout a proposal.',
                initial=initial, target=target, policy={'allowed_operations': allowed},
                reference={'operations': plan}, probe={'operations': []},
                expected={'goal_met': False, 'observed_violations': 0,
                          'action_policy_failures': 0, 'simulation_errors': 0}))
    for variant in range(4):
        prefix = f'diag{variant}'
        initial = [record(f'{prefix}-edit{k}', k % 8) for k in range(3 + variant)]
        target = [record(r['customer_id'], (k+4) % 8) for k,r in enumerate(initial)]
        initial += [record(prefix + '-keep', 7), record(prefix + '-zremove', 0)]
        target += [initial[-2]]
        plan = [operation('replace', r) for r in target[:-1]] + [
            {'type': 'delete_customer', 'customer_id': initial[-1]['customer_id']}]
        cases.append(dict(id=f'H{len(cases)+1:02d}', category='counterfactual_contraction',
            task='Reconcile the retained records to the exact target flags and remove the surplus record. '
                 'Use only the exhaustive allowlist; protection remains sticky throughout the proposal.',
            initial=initial, target=target, policy={'allowed_operations': list(reversed(plan))},
            reference={'operations': plan}, probe={'operations': []},
            expected={'goal_met': False, 'observed_violations': 0,
                      'action_policy_failures': 0, 'simulation_errors': 0}))
    return cases


def audit(cases):
    development, _ = load_cases()
    hashes = [visible_hash(c) for c in cases]
    if len(hashes) != len(set(hashes)):
        raise ValueError('duplicate visible task')
    exact_roles = [canonical(sorted(roles(c).items())) for c in cases]
    coarse_roles = [canonical(sorted(roles(c, flags=False).items())) for c in cases]
    if len(set(exact_roles)) != len(cases) or len(set(coarse_roles)) != len(cases):
        raise ValueError('identifier/flag-only variant within held-out tasks')
    rows = []
    for c in cases:
        distances = [distance(roles(c, flags=False), roles(d, flags=False)) for d in development]
        if visible_hash(c) in {visible_hash(d) for d in development} or min(distances) <= 1:
            raise ValueError('development duplicate or close variant')
        reference = parse(c['reference'])
        if not successful(oracle(c, reference)):
            raise ValueError('observed reference infeasible')
        result = gate(c, reference)
        diagnostic = c['category'] == 'counterfactual_contraction'
        if (result.accepted_proposal is not None) == diagnostic:
            raise ValueError('gate feasibility disagrees with preregistered group')
        rows.append({'id': c['id'], 'family': c['category'], 'task_sha256': visible_hash(c),
                     'analysis_group': 'diagnostic' if diagnostic else 'satisfiable',
                     'observed_reference_success': True, 'reference_gate_accepted': not diagnostic,
                     'reference_search_evaluations': result.search_evaluations,
                     'nearest_development_role_distance': min(distances),
                     'nearest_development_ids': [d['id'] for d,n in zip(development, distances) if n == min(distances)]})
    return rows
