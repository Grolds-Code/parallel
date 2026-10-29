"""Explicit operation policies, independent of final-state goals and invariants."""
from dataclasses import dataclass
from typing import Callable

from .simulator import Simulation


@dataclass(frozen=True, slots=True)
class ActionViolation:
    constraint: str
    operation_index: int
    customer_id: str
    message: str

    def __post_init__(self) -> None:
        for name in ("constraint", "customer_id", "message"):
            if type(getattr(self, name)) is not str or not getattr(self, name).strip():
                raise ValueError(f"{name} must be a nonblank string")
        if type(self.operation_index) is not int or self.operation_index < 0:
            raise ValueError("operation_index must be a nonnegative integer")


ActionCheck = Callable[[Simulation], tuple[ActionViolation, ...]]


def forbid_original_customer_operations(simulation: Simulation) -> tuple[ActionViolation, ...]:
    """Forbid every attempted operation targeting an ID in the original snapshot.

    Includes identical replacements, failed operations, and delete/recreate
    sequences. Newly created IDs are outside this policy.
    """
    original_ids = {c.customer_id for c in simulation.initial_state.customers}
    return tuple(
        ActionViolation("original_customer_untouched", change.operation_index,
                        change.customer_id,
                        f"{change.operation.type} targets an original customer")
        for change in simulation.changes if change.customer_id in original_ids
    )
