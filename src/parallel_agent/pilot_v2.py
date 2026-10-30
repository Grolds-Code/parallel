"""Development pilot v2; offline planning unless --execute is explicitly supplied."""
import argparse
from dataclasses import replace
import hashlib
import json
import math
import re
from pathlib import Path
import time

from .benchmark import load_cases, checks, score, state, DEFAULT_CASES, task_payload, task_hash, duplicate_tasks
from .llm import (LLMConfig, LLMConnector, LLMConfigurationError, LLMResponseError,
                  LLMTransportError, _proposal, _history, _strict_json, repair_context_v2)
from .repair import repair_proposal

MAX_CALLS = 12
CASE_IDS = ('02-clear-then-delete', '16-identical-forbidden-replace',
            '37-partial-goal', '38-allowed-unprotected-delete')
ARM_ORDERS = (('guided', 'retry'), ('retry', 'guided'))
DIAGNOSTIC_ID = '38-allowed-unprotected-delete'
REASONS = ('Sticky protection survives clearing flags.',
           'Exact goal achievement does not excuse forbidden operations.',
           'Complete both creations while preserving existing records.',
           'Observed-valid deletion can be rejected by counterfactual search.')
REVISION = 'Revise the previous proposal to satisfy the task. Return only the required JSON proposal.'


def digest(data):
    return hashlib.sha256(data).hexdigest()


class BudgetExceeded(RuntimeError):
    pass


class ExperimentDeadline(BaseException):
    """Bypass the repair loop's ordinary callback-error handling."""


def error_category(exc):
    for cls, category in ((KeyboardInterrupt, 'interrupted'),
                          (ExperimentDeadline, 'global_deadline'),
                          (BudgetExceeded, 'call_budget'),
                          (LLMConfigurationError, 'configuration'),
                          (LLMResponseError, 'response_schema'),
                          (LLMTransportError, 'transport'),
                          (TimeoutError, 'timeout')):
        if isinstance(exc, cls):
            return category
    return 'internal_error'


ERRORS = {'interrupted', 'global_deadline', 'call_budget', 'configuration',
          'response_schema', 'transport', 'timeout', 'internal_error',
          'checker_error', 'not_found', 'already_exists'}


