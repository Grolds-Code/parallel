from dataclasses import FrozenInstanceError
from itertools import product

import pytest

import parallel_agent.search as search_module
from parallel_agent.search import StateProvenance
from parallel_agent import (
    Constraint, CreateCustomer, Customer, DatabaseState, DeleteCustomer,
    Proposal, ReplaceCustomer, evaluate, search_counterexample,
)


@pytest.mark.parametrize("flags", list(product((False, True), repeat=3))[1:])
def test_initial_unsafe_state_and_replay(flags):
    state = DatabaseState((Customer("a", *flags),))
    proposal = Proposal((ReplaceCustomer(Customer("a")), DeleteCustomer("a"),
                         CreateCustomer(Customer("a")), DeleteCustomer("a")))
    result = search_counterexample(state, proposal, budget=100)
    witness = result.witness
    assert witness is not None
    assert witness.state_provenance is StateProvenance.OBSERVED_STATE
    assert witness.initial_state == state
    assert witness.proposal == proposal
    assert witness.cases_evaluated == result.cases_evaluated == 1
    replay = evaluate(witness.initial_state, witness.proposal)
    assert witness.trace == replay.simulation.changes
    assert witness.violations == replay.violations
    assert len(witness.violations) == 2 * sum(flags)
    assert result.message == "Counterexample found."
    assert search_counterexample(state, proposal, budget=100) == result
    with pytest.raises(FrozenInstanceError):
        witness.cases_evaluated = 3
    with pytest.raises(FrozenInstanceError):
        result.budget = 0


@pytest.mark.parametrize("state", [DatabaseState(), DatabaseState((Customer("a"),))])
def test_alternative_protection_found_for_missing_or_unprotected_customer(state):
    proposal = Proposal((DeleteCustomer("a"),))
    assert not evaluate(state, proposal).violations
    result = search_counterexample(state, proposal, budget=3)
    assert result.witness.state_provenance is StateProvenance.COUNTERFACTUAL_STATE
    replay = evaluate(result.witness.initial_state, result.witness.proposal)
    assert replay.simulation.changes == result.witness.trace
    assert replay.violations == result.witness.violations
    assert search_counterexample(state, proposal, budget=100).witness == result.witness
    assert result.cases_evaluated == 3
    assert result.witness.initial_state == DatabaseState((Customer("a", True),))
    assert [v.constraint for v in result.witness.violations] == [Constraint.ACTIVE_SUBSCRIPTION]


def test_proposal_can_introduce_protection():
    proposal = Proposal((CreateCustomer(Customer("a", unresolved_refund=True)),
                         DeleteCustomer("a")))
    result = search_counterexample(DatabaseState(), proposal, budget=1)
    assert result.witness.state_provenance is StateProvenance.OBSERVED_STATE
    assert result.witness.violations[0].constraint == Constraint.UNRESOLVED_REFUND


@pytest.mark.parametrize("proposal", [Proposal(), Proposal((CreateCustomer(Customer("a")),)),
                                     Proposal((ReplaceCustomer(Customer("a", True)),))])
def test_no_constraint_counterexample_does_not_claim_safety(proposal):
    result = search_counterexample(DatabaseState((Customer("a"),)), proposal, budget=100)
    assert result.witness is None
    assert result.cases_evaluated == 9
    assert result.message == "No counterexample found within the budget."
    assert not hasattr(result, "is_safe")


def test_exact_order_complete_domain_and_reordered_input(monkeypatch):
    observed = []

    def recording_evaluate(state, proposal):
        observed.append(state)
        return evaluate(state, proposal)

    monkeypatch.setattr(search_module, "evaluate", recording_evaluate)
    a, b = Customer("a", unresolved_refund=True), Customer("b")
    state = DatabaseState((b, a))
    result = search_counterexample(state, Proposal(), budget=1000)
    first_run = observed[:]
    assert result.cases_evaluated == len(set(observed)) == 81
    assert observed[0] == state
    assert observed[1] == DatabaseState((a,))
    assert observed[2] == DatabaseState((a, Customer("b", True)))
    assert observed[3] == DatabaseState((a, Customer("b", unresolved_refund=True)))
    assert observed[9] == DatabaseState((b,))
    expected = {None} | {Customer("b", *flags) for flags in product((False, True), repeat=3)}
    assert {s.get("b") for s in observed} == expected
    observed.clear()
    assert search_counterexample(DatabaseState((a, b)), Proposal(), budget=1000) == result
    assert observed == first_run


@pytest.mark.parametrize("budget", [0, 1, 2, 3])
def test_budget_boundary_and_actual_evaluation_count(monkeypatch, budget):
    calls = []

    def recording_evaluate(state, proposal):
        calls.append(state)
        return evaluate(state, proposal)

    monkeypatch.setattr(search_module, "evaluate", recording_evaluate)
    result = search_counterexample(DatabaseState(), Proposal((DeleteCustomer("a"),)), budget=budget)
    assert result.cases_evaluated == len(calls) == budget
    assert (result.witness is not None) == (budget == 3)
    if budget < 3:
        assert result.message == "No counterexample found within the budget."


