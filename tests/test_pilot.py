import json
import pytest
from parallel_agent import pilot
from parallel_agent.benchmark import load_cases, parse
from parallel_agent.llm import LLMConnector


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv('PARALLEL_LLM_BASE_URL', 'https://example.invalid/v1')
    monkeypatch.setenv('PARALLEL_LLM_MODEL', 'meta-llama/test-pinned')
    monkeypatch.setenv('PARALLEL_LLM_API_KEY', 'secret-do-not-log')


def test_offline_reproducible_no_connector(monkeypatch):
    def forbidden(*a, **k):
        pytest.fail('offline attempted a connection')
    monkeypatch.setattr(pilot.LLMConfig, 'from_env', forbidden)
    a = pilot.run(connector_factory=forbidden)
    assert a == pilot.run(connector_factory=forbidden)
    assert a['requests'] == 0
    assert a['case_order'] == list(pilot.CASE_IDS)


def test_hard_budget():
    budget = pilot.CallBudget()
    for _ in range(12):
        budget.charge()
    with pytest.raises(pilot.BudgetExceeded):
        budget.charge()
    assert budget.count == 12


def test_pairing_and_evidence_isolation(configured):
    cases = {c['id']: c for c in load_cases()[0]}
    captured = []
    class Fake:
        def __init__(self, config):
            self.config = config
        def _request(self, task, state, context):
            data = json.loads(task)
            case = next(c for c in cases.values() if c['task'] == data['task'] and c['initial'] == data['initial'])
            captured.append((task, state, context))
            return parse(case['probe'])
    report = pilot.run(execute=True, connector_factory=Fake)
    assert report['requests'] <= 12
    for row in report['rows']:
        assert row['arms']['guided']['initial_proposal'] == row['arms']['retry']['initial_proposal'] == row['initial']
        assert row['arms']['guided']['accepted'] == row['arms']['retry']['accepted']
    for task, state, context in captured:
        assert set(json.loads(task)) == {'task', 'initial', 'target', 'policy'}
        if context['mode'] == 'simple_retry':
            assert set(context) == {'mode', 'previous_proposal', 'instruction'}
            assert context['instruction'] == pilot.REVISION
    assert any(c[2]['mode'] == 'repair' for c in captured)
    assert any(c[2]['mode'] == 'simple_retry' for c in captured)
    assert 'secret-do-not-log' not in json.dumps(report)


@pytest.mark.parametrize('raw', [b'not json', b'{"choices":[]}', b'{"choices":[{"finish_reason":"stop","message":{"role":"assistant","content":"{\\"operations\\":[],\\"extra\\":1}"}}]}'])
def test_malformed_initial_fails_both_arms(configured, raw):
    class Fake(LLMConnector):
        def _transport(self, request):
            return raw
    report = pilot.run(execute=True, connector_factory=Fake)
    assert report['requests'] == 4
    assert all(not c['success'] for c in report['calls'])
    assert all(not a['accepted'] and a['final_oracle'] is None
               for r in report['rows'] for a in r['arms'].values())


def test_failed_calls_charged_and_sanitized(configured):
    class Fake:
        def __init__(self, config): pass
        def _request(self, *args):
            raise RuntimeError('secret-do-not-log')
    report = pilot.run(execute=True, connector_factory=Fake)
    assert report['requests'] == 4
    assert 'secret-do-not-log' not in json.dumps(report)


def test_checker_fails_closed(configured, monkeypatch):
    class Fake:
        def __init__(self, config): pass
        def _request(self, *args): return parse({'operations': []})
    def broken(*a, **k): raise RuntimeError('secret-do-not-log')
    monkeypatch.setattr(pilot, 'repair_proposal', broken)
    report = pilot.run(execute=True, connector_factory=Fake)
    assert report['requests'] == 4
    assert all(not a['accepted'] for r in report['rows'] for a in r['arms'].values())
    assert 'secret-do-not-log' not in json.dumps(report)


