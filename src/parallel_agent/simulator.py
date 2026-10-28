"""Pure simulation; unsafe operations are modeled, never executed externally."""
from dataclasses import dataclass
from enum import StrEnum

from .models import CreateCustomer, Customer, DatabaseState, DeleteCustomer, Operation, Proposal


class ErrorCode(StrEnum):
    ALREADY_EXISTS = "already_exists"
    NOT_FOUND = "not_found"


@dataclass(frozen=True, slots=True)
class Change:
    operation_index: int
    operation: Operation
    customer_id: str
    before: Customer | None
    after: Customer | None
    error: ErrorCode | None = None


@dataclass(frozen=True, slots=True)
class Simulation:
    initial_state: DatabaseState
    proposal: Proposal
    final_state: DatabaseState
    changes: tuple[Change, ...]

    @property
    def errors(self) -> tuple[Change, ...]:
        return tuple(change for change in self.changes if change.error is not None)


def simulate(state: DatabaseState, proposal: Proposal) -> Simulation:
    """Apply operations in order to a private copy. Failed operations are no-ops.

    Subsequent operations still run, allowing complete diagnostics. Safety is
    deliberately assessed separately so unsafe proposals yield counterexamples.
    """
    records = {customer.customer_id: customer for customer in state.customers}
    changes: list[Change] = []
    for index, operation in enumerate(proposal.operations):
        customer_id = (operation.customer_id if isinstance(operation, DeleteCustomer)
                       else operation.customer.customer_id)
        before = records.get(customer_id)
        error = None
        if isinstance(operation, CreateCustomer) and before is not None:
            error = ErrorCode.ALREADY_EXISTS
        elif not isinstance(operation, CreateCustomer) and before is None:
            error = ErrorCode.NOT_FOUND
        if error is None:
            if isinstance(operation, DeleteCustomer):
                del records[customer_id]
            else:
                records[customer_id] = operation.customer
        changes.append(Change(index, operation, customer_id, before, records.get(customer_id), error))
    return Simulation(state, proposal, DatabaseState(tuple(records.values())), tuple(changes))