def test_large_domain_small_budget_and_unchanged_inputs():
    state = DatabaseState(tuple(Customer(str(i)) for i in range(100)))
    proposal = Proposal((ReplaceCustomer(Customer("new")),))
    before = hash(state), hash(proposal)
    result = search_counterexample(state, proposal, budget=2)
    assert result.cases_evaluated == 2
    assert result.witness is None
    assert (hash(state), hash(proposal)) == before


def test_empty_domain_and_large_integer_budget():
    result = search_counterexample(DatabaseState(), Proposal(), budget=10**100)
    assert result.cases_evaluated == 1
    assert result.witness is None


@pytest.mark.parametrize("budget", [True, False, 1.5, "3", None])
def test_invalid_budget_types(budget):
    with pytest.raises(TypeError, match="budget"):
        search_counterexample(DatabaseState(), Proposal(), budget=budget)


def test_budget_required_and_nonnegative():
    with pytest.raises(TypeError):
        search_counterexample(DatabaseState(), Proposal())
    with pytest.raises(ValueError, match="budget"):
        search_counterexample(DatabaseState(), Proposal(), budget=-1)


@pytest.mark.parametrize("state, proposal", [(None, Proposal()), (DatabaseState(), None)])
def test_invalid_inputs(state, proposal):
    with pytest.raises(TypeError):
        search_counterexample(state, proposal, budget=0)


def test_increasing_budgets_preserve_candidate_prefix(monkeypatch):
    observed = []

    def recording_evaluate(state, proposal):
        observed.append(state)
        return evaluate(state, proposal)

    monkeypatch.setattr(search_module, "evaluate", recording_evaluate)
    state = DatabaseState((Customer("a"), Customer("b")))
    search_counterexample(state, Proposal(), budget=7)
    prefix = observed[:]
    observed.clear()
    search_counterexample(state, Proposal(), budget=15)
    assert observed[:7] == prefix


def test_zero_budget_does_not_generate_candidates(monkeypatch):
    def unexpected_candidates(*args):
        raise AssertionError("candidate generation must remain lazy")
        yield

    monkeypatch.setattr(search_module, "_candidate_states", unexpected_candidates)
    assert search_counterexample(DatabaseState(), Proposal(), budget=0).cases_evaluated == 0


@pytest.mark.parametrize("budget", [0, 1, 2, 100])
def test_observed_violation_budget_and_early_stop(monkeypatch, budget):
    state = DatabaseState((Customer("a", True),))
    proposal = Proposal((DeleteCustomer("a"),))
    calls = []

    def recording_evaluate(candidate, operations):
        calls.append(candidate)
        return evaluate(candidate, operations)

    monkeypatch.setattr(search_module, "evaluate", recording_evaluate)
    result = search_counterexample(state, proposal, budget=budget)
    assert result.cases_evaluated == len(calls) == min(budget, 1)
    assert calls == ([state] if budget else [])
    if budget:
        assert result.witness.state_provenance is StateProvenance.OBSERVED_STATE
    else:
        assert result.witness is None


def test_counterfactual_does_not_claim_observed_snapshot_is_unsafe():
    state = DatabaseState((Customer("a"),))
    proposal = Proposal((DeleteCustomer("a"),))
    assert evaluate(state, proposal).is_safe
    result = search_counterexample(state, proposal, budget=3)
    assert result.witness.state_provenance is StateProvenance.COUNTERFACTUAL_STATE
    assert not evaluate(result.witness.initial_state, proposal).is_safe
    assert evaluate(state, proposal).is_safe


def test_errors_without_violations_do_not_claim_safety():
    state = DatabaseState()
    proposal = Proposal((ReplaceCustomer(Customer("a")),))
    evaluation = evaluate(state, proposal)
    assert evaluation.simulation.errors
    assert not evaluation.violations
    assert not evaluation.is_safe
    result = search_counterexample(state, proposal, budget=100)
    assert result.cases_evaluated == 9
    assert result.witness is None
    assert result.message == "No counterexample found within the budget."
    assert not hasattr(result, "is_safe")


def test_observed_witness_replays_errors_and_violations():
    state = DatabaseState((Customer("a", True),))
    proposal = Proposal((DeleteCustomer("missing"), DeleteCustomer("a")))
    witness = search_counterexample(state, proposal, budget=1).witness
    assert witness.state_provenance is StateProvenance.OBSERVED_STATE
    replay = evaluate(witness.initial_state, witness.proposal)
    assert replay.simulation.errors
    assert replay.violations == witness.violations
    assert replay.simulation.changes == witness.trace
    assert not replay.is_safe