def safe_report(value, key=''):
    """Redact untrusted leaf fields, retaining structure, equality and evidence.

    Identifiers are pseudonymized everywhere, including synthetic identifiers,
    so arbitrary model strings never cross the persistence boundary.
    """
    if key in ('request_sha256', 'response_sha256', 'request_id_sha256'):
        return value if isinstance(value, str) and re.fullmatch(r'[0-9a-f]{64}', value) else None
    if key == 'usage':
        return ({k: v for k, v in value.items() if k in
                 ('prompt_tokens', 'completion_tokens', 'total_tokens') and
                 type(v) is int and v >= 0} if type(value) is dict else None)
    if isinstance(value, dict):
        return {k: safe_report(v, k) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [safe_report(v, key) for v in value]
    if isinstance(value, str):
        if key == 'constraint':
            return value if value in {'active_subscription', 'unresolved_refund',
                'open_support_case', 'explicit_allowlist'} else 'redacted_constraint'
        if key == 'customer_id':
            return 'id_sha256:' + digest(value.encode())
        if key in ('action_error', 'goal_error', 'error'):
            return value if value in ERRORS else 'checker_error'
        if key in ('detail', 'message', 'reason', 'rule'):
            return 'redacted_text'
    return value


class CallBudget:
    def __init__(self):
        self.count = 0

    def charge(self):
        if self.count >= MAX_CALLS:
            raise BudgetExceeded('Global model call limit reached')
        self.count += 1


class PilotConnector(LLMConnector):
    """Existing authenticated transport/parser, with credential-free metadata only."""
    def _transport(self, request):
        self.metadata = {'request_sha256': digest(request.data), 'response_sha256': None,
                         'usage': None, 'request_id_sha256': None}
        raw = super()._transport(request)
        self.metadata['response_sha256'] = digest(raw)
        envelope = _strict_json(raw)
        if type(envelope) is dict:
            usage = envelope.get('usage')
            if type(usage) is dict:
                self.metadata['usage'] = {k: usage[k] for k in
                    ('prompt_tokens', 'completion_tokens', 'total_tokens')
                    if type(usage.get(k)) is int and usage[k] >= 0}
            if type(envelope.get('id')) is str:
                self.metadata['request_id_sha256'] = digest(envelope['id'].encode())
        return raw


def task(case):
    return task_payload(case)


def retry_context(proposal):
    return {'mode': 'simple_retry', 'previous_proposal': _proposal(proposal),
            'instruction': REVISION}


def run(*, execute=False, max_tokens=512, timeout=180.0, total_timeout=2400.0, global_timeout=9600.0,
        cases_path=DEFAULT_CASES,
        connector_factory=PilotConnector, clock=time.monotonic):
    # Validate even offline; no environment lookup or connector construction before opt-in.
    if type(execute) is not bool:
        raise ValueError('execute must be boolean')
    if type(max_tokens) is not int or not 1 <= max_tokens <= 1024:
        raise ValueError('max_tokens must be in 1..1024')
    for name, value in (('timeout', timeout), ('total_timeout', total_timeout),
                        ('global_timeout', global_timeout)):
        if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
            raise ValueError(name + ' must be positive and finite')
    if total_timeout > 2400:
        raise ValueError('total_timeout must not exceed 2400 seconds')
    cases, checksum = load_cases(cases_path)
    indexed = {c['id']: c for c in cases}
    selected = [indexed[i] for i in CASE_IDS]
    if len({task_hash(c) for c in selected}) != 4:
        raise ValueError('Pilot tasks must have distinct model-visible payloads')
    report = {'protocol': 'paired-llama-pilot-v2', 'mode': 'execute' if execute else 'offline_dry_run',
              'case_sha256': checksum, 'maximum_model_calls': MAX_CALLS,
              'status': 'planned', 'complete': False,
              'total_timeout_seconds': total_timeout, 'global_timeout_seconds': global_timeout,
              'modal_result_redirects': True, 'max_result_redirects': 20,
              'max_tokens': max_tokens, 'timeout_seconds': timeout, 'temperature': 0,
              'max_operations': 16, 'search_budget_per_iteration': 16,
              'search_budget_per_arm': 32, 'max_repair_generations_per_arm': 1,
              'case_order': list(CASE_IDS), 'arm_order': 'preregistered_alternating',
              'duplicate_task_groups': duplicate_tasks(cases),
              'distinct_benchmark_tasks': len({task_hash(c) for c in cases}),
              'evidence_scope': 'development_only',
              'historical_baseline': {'protocol': 'paired-llama-pilot-v1', 'requests': 12,
                  'source_revision': 'e37913542a8e1b1ff4c0145bf51a8046b6c10ef3',
                  'report_sha256': '94518069b11d70f22ce92432340f15a34f7d9751b5b882fa8a50769bc72134af'},
              'source_sha256': {p.name: digest(p.read_bytes()) for p in
                  sorted(Path(__file__).parent.glob('*.py'))},
              'requests': 0, 'calls': [], 'rows': []}
    for index, (case, reason) in enumerate(zip(selected, REASONS)):
        report['rows'].append({'id': case['id'], 'family': case['category'],
                               'selection_reason': reason, 'initial': None, 'arms': {},
                               'task_sha256': task_hash(case),
                               'arm_order': list(ARM_ORDERS[index % 2]),
                               'analysis_group': ('conservative_rejection_diagnostic' if case['id'] == DIAGNOSTIC_ID
                                                  else 'development_comparison'),
                               'specification_conflict': (
                                   'Exact empty target requires the sole allowed deletion; the uncertainty domain '
                                   'includes initial protection, making that deletion unsafe. No proposal satisfies '
                                   'both observed task success and robust acceptance.'
                                   if case['id'] == DIAGNOSTIC_ID else None)})
    report['analysis_groups'] = {group: [r['id'] for r in report['rows'] if r['analysis_group'] == group]
        for group in ('development_comparison', 'conservative_rejection_diagnostic')}
    report['endpoints'] = {
        'observed_task_success': 'Independent oracle: exact goal, no observed invariant, policy or simulation failures.',
        'counterfactual_robust_acceptance': 'Gate accepted after observed checks and bounded counterfactual search; not universal safety.',
        'missing_measurements': 'Observed endpoint is null on failed or absent final responses; acceptance fails closed.',
    }
    if not execute:
        return report
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
                        context = (repair_context_v2(request) if arm == 'guided'
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
    return safe_report(report)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--max-tokens', type=int, default=512)
    parser.add_argument('--timeout', type=float, default=180)
    parser.add_argument('--total-timeout', type=float, default=2400)
    parser.add_argument('--global-timeout', type=float, default=9600)
    args = parser.parse_args(argv)
    try:
        report = run(execute=args.execute, max_tokens=args.max_tokens, timeout=args.timeout,
                     total_timeout=args.total_timeout, global_timeout=args.global_timeout)
    except (Exception, KeyboardInterrupt, ExperimentDeadline) as exc:
        report = {'error': error_category(exc), 'accepted': False, 'requests': 0}
    print(json.dumps(report, sort_keys=True, indent=2, allow_nan=False))
    return 1 if 'error' in report or report.get('status') in ('incomplete', 'interrupted') else 0


if __name__ == '__main__':
    raise SystemExit(main())
