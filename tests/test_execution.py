from dataclasses import FrozenInstanceError
from itertools import product

import pytest
from hypothesis import given, settings, strategies as st

from parallel_agent import (
    Constraint, CreateCustomer, Customer, DatabaseState, DeleteCustomer, ErrorCode,
    Proposal, ReplaceCustomer, evaluate, simulate, verify,
)


def test_normal_sequence_and_trace():
    a = Customer("a")
    protected = Customer("a", open_support_case=True)
    state = DatabaseState()
    proposal = Proposal((CreateCustomer(a), ReplaceCustomer(protected)))
    result = evaluate(state, proposal)
    assert result.is_safe
    assert result.simulation.final_state == DatabaseState((protected,))
    assert result.simulation.changes[0].before is None
    assert result.simulation.changes[1].before == a
    assert state == DatabaseState()
    assert verify(simulate(state, proposal)) == result


@pytest.mark.parametrize("flags", list(product((False, True), repeat=3)))
def test_every_combination_of_protection(flags):
    record = Customer("customer", *flags)
    result = evaluate(DatabaseState((record,)), Proposal((DeleteCustomer("customer"),)))
    assert result.is_safe == (not any(flags))
    assert [v.constraint for v in result.violations] == [c for c, flag in zip(Constraint, flags) if flag]
    assert result.simulation.final_state == DatabaseState()
    for example in result.counterexamples:
        assert example.customer_id == "customer"
        assert example.operation_index == 0
        assert example.protected_record == record
        assert example.record_before_delete == record


def test_failed_operations_are_noops_and_fail_verification():
    a = Customer("a")
    proposal = Proposal((CreateCustomer(Customer("a", True)), DeleteCustomer("missing"),
                         ReplaceCustomer(Customer("missing")), DeleteCustomer("a"), DeleteCustomer("a")))
    result = evaluate(DatabaseState((a,)), proposal)
    assert not result.is_safe
    assert not result.violations
    assert [c.error for c in result.simulation.errors] == [
        ErrorCode.ALREADY_EXISTS, ErrorCode.NOT_FOUND, ErrorCode.NOT_FOUND, ErrorCode.NOT_FOUND]
    assert all(c.before == c.after for c in result.simulation.errors)
    assert result.simulation.final_state == DatabaseState()


@pytest.mark.parametrize("initially_present", [False, True])
def test_protection_cannot_be_cleared_within_proposal(initially_present):
    protected = Customer("a", True, True, True)
    state = DatabaseState((protected,)) if initially_present else DatabaseState()
    operations = [] if initially_present else [CreateCustomer(protected)]
    operations += [ReplaceCustomer(Customer("a")), DeleteCustomer("a"),
                   CreateCustomer(Customer("a")), DeleteCustomer("a")]
    result = evaluate(state, Proposal(operations))
    assert len(result.violations) == 6
    assert all(c.protected_record == protected for c in result.counterexamples)


def test_delete_then_recreate_does_not_hide_violation():
    a = Customer("a", True)
    result = evaluate(DatabaseState((a,)), Proposal((DeleteCustomer("a"), CreateCustomer(a))))
    assert result.simulation.initial_state == result.simulation.final_state
    assert not result.is_safe
    assert len(result.violations) == 1


def test_input_containers_are_detached_and_records_frozen():
    records = [Customer("b"), Customer("a")]
    operations = [DeleteCustomer("a")]
    state, proposal = DatabaseState(records), Proposal(operations)
    records.clear()
    operations.clear()
    assert [c.customer_id for c in state.customers] == ["a", "b"]
    assert len(proposal.operations) == 1
    result = evaluate(state, proposal)
    for obj, field, value in [(state, "customers", ()), (state.customers[0], "customer_id", "x"),
                              (proposal, "operations", ()), (result, "violations", ()),
                              (result.simulation.changes[0], "after", Customer("x"))]:
        with pytest.raises(FrozenInstanceError):
            setattr(obj, field, value)


@pytest.mark.parametrize("identifier", ["", "  ", None, 1])
def test_invalid_identifiers(identifier):
    with pytest.raises(ValueError):
        Customer(identifier)
    with pytest.raises(ValueError):
        DeleteCustomer(identifier)


def test_invalid_models():
    with pytest.raises(ValueError):
        DatabaseState((Customer("a"), Customer("a")))
    for construct in [lambda: Customer("a", 1), lambda: DatabaseState(({},)),
                      lambda: Proposal((object(),)), lambda: CreateCustomer({}),
                      lambda: ReplaceCustomer({})]:
        with pytest.raises(TypeError):
            construct()


def test_empty_proposal():
    state = DatabaseState((Customer("a", True),))
    result = evaluate(state, Proposal())
    assert result.is_safe
    assert not result.simulation.changes
    assert result.simulation.final_state == state
    assert result.simulation.final_state is not state


ids = st.sampled_from(["a", "b", "c", "δ", "customer-5"])
customers = st.builds(Customer, ids, st.booleans(), st.booleans(), st.booleans())
states = st.lists(customers, unique_by=lambda c: c.customer_id).map(DatabaseState)
operations = st.one_of(st.builds(CreateCustomer, customers), st.builds(ReplaceCustomer, customers),
                       st.builds(DeleteCustomer, ids))
proposals = st.lists(operations, max_size=30).map(Proposal)


@settings(max_examples=250, derandomize=True)
@given(states, proposals)
def test_deterministic_immutable_and_matches_reference(state, proposal):
    original_state, original_proposal = hash(state), hash(proposal)
    result = evaluate(state, proposal)
    assert evaluate(state, proposal) == result
    assert hash(state) == original_state
    assert hash(proposal) == original_proposal
    assert len(result.simulation.changes) == len(proposal.operations)

    # Independent list-based state machine and history-based safety oracle.
    current = list(state.customers)
    history = list(current)
    expected_violations = []
    expected_errors = []
    for index, op in enumerate(proposal.operations):
        key = op.customer_id if isinstance(op, DeleteCustomer) else op.customer.customer_id
        matching = [c for c in current if c.customer_id == key]
        error = (ErrorCode.ALREADY_EXISTS if isinstance(op, CreateCustomer) and matching else
                 ErrorCode.NOT_FOUND if not isinstance(op, CreateCustomer) and not matching else None)
        if error:
            expected_errors.append((index, error))
            continue
        if isinstance(op, DeleteCustomer):
            for rule in Constraint:
                witnesses = [c for c in history if c.customer_id == key and getattr(c, rule.value)]
                if witnesses:
                    expected_violations.append((index, key, rule, witnesses[0]))
        current = [c for c in current if c.customer_id != key]
        if not isinstance(op, DeleteCustomer):
            current.append(op.customer)
            history.append(op.customer)
    assert result.simulation.final_state == DatabaseState(current)
    assert [(c.operation_index, c.error) for c in result.simulation.errors] == expected_errors
    assert [(c.operation_index, c.customer_id, c.constraint, c.protected_record)
            for c in result.counterexamples] == expected_violations
    assert result.is_safe == (not expected_errors and not expected_violations)


@settings(max_examples=100, derandomize=True)
@given(states, st.data())
def test_storage_order_does_not_change_outcome(state, data):
    reordered = DatabaseState(data.draw(st.permutations(state.customers)))
    proposal = data.draw(proposals)
    assert evaluate(state, proposal) == evaluate(reordered, proposal)
