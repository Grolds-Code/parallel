"""PARALLEL's deterministic, in-memory execution foundation."""
from .models import (CreateCustomer, Customer, DatabaseState, DeleteCustomer,
                     Operation, Proposal, ReplaceCustomer)
from .search import SearchResult, SearchWitness, search_counterexample
from .simulator import Change, ErrorCode, Simulation, simulate
from .verifier import (Constraint, Counterexample, VerificationResult, Violation,
                       evaluate, verify)

__all__ = [
    "SearchResult", "SearchWitness", "search_counterexample",
    "CreateCustomer", "Customer", "DatabaseState", "DeleteCustomer", "Operation",
    "Proposal", "ReplaceCustomer", "Change", "ErrorCode", "Simulation", "simulate",
    "Constraint", "Counterexample", "VerificationResult", "Violation", "evaluate", "verify",
]


def main() -> None:
    print("PARALLEL execution library: use parallel_agent.evaluate(state, proposal).")
