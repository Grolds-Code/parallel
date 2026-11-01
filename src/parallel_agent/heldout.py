"""First held-out evaluator. Offline by default; frozen v3 components are imported unchanged."""
import argparse
from dataclasses import replace
import json
import time

from .benchmark import checks, score, state, load_cases
from .llm import LLMConfig, LLMConfigurationError, _proposal, _history
from .repair import repair_proposal
from .prompt_v3 import repair_context_v3, structured_task as task
from .pilot_v3 import (PilotConnector, BudgetExceeded, ExperimentDeadline, error_category,
                       safe_report, digest, retry_context)
from .heldout_cases import DIRECTORY, ROOT, sha, visible_hash, audit
from .heldout_analysis import analyze

MAX_CALLS = 84


class CallBudget:
    def __init__(self):
        self.count = 0

    def charge(self):
        if self.count >= MAX_CALLS:
            raise BudgetExceeded('Held-out call limit reached')
        self.count += 1


def validate_freeze():
    raw = (DIRECTORY / 'manifest.json').read_bytes()
    if sha(raw) != (DIRECTORY / 'manifest.sha256').read_text().strip():
        raise ValueError('Manifest checksum mismatch')
    manifest = json.loads(raw)
    for name, expected in manifest['file_sha256'].items():
        if sha((ROOT / name).read_bytes()) != expected:
            raise ValueError('Frozen file checksum mismatch: ' + name)
    cases, checksum = load_cases(DIRECTORY / 'cases.json')
    if checksum != manifest['case_sha256']:
        raise ValueError('Frozen case checksum mismatch')
    rows = audit(cases)
    if rows != manifest['audit']:
        raise ValueError('Frozen audit mismatch')
    if len(cases) != 28 or set(manifest['case_order']) != {c['id'] for c in cases} or len(manifest['case_order']) != 28:
        raise ValueError('Invalid selection')
    indexed = {c['id']: c for c in cases}
    return manifest, [indexed[i] for i in manifest['case_order']], rows


