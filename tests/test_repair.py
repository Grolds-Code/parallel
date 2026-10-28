import pytest

from parallel_agent import (
    CreateCustomer, Customer, DatabaseState, DeleteCustomer, Disposition,
    FailureKind, Proposal, ReplaceCustomer, StateProvenance, evaluate, repair_proposal,
)

STATE = DatabaseState((Customer("a", True),))
BAD = Proposal((DeleteCustomer("a"),))
GOOD = Proposal((CreateCustomer(Customer("b")),))


def run(repair, **kwargs):
    return repair_proposal(STATE, BAD, repair, search_budget_per_iteration=10,
                           max_search_evaluations=100, **kwargs)


def test_success_evidence_original_state_and_replay():
    def repair_with_goal(request):
        assert request.evidence.failures == (FailureKind.OBSERVED_VIOLATION,
                                             FailureKind.GOAL_NOT_MET)
        return GOOD

    result = run(repair_with_goal, goal=lambda e: e.simulation.final_state.get("b") is not None)
    assert result.disposition is Disposition.PASSED_BOUNDED_CHECKS
    assert result.accepted_proposal == GOOD
    assert result.repair_attempts == 1
    assert len(result.history) == 2
    assert result.history[-1].observed_evaluation.simulation.final_state.get("a") == STATE.get("a")
    for step in result.history:
        assert step.observed_evaluation == evaluate(STATE, step.proposal)
        if step.search.witness:
            w = step.search.witness
            replay = evaluate(w.initial_state, w.proposal)
            assert w.trace == replay.simulation.changes
            assert w.violations == replay.violations
    assert result == run(repair_with_goal, goal=lambda e: e.simulation.final_state.get("b") is not None)


def test_repetition_and_cycles():
    assert run(lambda _: BAD).disposition is Disposition.REPEATED_PROPOSAL
    other = Proposal((DeleteCustomer("missing"),))
    result = run(lambda r: other if r.attempt == 1 else BAD)
    assert result.disposition is Disposition.REPEATED_PROPOSAL
    assert result.last_proposal == BAD
    assert len(result.history) == 2
    assert result.accepted_proposal is None


def test_unique_failures_exhaust_attempts():
    result = run(lambda r: Proposal((DeleteCustomer(str(r.attempt)),)), max_repair_attempts=2)
    assert result.disposition is Disposition.REPAIR_LIMIT
    assert result.repair_attempts == 2
    assert len(result.history) == 3
    assert result.history[-1].failures == (FailureKind.SIMULATION_ERROR,)
    assert not result.history[-1].observed_evaluation.violations


def test_counterfactual_not_observed_fact():
    state = DatabaseState((Customer("a"),))
    seen = []

    def repair(request):
        seen.append(request.evidence)
        return GOOD

    result = repair_proposal(state, BAD, repair, search_budget_per_iteration=3,
                             max_search_evaluations=6)
    evidence = seen[0]
    assert evidence.observed_evaluation.is_safe
    assert evidence.failures == (FailureKind.COUNTERFACTUAL_VULNERABILITY,)
    assert evidence.search.witness.state_provenance is StateProvenance.COUNTERFACTUAL_STATE
    assert state.get("a").active_subscription is False
    assert result.disposition is Disposition.PASSED_BOUNDED_CHECKS


@pytest.mark.parametrize("noop", [Proposal(), Proposal((ReplaceCustomer(Customer("a", True)),))])
def test_goal_rejects_abandonment(noop):
    result = run(lambda _: noop, max_repair_attempts=1,
                 goal=lambda e: e.simulation.final_state.get("a") is None)
    assert result.disposition is Disposition.REPAIR_LIMIT
    assert result.history[-1].failures == (FailureKind.GOAL_NOT_MET,)
    assert result.accepted_proposal is None


@pytest.mark.parametrize("response", [None, {}, [], "safe", True, {"proposal": GOOD}])
def test_invalid_responses(response):
    result = run(lambda _: response)
    assert result.disposition is Disposition.INVALID_RESPONSE
    assert result.accepted_proposal is None
    assert result.repair_attempts == 1


def test_callback_exceptions_and_invalid_goal():
    def broken(_):
        raise RuntimeError("offline failure")
    assert run(broken).disposition is Disposition.REPAIR_ERROR
    for goal in (broken, lambda _: "yes"):
        result = run(lambda _: GOOD, goal=goal)
        assert result.disposition is Disposition.GOAL_ERROR
        assert result.repair_attempts == 0
        assert result.history[0].goal_error


@pytest.mark.parametrize("total, per", [(0, 3), (2, 3), (10, 0)])
def test_no_search_allowance_fails_closed(total, per):
    result = repair_proposal(STATE, GOOD, lambda _: pytest.fail("unexpected callback"),
                             max_search_evaluations=total, search_budget_per_iteration=per)
    assert result.disposition is Disposition.SEARCH_BUDGET_EXHAUSTED
    assert result.history == ()
    assert result.search_evaluations == 0


def test_budget_exhausted_after_failure_and_counts(monkeypatch):
    import parallel_agent.search as search
    calls = []
    original = search.evaluate

    def counted(state, proposal):
        calls.append((state, proposal))
        return original(state, proposal)

    monkeypatch.setattr(search, "evaluate", counted)
    result = repair_proposal(DatabaseState((Customer("a"),)), BAD,
                             lambda _: pytest.fail("no budget for repair"),
                             search_budget_per_iteration=3, max_search_evaluations=3)
    assert result.disposition is Disposition.SEARCH_BUDGET_EXHAUSTED
    assert result.search_evaluations == len(calls) == 3
    assert result.repair_attempts == 0


def test_bounded_pass_is_not_universal_safety():
    result = repair_proposal(DatabaseState((Customer("a"),)), BAD, lambda _: BAD,
                             search_budget_per_iteration=1, max_search_evaluations=1)
    assert result.disposition is Disposition.PASSED_BOUNDED_CHECKS
    assert "not universal safety" in result.detail
    assert not hasattr(result, "is_safe")


@pytest.mark.parametrize("name", ["max_repair_attempts", "search_budget_per_iteration",
                                  "max_search_evaluations"])
@pytest.mark.parametrize("value, error", [(True, TypeError), (1.2, TypeError), (-1, ValueError)])
def test_invalid_limits(name, value, error):
    with pytest.raises(error):
        repair_proposal(STATE, BAD, lambda _: GOOD, **{name: value})
