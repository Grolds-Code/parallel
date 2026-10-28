"""Deletion constraints and concrete witnesses for a future repair loop."""
from dataclasses import dataclass
from enum import StrEnum

from .models import Customer, DatabaseState, DeleteCustomer, Proposal
from .simulator import Simulation, simulate


class Constraint(StrEnum):
    ACTIVE_SUBSCRIPTION = "active_subscription"
    UNRESOLVED_REFUND = "unresolved_refund"
    OPEN_SUPPORT_CASE = "open_support_case"


@dataclass(frozen=True, slots=True)
class Counterexample:
    operation_index: int
    customer_id: str
    protected_record: Customer
    record_before_delete: Customer
    constraint: Constraint


@dataclass(frozen=True, slots=True)
class Violation:
    constraint: Constraint
    customer_id: str
    operation_index: int
    counterexample: Counterexample


@dataclass(frozen=True, slots=True)
class VerificationResult:
    simulation: Simulation
    violations: tuple[Violation, ...]

    @property
    def counterexamples(self) -> tuple[Counterexample, ...]:
        return tuple(v.counterexample for v in self.violations)

    @property
    def is_safe(self) -> bool:
        """Whether this simulation has neither violations nor operation errors.

        Scoped to this state and proposal, not real-world execution approval.
        """
        return not self.violations and not self.simulation.errors


def verify(simulation: Simulation) -> VerificationResult:
    """Protect any record observed with a protection flag during this proposal.

    "Observed" means encountered in the simulation, including hypothetical
    input records; it does not establish real-world facts.
    Protection is sticky within a proposal: clearing flags or recreating an ID
    cannot authorize a later deletion. Each rule emits its earliest witness.
    """
    protected: dict[tuple[str, Constraint], Customer] = {}

    def observe(record: Customer | None) -> None:
        if record is not None:
            for constraint in Constraint:
                if getattr(record, constraint.value):
                    protected.setdefault((record.customer_id, constraint), record)

    for record in simulation.initial_state.customers:
        observe(record)
    violations: list[Violation] = []
    for change in simulation.changes:
        if change.error is not None:
            continue
        observe(change.before)
        if isinstance(change.operation, DeleteCustomer) and change.before is not None:
            for constraint in Constraint:
                witness = protected.get((change.customer_id, constraint))
                if witness is not None:
                    example = Counterexample(change.operation_index, change.customer_id,
                                             witness, change.before, constraint)
                    violations.append(Violation(constraint, change.customer_id,
                                                change.operation_index, example))
        observe(change.after)
    return VerificationResult(simulation, tuple(violations))


def evaluate(state: DatabaseState, proposal: Proposal) -> VerificationResult:
    """Trusted entry point: simulate a proposal and verify its complete trace."""
    return verify(simulate(state, proposal))
