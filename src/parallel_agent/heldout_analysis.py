"""Preregistered paired descriptive analysis; no development data is pooled."""
from collections import Counter
import math
import random

SEED = 20261031
BOOTSTRAPS = 10000


def paired(values, *, binary=False):
    """Paired percentile bootstrap with deterministic resampling of task pairs."""
    n = len(values)
    if not n:
        return {'n': 0, 'difference': None, 'ci95': None}
    diffs = [g-r for g,r in values]
    rng = random.Random(SEED)
    samples = sorted(sum(rng.choices(diffs, k=n))/n for _ in range(BOOTSTRAPS))
    result = {'n': n, 'guided_mean': sum(g for g,r in values)/n,
              'retry_mean': sum(r for g,r in values)/n,
              'difference': sum(diffs)/n,
              'ci95': [samples[max(0, int(BOOTSTRAPS*.025)-1)], samples[int(BOOTSTRAPS*.975)-1]]}
    if binary:
        wins = sum(g > r for g,r in values)
        losses = sum(g < r for g,r in values)
        discordant = wins + losses
        result.update(guided_only=wins, retry_only=losses,
            mcnemar_exact_two_sided=min(1.0, 2*sum(math.comb(discordant,k)
                for k in range(min(wins,losses)+1))/(2**discordant)) if discordant else 1.0)
    return result


def analyze(report):
    if not report['model_results']:
        return None
    calls = report['calls']
    def group(rows):
        endpoints = {k: [] for k in ('accepted_success', 'observed_success', 'false_acceptance',
            'repeated_proposal', 'inference_calls', 'prompt_tokens', 'completion_tokens',
            'search_evaluations', 'elapsed_seconds', 'verification_seconds', 'recovery')}
        missing = Counter()
        dispositions = {a: Counter() for a in ('guided','retry')}
        for row in rows:
            values = {}
            for arm in ('guided','retry'):
                outcome = row['arms'][arm]
                oracle = outcome.get('final_oracle')
                own = [c for c in calls if c['case_id'] == row['id'] and c['arm'] == arm]
                shared = [c for c in calls if c['case_id'] == row['id'] and c['arm'] == 'initial']
                dispositions[arm][outcome['disposition']] += 1
                measured = own + shared
                values[arm] = {
                    'accepted_success': int(bool(outcome['accepted'] and oracle and oracle['task_success'])),
                    'observed_success': None if oracle is None else int(oracle['task_success']),
                    'false_acceptance': int(bool(outcome['accepted'] and oracle and oracle['false_acceptance'])),
                    'repeated_proposal': int(outcome['disposition'] == 'repeated_proposal'),
                    'inference_calls': len(own) + len(shared)/2,
                    'search_evaluations': outcome.get('search_evaluations'),
                    'elapsed_seconds': outcome.get('elapsed_seconds'),
                    'verification_seconds': outcome.get('verification_seconds')}
                for token in ('prompt_tokens', 'completion_tokens'):
                    values[arm][token] = (sum(c['usage'][token] * (0.5 if c['arm'] == 'initial' else 1)
                        for c in measured) if measured and all(c.get('usage') is not None and
                        token in c['usage'] for c in measured) else None)
            for key in endpoints:
                if key == 'recovery':
                    # Both histories independently assess the same initial. Missing initial != rejection.
                    histories = [row['arms'][a].get('verified_outcomes', []) for a in ('guided','retry')]
                    available = [history[0] for history in histories if history]
                    if not available:
                        continue
                    # Either arm's initial evidence classifies the shared proposal.
                    # A later runner failure in its pair must not erase eligibility.
                    if not any(step['failure_kinds'] for step in available):
                        continue
                    if row['analysis_group'] != 'satisfiable':
                        continue
                    pair = tuple(values[a]['accepted_success'] for a in ('guided','retry'))
                else:
                    pair = tuple(values[a][key] for a in ('guided','retry'))
                if any(v is None for v in pair):
                    missing[key] += 1
                else:
                    endpoints[key].append(pair)
        accepted = {a: sum(r['arms'][a]['accepted'] for r in rows) for a in ('guided','retry')}
        false = {a: sum(bool(r['arms'][a].get('final_oracle', {}) and
                       r['arms'][a]['final_oracle']['false_acceptance']) for r in rows)
                 for a in ('guided','retry')}
        return {'tasks': len(rows), 'accepted_counts': accepted,
                'false_acceptance_among_accepted': {a: false[a]/accepted[a] if accepted[a] else None
                                                     for a in accepted},
                'initial_unmeasured': sum(r['initial'] is None for r in rows), 'paired': {k: paired(v, binary=k in {
            'accepted_success','observed_success','false_acceptance','repeated_proposal','recovery'})
            for k,v in endpoints.items()}, 'missing_pairs': dict(missing),
            'dispositions': {a: dict(v) for a,v in dispositions.items()}}
    main = [r for r in report['rows'] if r['analysis_group'] == 'satisfiable']
    diagnostics = [r for r in report['rows'] if r['analysis_group'] == 'diagnostic']
    family_diffs = []
    for family in sorted({r['family'] for r in main}):
        pairs = []
        for r in main:
            if r['family'] == family:
                pairs.append(tuple(int(bool(r['arms'][a]['accepted'] and
                    r['arms'][a].get('final_oracle') and r['arms'][a]['final_oracle']['task_success']))
                    for a in ('guided','retry')))
        family_diffs.append((sum(g-r for g,r in pairs)/len(pairs), 0))
    latencies = sorted(c['elapsed_seconds'] for c in calls)
    return {'primary_family_cluster_sensitivity': paired(family_diffs),
            'physical_tokens': {k: sum(c['usage'][k] for c in calls) if calls and
                all(c.get('usage') is not None and k in c['usage'] for c in calls) else None
                for k in ('prompt_tokens','completion_tokens')},
            'initial_call_latency_seconds': sum(c['elapsed_seconds'] for c in calls if c['arm'] == 'initial'),
            'call_latency_median_seconds': (latencies[(len(latencies)-1)//2] + latencies[len(latencies)//2])/2 if latencies else None,
            'call_latency_p95_seconds': latencies[math.ceil(len(latencies)*.95)-1] if latencies else None,
            'satisfiable': group(main), 'diagnostic': group(diagnostics),
            'families': {f: group([r for r in report['rows'] if r['family'] == f])
                         for f in sorted({r['family'] for r in report['rows']})},
            'physical_inference_calls': len(calls),
            'call_errors': dict(Counter(c['error'] for c in calls if c.get('error'))),
            'usage_complete_calls': sum(c.get('usage') is not None for c in calls),
            'call_latency_total_seconds': sum(c['elapsed_seconds'] for c in calls)}
