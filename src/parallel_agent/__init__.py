"""PARALLEL's deterministic, in-memory execution foundation."""
from .actions import ActionCheck, ActionViolation, forbid_original_customer_operations
from .llm import (LLMConfig, LLMConfigurationError, LLMConnector, LLMError,
                  LLMResponseError, LLMTransportError)
from .models import (CreateCustomer, Customer, DatabaseState, DeleteCustomer,
                     Operation, Proposal, ReplaceCustomer)
from .search import SearchResult, SearchWitness, StateProvenance, search_counterexample
from .repair import (Disposition, FailureKind, GoalCheck, RepairCallable, RepairIteration,
                     RepairRequest, RepairResult, repair_proposal)
from .simulator import Change, ErrorCode, Simulation, simulate
from .verifier import (Constraint, Counterexample, VerificationResult, Violation,
                       evaluate, verify)

__all__ = [
    "ActionCheck", "ActionViolation", "forbid_original_customer_operations",
    "LLMConfig", "LLMConfigurationError", "LLMConnector", "LLMError",
    "LLMResponseError", "LLMTransportError",
    "Disposition", "FailureKind", "GoalCheck", "RepairCallable", "RepairIteration",
    "RepairRequest", "RepairResult", "repair_proposal", "StateProvenance",
    "SearchResult", "SearchWitness", "search_counterexample",
    "CreateCustomer", "Customer", "DatabaseState", "DeleteCustomer", "Operation",
    "Proposal", "ReplaceCustomer", "Change", "ErrorCode", "Simulation", "simulate",
    "Constraint", "Counterexample", "VerificationResult", "Violation", "evaluate", "verify",
]


def main() -> None:
    print("PARALLEL execution library: use parallel_agent.evaluate(state, proposal).")