@pytest.mark.parametrize('kwargs', [{'max_tokens': 1025}, {'timeout': float('nan')}, {'total_timeout': 2401}, {'global_timeout': 0}])
def test_invalid_limits(kwargs):
    with pytest.raises(ValueError): pilot.run(**kwargs)


def test_all_twelve_slots_and_later_failure(configured):
    class Fake:
        def __init__(self, config): self.n = 0
        def _request(self, *args):
            self.n += 1
            return parse({'operations': [{'type': 'delete_customer', 'customer_id': f'absent-{self.n}'}]})
    report = pilot.run(execute=True, connector_factory=Fake)
    assert report['requests'] == len(report['calls']) == 12
    assert all(r['arms'][a]['repair_calls'] == 1 for r in report['rows'] for a in ('guided', 'retry'))
    class Failing(Fake):
        def _request(self, task, state, context):
            if context['mode'] != 'initial': raise TimeoutError('secret-do-not-log')
            return super()._request(task, state, context)
    report = pilot.run(execute=True, connector_factory=Failing)
    assert report['requests'] == 12
    assert sum(not c['success'] for c in report['calls']) == 8
    assert all(not a['accepted'] and a['disposition'] == 'repair_error'
               for r in report['rows'] for a in r['arms'].values())
    assert 'secret-do-not-log' not in json.dumps(report)


def test_actual_connector_polling(configured, monkeypatch):
    import io
    from urllib.error import HTTPError
    import parallel_agent.llm as llm
    requests = []
    contexts = []
    class Response(io.BytesIO):
        status = 200
    class Opener:
        def open(self, request, timeout):
            requests.append(request)
            assert 0 < timeout <= 180
            if request.get_method() == 'POST':
                contexts.append(json.loads(json.loads(request.data)['messages'][1]['content']))
            if len(requests) % 3:
                raise HTTPError(request.full_url, 303, 'secret-do-not-log',
                    {'Location': 'https://example.invalid/v1/chat/completions?secret-do-not-log'}, io.BytesIO())
            return Response(json.dumps({'id': 'secret-do-not-log',
                'usage': {'total_tokens': 7, 'api_key': 'secret-do-not-log'},
                'choices': [{'finish_reason': 'stop', 'message': {'role': 'assistant',
                'content': json.dumps({'operations': [{'type': 'delete_customer',
                    'customer_id': 'https://user:secret-do-not-log@host/?api_key=secret-do-not-log'}]})}}]}).encode())
    monkeypatch.setattr(llm, 'build_opener', lambda *a: Opener())
    report = pilot.run(execute=True)
    assert report['requests'] == 12
    assert [r.get_method() for r in requests] == ['POST', 'GET', 'GET'] * 12
    assert all(r.data is None for r in requests if r.get_method() == 'GET')
    assert all(c['usage'] == {'total_tokens': 7} for c in report['calls'])
    assert 'secret-do-not-log' not in json.dumps(report)
    for c in contexts:
        if c['mode'] == 'simple_retry':
            assert 'history' not in c and 'counterfactual_witness' not in c
    assert any(c['mode'] == 'repair' and c['history'] for c in contexts)
    assert report['rows'][0]['initial']['operations'][0]['customer_id'].startswith('id_sha256:')


@pytest.mark.parametrize('failure', ['redirect', 'timeout', 'poll_limit'])
def test_actual_connector_failures(configured, monkeypatch, failure):
    import io
    from urllib.error import HTTPError
    import parallel_agent.llm as llm
    methods = []
    class Opener:
        def open(self, request, timeout):
            methods.append(request.get_method())
            if failure == 'timeout':
                raise TimeoutError('api_key=malicious')
            location = ('https://evil.invalid/v1/chat/completions?token=malicious'
                        if failure == 'redirect' else
                        'https://example.invalid/v1/chat/completions?token=malicious')
            raise HTTPError(request.full_url, 303, 'malicious', {'Location': location}, io.BytesIO())
    monkeypatch.setattr(llm, 'build_opener', lambda *a: Opener())
    report = pilot.run(execute=True)
    assert report['requests'] == 4
    assert methods.count('POST') == 4
    assert len(methods) == (84 if failure == 'poll_limit' else 4)
    assert report['status'] == 'incomplete'
    assert all(c['oracle'] is None and not c['success'] for c in report['calls'])
    assert 'malicious' not in json.dumps(report)


