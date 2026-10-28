"""Immutable, validated inputs for deterministic execution."""
from dataclasses import dataclass, field
from typing import TypeAlias


def _identifier(value: str) -> None:
    if type(value) is not str or not value.strip():
        raise ValueError("customer_id must be a nonblank string")


@dataclass(frozen=True, slots=True)
class Customer:
    customer_id: str
    active_subscription: bool = False
    unresolved_refund: bool = False
    open_support_case: bool = False

    def __post_init__(self) -> None:
        _identifier(self.customer_id)
        for name in ("active_subscription", "unresolved_refund", "open_support_case"):
            if type(getattr(self, name)) is not bool:
                raise TypeError(f"{name} must be bool")


@dataclass(frozen=True, slots=True)
class DatabaseState:
    customers: tuple[Customer, ...] = ()

    def __post_init__(self) -> None:
        records = tuple(self.customers)
        if any(type(record) is not Customer for record in records):
            raise TypeError("customers must contain Customer records")
        if len({record.customer_id for record in records}) != len(records):
            raise ValueError("duplicate customer_id")
        object.__setattr__(self, "customers", tuple(sorted(records, key=lambda c: c.customer_id)))

    def get(self, customer_id: str) -> Customer | None:
        return next((c for c in self.customers if c.customer_id == customer_id), None)


@dataclass(frozen=True, slots=True)
class CreateCustomer:
    customer: Customer
    type: str = field(default="create_customer", init=False)

    def __post_init__(self) -> None:
        if type(self.customer) is not Customer:
            raise TypeError("customer must be a Customer")


@dataclass(frozen=True, slots=True)
class ReplaceCustomer:
    customer: Customer
    type: str = field(default="replace_customer", init=False)

    def __post_init__(self) -> None:
        if type(self.customer) is not Customer:
            raise TypeError("customer must be a Customer")


@dataclass(frozen=True, slots=True)
class DeleteCustomer:
    customer_id: str
    type: str = field(default="delete_customer", init=False)

    def __post_init__(self) -> None:
        _identifier(self.customer_id)


Operation: TypeAlias = CreateCustomer | ReplaceCustomer | DeleteCustomer


@dataclass(frozen=True, slots=True)
class Proposal:
    operations: tuple[Operation, ...] = ()

    def __post_init__(self) -> None:
        operations = tuple(self.operations)
        if any(type(op) not in (CreateCustomer, ReplaceCustomer, DeleteCustomer) for op in operations):
            raise TypeError("unsupported operation")
        object.__setattr__(self, "operations", operations)
