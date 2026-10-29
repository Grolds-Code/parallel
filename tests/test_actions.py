import pytest

from parallel_agent import (
    ActionViolation, CreateCustomer, Customer, DatabaseState, DeleteCustomer,
    Disposition, FailureKind, Proposal, ReplaceCustomer, evaluate,
    forbid_original_customer_operations, repair_proposal, simulate, verify,
)
from parallel_agent.llm import _history

ORIGINAL = Customer("demo-001")
NEW = Customer("demo-002")
STATE = DatabaseState((ORIGINAL,))
CREATE = CreateCustomer(NEW)
INCIDENT = Proposal((CREATE, ReplaceCustomer(ORIGINAL)))
VALID = Proposal((CREATE,))


def goal(e):
    return e.simulation.final_state == DatabaseState((ORIGINAL, NEW))


def run(proposal, repair=lambda _: VALID, **kwargs):
    return repair_proposal(
        STATE, proposal, repair, goal=goal,
        search_budget_per_iteration=2, max_search_evaluations=20, **kwargs,
    )


def test_live_incident_and_create_only_repair():
    # Final-state equality alone accepts the reported live proposal.
    assert run(INCIDENT).disposition is Disposition.PASSED_BOUNDED_CHECKS
    requests = []

    def repair(request):
        requests.append(request)
        evidence = request.evidence
        assert evidence.goal_met is True
        assert evidence.failures == (FailureKind.ACTION_VIOLATION,)
        violation, = evidence.observed_evaluation.action_violations
        assert violation.operation_index == 1
        assert violation.customer_id == "demo-001"
        assert "replace_customer" in violation.message
        assert _history(request)[0]["action_violations"][0]["operation_index"] == 1
        return VALID

    result = run(INCIDENT, repair, action_check=forbid_original_customer_operations)
    assert result.disposition is Disposition.PASSED_BOUNDED_CHECKS
    assert result.accepted_proposal == VALID
    assert result.repair_attempts == 1
    assert result.search_evaluations == 4
    assert len(requests) == 1
    rejected = run(INCIDENT, action_check=forbid_original_customer_operations,
                   max_repair_attempts=0)
    assert rejected.accepted_proposal is None


@pytest.mark.parametrize("operations", [
    (ReplaceCustomer(ORIGINAL), ReplaceCustomer(ORIGINAL)),
    (ReplaceCustomer(Customer("demo-001", True)), ReplaceCustomer(ORIGINAL)),
    (DeleteCustomer("demo-001"), CreateCustomer(ORIGINAL)),
    (DeleteCustomer("demo-001"), DeleteCustomer("demo-001"), CreateCustomer(ORIGINAL)),
    (CreateCustomer(ORIGINAL), ReplaceCustomer(ORIGINAL)),
])
def test_every_attempt_checked_against_original_snapshot(operations):
    proposal = Proposal((CREATE, *operations))
    evaluation = evaluate(STATE, proposal, action_check=forbid_original_customer_operations)
    assert evaluation == verify(simulate(STATE, proposal),
                                action_check=forbid_original_customer_operations)
    assert not evaluation.is_safe
    assert [v.operation_index for v in evaluation.action_violations] == list(
        range(1, len(operations) + 1))
    assert run(proposal, action_check=forbid_original_customer_operations,
               max_repair_attempts=0).accepted_proposal is None


def test_new_records_are_not_original_and_policy_persists_across_repairs():
    proposal = Proposal((CREATE, ReplaceCustomer(NEW)))
    assert run(proposal, action_check=forbid_original_customer_operations).accepted_proposal == proposal
    result = run(Proposal(), lambda _: INCIDENT,
                 action_check=forbid_original_customer_operations, max_repair_attempts=1)
    assert result.accepted_proposal is None
    assert result.history[-1].observed_evaluation.action_violations


def broken(_):
    raise RuntimeError("checker failed")


@pytest.mark.parametrize("checker", [
    broken, lambda _: False, lambda _: None, lambda _: [], lambda _: ("bad",),
    lambda _: (ActionViolation("rule", 20, "demo-001", "bad index"),),
    lambda _: (ActionViolation("rule", 0, "wrong-id", "bad ID"),),
])
def test_checker_errors_fail_closed(checker):
    result = run(VALID, lambda _: pytest.fail("checker errors must terminate"),
                 action_check=checker)
    assert result.disposition is Disposition.ACTION_CHECK_ERROR
    assert result.accepted_proposal is None
    evidence = result.history[0].observed_evaluation
    assert evidence.action_error
    assert not evidence.is_safe
    assert result.history[0].failures == (FailureKind.ACTION_CHECK_ERROR,)


def test_custom_operation_policy_is_independent_of_goal_and_state_rules():
    def forbid_create(simulation):
        return tuple(ActionViolation("no_create", c.operation_index, c.customer_id,
                                     "Creation forbidden")
                     for c in simulation.changes if isinstance(c.operation, CreateCustomer))

    result = run(VALID, action_check=forbid_create, max_repair_attempts=0)
    assert result.history[0].goal_met
    assert not result.history[0].observed_evaluation.violations
    assert result.accepted_proposal is None
