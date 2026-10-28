"""Bounded, deterministic search for deletion-constraint counterexamples."""
from dataclasses import dataclass
from enum import StrEnum
from itertools import product
from typing import Iterator

from .models import Customer, DatabaseState, DeleteCustomer, Proposal
from .simulator import Change
from .verifier import Violation, evaluate


class StateProvenance(StrEnum):
    """Initial snapshot origin, not authentication of customer facts."""

    OBSERVED_STATE = "OBSERVED_STATE"
    COUNTERFACTUAL_STATE = "COUNTERFACTUAL_STATE"


@dataclass(frozen=True, slots=True)
class SearchWitness:
    """Replayable evidence with an explicitly classified initial snapshot."""
    initial_state: DatabaseState
    proposal: Proposal
    trace: tuple[Change, ...]
    violations: tuple[Violation, ...]
    cases_evaluated: int
    state_provenance: StateProvenance


@dataclass(frozen=True, slots=True)
class SearchResult:
    witness: SearchWitness | None
    cases_evaluated: int
    budget: int

    @property
    def message(self) -> str:
        if self.witness is not None:
            return "Counterexample found."
        return "No counterexample found within the budget."


def _candidate_states(state: DatabaseState, proposal: Proposal) -> Iterator[DatabaseState]:
    ids = sorted({c.customer_id for c in state.customers} | {
        op.customer_id if isinstance(op, DeleteCustomer) else op.customer.customer_id
        for op in proposal.operations
    })
    records = {c.customer_id: c for c in state.customers}
    pools = []
    for customer_id in ids:
        baseline = records.get(customer_id)
        # Baseline first; then absence, then flags in increasing bit-mask order:
        # active subscription is bit 0, refund bit 1, support case bit 2.
        alternatives = [None] + [
            Customer(customer_id, bool(mask & 1), bool(mask & 2), bool(mask & 4))
            for mask in range(8)
        ]
        pools.append((baseline, *(c for c in alternatives if c != baseline)))
    # Only the small per-ID pools are materialized, never their Cartesian product.
    # The rightmost (lexicographically greatest) ID varies fastest.
    for combination in product(*pools):
        yield DatabaseState(tuple(c for c in combination if c is not None))


def search_counterexample(
    state: DatabaseState, proposal: Proposal, *, budget: int,
) -> SearchResult:
    """Return the first constraint witness, evaluating at most ``budget`` states.

    Search the supplied snapshot first, then all presence/flag combinations for
    IDs in the snapshot or proposal, in canonical order. No new IDs are invented.
    Zero budget performs no evaluations. Stop on the first constraint violation;
    operation errors alone do not constitute a deletion-constraint witness.
    Provenance labels the supplied snapshot OBSERVED_STATE and generated
    alternatives COUNTERFACTUAL_STATE; it does not authenticate customer facts.

    A negative result means only that no counterexample was found within this
    budget and candidate domain, even when the finite domain was exhausted.
    Budget bounds simulations, not the size of an input state or proposal.
    """
    if type(budget) is not int:
        raise TypeError("budget must be an integer")
    if budget < 0:
        raise ValueError("budget must be nonnegative")
    if type(state) is not DatabaseState:
        raise TypeError("state must be a DatabaseState")
    if type(proposal) is not Proposal:
        raise TypeError("proposal must be a Proposal")

    cases_evaluated = 0
    for _, candidate in zip(range(budget), _candidate_states(state, proposal)):
        result = evaluate(candidate, proposal)
        cases_evaluated += 1
        if result.violations:
            provenance = (StateProvenance.OBSERVED_STATE if cases_evaluated == 1
                          else StateProvenance.COUNTERFACTUAL_STATE)
            witness = SearchWitness(candidate, proposal, result.simulation.changes,
                                    result.violations, cases_evaluated, provenance)
            return SearchResult(witness, cases_evaluated, budget)
    return SearchResult(None, cases_evaluated, budget)
