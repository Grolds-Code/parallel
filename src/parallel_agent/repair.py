"""Offline counterexample-guided repair; callbacks propose, trusted code evaluates."""
from dataclasses import dataclass
from enum import StrEnum
from typing import Callable

from .models import DatabaseState, Proposal
from .search import SearchResult, StateProvenance, search_counterexample
from .verifier import VerificationResult, evaluate


class Disposition(StrEnum):
    PASSED_BOUNDED_CHECKS = "passed_bounded_checks"
    REPAIR_LIMIT = "repair_limit"
    SEARCH_BUDGET_EXHAUSTED = "search_budget_exhausted"
    REPEATED_PROPOSAL = "repeated_proposal"
    INVALID_RESPONSE = "invalid_response"
    REPAIR_ERROR = "repair_error"
    GOAL_ERROR = "goal_error"


class FailureKind(StrEnum):
    OBSERVED_VIOLATION = "observed_violation"
    SIMULATION_ERROR = "simulation_error"
    COUNTERFACTUAL_VULNERABILITY = "counterfactual_vulnerability"
    GOAL_NOT_MET = "goal_not_met"


@dataclass(frozen=True, slots=True)
class RepairIteration:
    proposal: Proposal
    observed_evaluation: VerificationResult
    search: SearchResult
    goal_met: bool | None
    failures: tuple[FailureKind, ...]
    goal_error: str | None = None
    state_provenance: StateProvenance = StateProvenance.OBSERVED_STATE


@dataclass(frozen=True, slots=True)
class RepairRequest:
    """Evidence is scoped to each initial snapshot; hypothetical flags are not facts."""
    supplied_state: DatabaseState
    history: tuple[RepairIteration, ...]
    attempt: int
    attempts_remaining: int
    search_evaluations_remaining: int

    @property
    def evidence(self) -> RepairIteration:
        return self.history[-1]


@dataclass(frozen=True, slots=True)
class RepairResult:
    disposition: Disposition
    history: tuple[RepairIteration, ...]
    last_proposal: Proposal
    repair_attempts: int
    search_evaluations: int
    detail: str

    @property
    def accepted_proposal(self) -> Proposal | None:
        return (self.last_proposal if self.disposition is Disposition.PASSED_BOUNDED_CHECKS
                else None)


RepairCallable = Callable[[RepairRequest], Proposal]
GoalCheck = Callable[[VerificationResult], bool]


def repair_proposal(
    state: DatabaseState,
    proposal: Proposal,
    repair: RepairCallable,
    *,
    max_repair_attempts: int = 3,
    search_budget_per_iteration: int = 100,
    max_search_evaluations: int = 400,
    goal: GoalCheck | None = None,
) -> RepairResult:
    """Re-evaluate each proposal from the original snapshot, never a prior final state.

    Attempts count callback invocations; the initial proposal is not an attempt.
    Each iteration reserves the full per-iteration search allowance, then charges
    actual evaluations. Insufficient allowance fails closed (including zero).
    Completing a bounded search is not total-budget failure or universal safety.
    Observed evaluations are additional to the search budget, at most attempts+1.
    Callbacks are trusted local code, not sandboxed or subject to time limits.
    """
    if type(state) is not DatabaseState or type(proposal) is not Proposal:
        raise TypeError("state and proposal must be validated model instances")
    if not callable(repair) or (goal is not None and not callable(goal)):
        raise TypeError("repair and goal must be callable")
    for name, value in (("max_repair_attempts", max_repair_attempts),
                        ("search_budget_per_iteration", search_budget_per_iteration),
                        ("max_search_evaluations", max_search_evaluations)):
        if type(value) is not int:
            raise TypeError(f"{name} must be an integer")
        if value < 0:
            raise ValueError(f"{name} must be nonnegative")

    history: list[RepairIteration] = []
    seen: set[Proposal] = set()
    attempts = spent = 0

    def finish(disposition: Disposition, detail: str) -> RepairResult:
        return RepairResult(disposition, tuple(history), proposal, attempts, spent, detail)

    while True:
        if proposal in seen:
            return finish(Disposition.REPEATED_PROPOSAL, "Previously evaluated proposal repeated.")
        if (search_budget_per_iteration == 0 or
                max_search_evaluations - spent < search_budget_per_iteration):
            return finish(Disposition.SEARCH_BUDGET_EXHAUSTED,
                          "Insufficient budget for a full bounded search iteration.")
        seen.add(proposal)
        observed = evaluate(state, proposal)
        search = search_counterexample(state, proposal, budget=search_budget_per_iteration)
        spent += search.cases_evaluated
        failures = []
        if observed.violations:
            failures.append(FailureKind.OBSERVED_VIOLATION)
        if observed.simulation.errors:
            failures.append(FailureKind.SIMULATION_ERROR)
        if (search.witness is not None and
                search.witness.state_provenance is StateProvenance.COUNTERFACTUAL_STATE):
            failures.append(FailureKind.COUNTERFACTUAL_VULNERABILITY)
        goal_met = None
        goal_error = None
        if goal is not None:
            try:
                goal_met = goal(observed)
                if type(goal_met) is not bool:
                    raise TypeError("goal must return bool")
            except Exception as exc:
                goal_met = None
                goal_error = f"{type(exc).__name__}: {exc}"
            if goal_met is False:
                failures.append(FailureKind.GOAL_NOT_MET)
        history.append(RepairIteration(proposal, observed, search, goal_met,
                                       tuple(failures), goal_error))
        if goal_error is not None:
            return finish(Disposition.GOAL_ERROR, goal_error)
        if not failures:
            return finish(Disposition.PASSED_BOUNDED_CHECKS,
                          "Observed checks passed; no counterexample found within the search "
                          "budget and candidate domain. This is not universal safety or execution approval.")
        if attempts >= max_repair_attempts:
            return finish(Disposition.REPAIR_LIMIT, "Repair attempt limit reached.")
        if max_search_evaluations - spent < search_budget_per_iteration:
            return finish(Disposition.SEARCH_BUDGET_EXHAUSTED,
                          "Insufficient budget to evaluate another repair.")
        attempts += 1
        request = RepairRequest(state, tuple(history), attempts,
                                max_repair_attempts - attempts,
                                max_search_evaluations - spent)
        try:
            response = repair(request)
        except Exception as exc:
            return finish(Disposition.REPAIR_ERROR, f"{type(exc).__name__}: {exc}")
        if type(response) is not Proposal:
            return finish(Disposition.INVALID_RESPONSE, "Repair callable must return a Proposal.")
        proposal = response
