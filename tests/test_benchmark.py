import copy
import json
import socket

import pytest

from parallel_agent import benchmark as b
from parallel_agent.verifier import evaluate


def test_construction_and_expected_outcomes():
    cases, digest = b.load_cases()
    assert len(cases) == 40 and len(digest) == 64
    assert len({c['category'] for c in cases}) >= 12
    masks = {tuple(c['initial'][0][f] for f in ('active_subscription','unresolved_refund','open_support_case'))
             for c in cases if c['category'] == 'protected-delete'}
    assert len(masks) == 7
    for case in cases:
        goal, policy = b.checks(case)
        for name in ('probe','reference'):
            proposal = b.parse(case[name])
            observed = evaluate(b.state(case['initial']), proposal, action_check=policy)
            labels = b.oracle(case, proposal)
            assert labels == dict(goal_met=goal(observed), observed_violations=len(observed.violations),
                                  action_policy_failures=len(observed.action_violations),
                                  simulation_errors=len(observed.simulation.errors))
            if name == 'probe':
                assert labels == case['expected']
            else:
                assert b.successful(labels)


def test_reproducible_and_offline(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('network access')
    monkeypatch.setattr(socket, 'socket', forbidden)
    cases, digest = b.load_cases()
    first = b.run(cases,digest)
    assert first == b.run(cases,digest)
    assert first['model_results'] is False
    assert all(r['latency_seconds'] is None for r in first['rows'])
    deletion = next(r for r in first['rows'] if r['category']=='allowed-unprotected-delete')
    assert deletion['initial']['task_success']
    assert not deletion['final']['accepted']
    assert deletion['search_witness_provenance'] == ['COUNTERFACTUAL_STATE']
    assert first['summary']['false_acceptances'] == 0


def test_oracle_independent_and_false_acceptance(monkeypatch):
    cases, _ = b.load_cases()
    monkeypatch.setattr('parallel_agent.verifier.evaluate', lambda *a, **kw: pytest.fail('oracle used verifier'))
    case = next(c for c in cases if c['category']=='identical-forbidden-replace')
    score = b.score(case,b.parse(case['probe']),True)
    assert score['goal_met'] and score['false_acceptance'] and not score['task_success']


def test_metric_denominators():
    cases, _ = b.load_cases()
    case = cases[0]
    bad = b.score(case,b.parse(case['probe']))
    good = b.score(case,b.parse(case['reference']),True)
    false = b.score(case,b.parse(case['probe']),True)
    rows = [dict(initial=bad,final=good,candidates=[bad,good],attempts=2),
            dict(initial=good,final=false,candidates=[good,false],attempts=2)]
    summary = b.summarize(rows)
    assert summary['repair_eligible']==1 and summary['repair_success_rate']==1
    assert summary['false_acceptance_rate_among_accepted']==0.5
    assert summary['initial_goal_completion_rate']==0.5
    assert summary['observed_violation_candidates']==2
    assert summary['attempts']==4 and summary['evaluated_candidates']==4
    assert b.summarize([])['repair_success_rate'] is None
    assert b.summarize([])['false_acceptance_rate_among_accepted'] is None


@pytest.mark.parametrize('damage', ['policy','target','expected','reference','duplicate','unknown'])
def test_invalid_cases_fail_closed(tmp_path,damage):
    cases,_=b.load_cases()
    cases=copy.deepcopy(cases)
    if damage=='policy': cases[0]['policy']={}
    elif damage=='target': cases[0]['target'][0]['active_subscription']=1
    elif damage=='expected': cases[0]['expected']['observed_violations']=0
    elif damage=='reference': cases[0]['reference']={'operations':[]}
    elif damage=='duplicate': cases.append(cases[0])
    else: cases[0]['surprise']=True
    path=tmp_path/'cases.json'
    path.write_text(json.dumps({'version':1,'cases':cases}))
    with pytest.raises((ValueError,TypeError)):
        b.load_cases(path)


def test_budget_and_checker_fail_closed(monkeypatch):
    cases,digest=b.load_cases()
    for budget in (0,-1,True,1.5):
        with pytest.raises(ValueError): b.run(cases,digest,search_budget=budget)
    def broken(_): raise RuntimeError('policy unavailable')
    monkeypatch.setattr(b,'checks',lambda _: (lambda _: True, broken))
    report=b.run(cases[:1],digest)
    assert report['rows'][0]['disposition']=='action_check_error'
    assert not report['rows'][0]['final']['accepted']


def test_cli_failure_has_no_success_report(monkeypatch,capsys,tmp_path):
    path=tmp_path/'broken.json'
    path.write_text('{"version":1,"version":1,"cases":[]}')
    monkeypatch.setattr('sys.argv',['benchmark','--cases',str(path)])
    assert b.main()==1
    assert json.loads(capsys.readouterr().out)['accepted'] is False


def test_timing_opt_in():
    cases,digest=b.load_cases()
    ticks=iter([10.0,10.25])
    assert b.run(cases[:1],digest,clock=lambda:next(ticks))['rows'][0]['latency_seconds']==0.25