def run(*, execute=False, max_tokens=512, timeout=180.0, total_timeout=2400.0, global_timeout=9600.0,
        connector_factory=PilotConnector, clock=time.monotonic):
    if type(execute) is not bool:
        raise ValueError('execute must be boolean')
    if (max_tokens, timeout, total_timeout, global_timeout) != (512, 180.0, 2400.0, 9600.0):
        raise ValueError('Held-out limits are frozen')
    manifest, selected, audit_rows = validate_freeze()
    report = {'protocol': 'parallel-heldout-v1', 'mode': 'execute' if execute else 'offline_dry_run',
              'model_results': execute, 'case_sha256': manifest['case_sha256'],
              'manifest_sha256': sha((DIRECTORY / 'manifest.json').read_bytes()),
              'maximum_model_calls': MAX_CALLS, 'requests': 0, 'calls': [],
              'status': 'planned', 'complete': False, 'rows': [],
              'evidence_scope': 'heldout_v1', 'offline_feasibility': audit_rows,
              'execution_blockers': manifest['execution_blockers'],
              'case_order': manifest['case_order'], 'limits': manifest['limits']}
    indexed_audit = {r['id']: r for r in audit_rows}
    for case in selected:
        row = indexed_audit[case['id']]
        report['rows'].append({'id': case['id'], 'family': case['category'],
            'task_sha256': visible_hash(case), 'analysis_group': row['analysis_group'],
            'arm_order': manifest['arm_orders'][case['id']], 'initial': None, 'arms': {}})
    if not execute:
        return report
    if manifest['execution_blockers']:
        raise ValueError('Execution blocked: serving endpoint/runtime identity is not frozen')
    started = clock()
    deadline = started + global_timeout
    def remaining():
        left = deadline - clock()
        if left <= 0:
            raise ExperimentDeadline()
        return left

    budget = CallBudget()
    try:
        config = replace(LLMConfig.from_env(), max_tokens=max_tokens, max_operations=16,
                         timeout_seconds=timeout, total_timeout_seconds=total_timeout,
                         modal_result_redirects=True, max_result_redirects=20)
        if (digest(config.model.encode()) != manifest['model_sha256'] or
                digest(config.base_url.encode()) != manifest['endpoint_sha256']):
            raise LLMConfigurationError('Frozen serving configuration mismatch')
        connector = connector_factory(config)
        report['model_sha256'] = digest(config.model.encode())
        report['endpoint_sha256'] = digest(config.base_url.encode())

        def call(case, arm, context):
            left = remaining()
            connector.config = replace(config, timeout_seconds=min(timeout, left),
                                       total_timeout_seconds=min(total_timeout, left))
            remaining()
            budget.charge()  # Before invocation, including all failed requests; never retry transport.
            entry = {'number': budget.count, 'case_id': case['id'], 'arm': arm,
                     'success': False, 'error': None, 'proposal': None,
                     'oracle': None, 'usage': None}
            report['calls'].append(entry)
            begin = clock()
            connector.metadata = {}
            try:
                proposal = connector._request(task(case), state(case['initial']), context)
                remaining()
                entry['proposal'] = _proposal(proposal)
                entry['oracle'] = score(case, proposal)
                entry['success'] = True
                return proposal
            except (Exception, KeyboardInterrupt, ExperimentDeadline) as exc:
                entry['error'] = error_category(exc)  # Never persist arbitrary exception text.
                raise
            finally:
                entry.update({k: v for k, v in connector.metadata.items() if k in
                              ('request_sha256', 'response_sha256', 'request_id_sha256', 'usage')})
                entry['elapsed_seconds'] = clock() - begin

        for case, row in zip(selected, report['rows']):
            try:
                initial = call(case, 'initial', {'mode': 'initial'})
                row['initial'] = _proposal(initial)
            except (Exception, KeyboardInterrupt, ExperimentDeadline) as exc:
                if isinstance(exc, (KeyboardInterrupt, ExperimentDeadline)):
                    raise
                row['arms'] = {a: {'accepted': False, 'disposition': 'initial_failure',
                                     'error': error_category(exc), 'final_oracle': None}
                               for a in ('guided', 'retry')}
                continue
            for arm in row['arm_order']:
                begin = clock()
                call_time_before = sum(c['elapsed_seconds'] for c in report['calls'])
                try:
                    remaining()
                    goal, policy = checks(case)
                    def revise(request):
                        context = (repair_context_v3(request, task(case)) if arm == 'guided'
                                   else retry_context(initial))
                        return call(case, arm, context)
                    result = repair_proposal(state(case['initial']), initial, revise,
                        goal=goal, action_check=policy, max_repair_attempts=1,
                        search_budget_per_iteration=16, max_search_evaluations=32)
                    remaining()
                    failed = result.disposition.value in {
                        'repair_error', 'invalid_response', 'goal_error', 'action_check_error'}
                    accepted = result.accepted_proposal is not None and not failed
                    row['arms'][arm] = {'accepted': accepted, 'disposition': result.disposition.value,
                        'initial_proposal': _proposal(initial), 'repair_calls': result.repair_attempts,
                        'final_proposal': None if failed else _proposal(result.last_proposal),
                        'final_oracle': None if failed else score(case, result.last_proposal, accepted),
                        'observed_task_success': None if failed else score(case, result.last_proposal)['task_success'],
                        'counterfactual_robust_acceptance': accepted,
                        'search_evaluations': result.search_evaluations,
                        'verified_outcomes': _history(type('History', (), {'history': result.history})()),
                        'candidate_scores': [score(case, s.proposal) for s in result.history]}
                except (Exception, KeyboardInterrupt, ExperimentDeadline) as exc:
                    if isinstance(exc, (KeyboardInterrupt, ExperimentDeadline)):
                        raise
                    row['arms'][arm] = {'accepted': False, 'disposition': 'runner_failure',
                                        'error': error_category(exc), 'final_oracle': None}
                elapsed = clock() - begin
                row['arms'][arm]['elapsed_seconds'] = elapsed
                row['arms'][arm]['verification_seconds'] = max(0, elapsed - (
                    sum(c['elapsed_seconds'] for c in report['calls']) - call_time_before))
    except (Exception, KeyboardInterrupt, ExperimentDeadline) as exc:
        report['error'] = error_category(exc)
        report['accepted'] = False
    for row in report['rows']:
        for arm in ('guided', 'retry'):
            row['arms'].setdefault(arm, {'accepted': False, 'disposition': 'not_completed',
                                        'final_oracle': None})
    for row in report['rows']:
        for outcome in row['arms'].values():
            outcome.setdefault('observed_task_success', None)
            outcome.setdefault('counterfactual_robust_acceptance', False)
    report['complete'] = ('error' not in report and
        all(a['final_oracle'] is not None for r in report['rows'] for a in r['arms'].values()))
    report['status'] = ('complete' if report['complete'] else
                        'interrupted' if report.get('error') == 'interrupted' else 'incomplete')
    report['requests'] = budget.count
    report['elapsed_seconds'] = clock() - started
    report['analysis'] = analyze(report)
    return safe_report(report)



def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true', help='Explicit live opt-in; also requires a frozen serving identity')
    args = parser.parse_args(argv)
    try:
        report = run(execute=args.execute)
    except (Exception, KeyboardInterrupt, ExperimentDeadline) as exc:
        report = {'error': error_category(exc), 'accepted': False, 'requests': 0,
                  'status': 'blocked_or_failed'}
    print(json.dumps(report, sort_keys=True, indent=2, allow_nan=False))
    return 1 if 'error' in report or report.get('status') in ('incomplete', 'interrupted') else 0


if __name__ == '__main__':
    raise SystemExit(main())