@pytest.mark.parametrize('interrupt', [True, False])
def test_global_deadline_and_interruption_partial(configured, interrupt):
    now = [0.0]
    class Fake:
        def __init__(self, config): self.config = config
        def _request(self, *a):
            if interrupt:
                raise KeyboardInterrupt('secret-do-not-log')
            now[0] = 11
            return parse({'operations': []})
    report = pilot.run(execute=True, connector_factory=Fake, global_timeout=10,
                       clock=lambda: now[0])
    assert report['requests'] == 1
    assert report['error'] == ('interrupted' if interrupt else 'global_deadline')
    assert not report['complete']
    assert len(report['rows']) == 4
    assert all(not a['accepted'] and a['final_oracle'] is None
               for r in report['rows'] for a in r['arms'].values())


@pytest.mark.parametrize('which', ['goal', 'policy'])
def test_nested_checker_messages_redacted(configured, monkeypatch, which):
    class Fake:
        def __init__(self, config): pass
        def _request(self, *args): return parse({'operations': []})
    def malicious(*a): raise RuntimeError('Authorization: Bearer sk-secret https://u:p@host')
    monkeypatch.setattr(pilot, 'checks', lambda c: (
        malicious if which == 'goal' else lambda r: True,
        malicious if which == 'policy' else lambda s: ()))
    report = pilot.run(execute=True, connector_factory=Fake)
    text = json.dumps(report)
    assert 'sk-secret' not in text and 'https://' not in text and 'Authorization' not in text
    assert 'checker_error' in text
    assert report['status'] == 'incomplete'
    assert all(a['final_oracle'] is None for r in report['rows'] for a in r['arms'].values())


def test_deadline_between_arms_and_clamped_transport(configured):
    now = [0.0]
    configs = []
    class Fake:
        def __init__(self, config): self.config = config
        def _request(self, task, state, context):
            configs.append(self.config)
            if context['mode'] == 'repair': now[0] = 10
            return parse({'operations': [{'type': 'delete_customer', 'customer_id': 'absent'}]})
    report = pilot.run(execute=True, connector_factory=Fake, global_timeout=10,
                       clock=lambda: now[0])
    assert report['requests'] == 2
    assert report['error'] == 'global_deadline'
    assert all(c.timeout_seconds <= 10 and c.total_timeout_seconds <= 10 for c in configs)
    assert all(c.modal_result_redirects for c in configs)
    assert report['rows'][0]['arms']['retry']['disposition'] == 'not_completed'


def test_budget_exhaustion_partial(configured, monkeypatch):
    monkeypatch.setattr(pilot, 'MAX_CALLS', 1)
    class Fake:
        def __init__(self, config): pass
        def _request(self, *args):
            return parse({'operations': [{'type': 'delete_customer', 'customer_id': 'absent'}]})
    report = pilot.run(execute=True, connector_factory=Fake)
    assert report['requests'] == 1
    assert report['status'] == 'incomplete'
    assert all(not a['accepted'] and a['final_oracle'] is None
               for r in report['rows'] for a in r['arms'].values())


def test_partial_report_cli_nonzero(monkeypatch, capsys):
    monkeypatch.setattr(pilot, 'run', lambda **k: {
        'status': 'interrupted', 'complete': False, 'requests': 1, 'rows': []})
    assert pilot.main([]) == 1
    assert json.loads(capsys.readouterr().out)['status'] == 'interrupted'


def test_metadata_boundary_allowlist():
    report = pilot.safe_report({'usage': {'total_tokens': 'Bearer malicious',
        'prompt_tokens': 2, 'api_key': 'malicious'},
        'request_sha256': 'https://user:malicious@host',
        'constraint': 'Authorization: malicious', 'error': 'malicious'})
    assert 'malicious' not in json.dumps(report)
    assert report['usage'] == {'prompt_tokens': 2}
