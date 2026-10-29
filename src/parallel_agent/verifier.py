"""Deletion constraints and concrete witnesses for a future repair loop."""
from dataclasses import dataclass
from enum import StrEnum

from .actions import ActionCheck, ActionViolation
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
    action_violations: tuple[ActionViolation, ...] = ()
    action_error: str | None = None

    @property
    def counterexamples(self) -> tuple[Counterexample, ...]:
        return tuple(v.counterexample for v in self.violations)

    @property
    def is_safe(self) -> bool:
        """Whether this simulation has neither violations nor operation errors.

        Scoped to this state and proposal, not real-world execution approval.
        """
        return (not self.violations and not self.simulation.errors
                and not self.action_violations and self.action_error is None)


def verify(simulation: Simulation, *, action_check: ActionCheck | None = None) -> VerificationResult:
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
    action_violations = ()
    action_error = None
    if action_check is not None:
        try:
            action_violations = action_check(simulation)
            if type(action_violations) is not tuple or any(
                    type(v) is not ActionViolation for v in action_violations):
                raise TypeError("action_check must return a tuple of ActionViolation")
            for violation in action_violations:
                if (violation.operation_index >= len(simulation.changes) or
                        simulation.changes[violation.operation_index].customer_id !=
                        violation.customer_id):
                    raise ValueError("action violation must identify an attempted operation")
        except Exception as exc:
            action_violations = ()
            action_error = f"{type(exc).__name__}: {exc}"
    return VerificationResult(simulation, tuple(violations), action_violations, action_error)


def evaluate(state: DatabaseState, proposal: Proposal, *,
             action_check: ActionCheck | None = None) -> VerificationResult:
    """Trusted entry point: simulate a proposal and verify its complete trace."""
    return verify(simulate(state, proposal), action_check=action_check)
