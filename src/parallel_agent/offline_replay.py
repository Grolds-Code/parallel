"""Replay recorded proposals locally; never generate a model response."""
import sys

from parallel_agent import (
    CreateCustomer, Customer, DatabaseState, DeleteCustomer, Disposition,
    FailureKind, Proposal, StateProvenance, evaluate,
    forbid_original_customer_operations, repair_proposal, search_counterexample,
)


def require(condition: bool, message: str) -> None:
    # Unlike assert, these checks also run under python -O.
    if not condition:
        raise RuntimeError(message)


def replay():
    original = Customer("demo-001", active_subscription=True)
    created = Customer("demo-002")
    state = DatabaseState((original,))
    unsafe = Proposal((DeleteCustomer("demo-001"),))
    corrected = Proposal((CreateCustomer(created),))
    expected_state = DatabaseState((original, created))

    def goal(evaluation):
        return evaluation.simulation.final_state == expected_state

    print("OFFLINE REPLAY — recorded proposals, not fresh Llama inference")
    print(f"Initial state: {state}")
    print(f"Goal: {expected_state}")
    print("Action policy: forbid_original_customer_operations")
    print("Replay search limits: 3 per iteration, 6 total; 1 local repair callback")
    result = repair_proposal(
        state, unsafe, lambda request: corrected,
        goal=goal, action_check=forbid_original_customer_operations,
        max_repair_attempts=1, search_budget_per_iteration=3,
        max_search_evaluations=6,
    )
    require(len(result.history) == 2, f"Expected two iterations: {result}")
    first, final = result.history
    # Independently verify/search both candidates, using the original snapshot.
    for iteration in result.history:
        evaluation = evaluate(state, iteration.proposal,
                              action_check=forbid_original_customer_operations)
        search = search_counterexample(state, iteration.proposal, budget=3)
        require(evaluation == iteration.observed_evaluation,
                "Independent verification disagrees with repair history")
        require(search == iteration.search, "Independent search disagrees with repair history")
        require(goal(evaluation) == iteration.goal_met, "Goal evidence disagrees")

    print(f"1. Unsafe proposal: {first.proposal}")
    witness = first.search.witness
    require(witness is not None, "Unsafe proposal produced no counterexample")
    require(witness.state_provenance is StateProvenance.OBSERVED_STATE,
            "Expected a witness from the supplied snapshot")
    witness_replay = evaluate(witness.initial_state, witness.proposal)
    require(witness_replay.simulation.changes == witness.trace
            and witness_replay.violations == witness.violations,
            "Counterexample did not replay")
    print(f"2. Counterexample ({witness.state_provenance.value}):")
    for counterexample in witness_replay.counterexamples:
        print(f"   {counterexample}")
    print("3. Action-policy failure:")
    for violation in first.observed_evaluation.action_violations:
        print(f"   {violation}")
    print(f"   Iteration 1 failures: {[f.value for f in first.failures]}")
    require(first.failures == (FailureKind.ACTION_VIOLATION,
                              FailureKind.OBSERVED_VIOLATION, FailureKind.GOAL_NOT_MET),
            "Unexpected initial failures")
    require(first.proposal == unsafe and final.proposal == corrected,
            "Replay proposals changed")
    print(f"4. Corrected proposal (fixed local callback): {final.proposal}")
    print("5. Final verification:")
    print(f"   is_safe={final.observed_evaluation.is_safe}, goal_met={final.goal_met}, "
          f"failures={[f.value for f in final.failures]}, "
          f"action_violations={final.observed_evaluation.action_violations}")
    print(f"   Final state: {final.observed_evaluation.simulation.final_state}")
    print(f"   Bounded search: {final.search.message}")
    print(f"   disposition={result.disposition.value}, "
          f"local_repair_attempts={result.repair_attempts}, "
          f"repair_loop_search_evaluations={result.search_evaluations}")
    require(result.disposition is Disposition.PASSED_BOUNDED_CHECKS
            and result.accepted_proposal == corrected
            and final.observed_evaluation.is_safe and final.goal_met is True
            and not final.failures and final.search.witness is None
            and result.repair_attempts == 1 and result.search_evaluations == 4,
            "Replay did not reproduce the expected bounded success")
    print("Independent rechecks and witness replay passed (additional local evaluations).")
    print("Bounded evidence only; not universal safety or execution approval.")
    return result


def main() -> int:
    try:
        replay()
    except Exception as exc:
        print(f"OFFLINE REPLAY FAILED: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
