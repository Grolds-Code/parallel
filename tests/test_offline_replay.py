from dataclasses import replace
import socket
import subprocess
import sys

import pytest

from parallel_agent import Customer, DatabaseState, Disposition, Proposal
from parallel_agent import offline_replay


def test_replay_computes_evidence_without_network(monkeypatch, capsys):
    def blocked(*args, **kwargs):
        pytest.fail("Offline replay attempted network access")

    monkeypatch.setattr(socket, "socket", blocked)
    result = offline_replay.replay()
    first, final = result.history
    assert result.disposition is Disposition.PASSED_BOUNDED_CHECKS
    assert result.repair_attempts == 1
    assert result.search_evaluations == 4
    assert first.search.cases_evaluated == 1
    assert final.search.cases_evaluated == 3
    witness = first.observed_evaluation.counterexamples[0]
    assert witness.customer_id == "demo-001"
    assert witness.operation_index == 0
    assert witness.protected_record.active_subscription is True
    assert final.observed_evaluation.simulation.final_state == DatabaseState((
        Customer("demo-001", active_subscription=True), Customer("demo-002"),
    ))
    assert final.goal_met is True
    assert not final.failures
    assert not final.observed_evaluation.action_violations
    output = capsys.readouterr().out
    labels = ["1. Unsafe proposal", "2. Counterexample (OBSERVED_STATE)",
              "3. Action-policy failure", "4. Corrected proposal", "5. Final verification"]
    assert [output.index(label) for label in labels] == sorted(
        output.index(label) for label in labels)
    assert "OFFLINE REPLAY" in output
    assert "not fresh Llama inference" in output


@pytest.mark.parametrize("fault", ["exception", "wrong_correction", "search_disagreement"])
def test_unexpected_failures_return_nonzero(monkeypatch, capsys, fault):
    real_repair = offline_replay.repair_proposal
    real_search = offline_replay.search_counterexample

    def faulty_repair(state, proposal, callback, **kwargs):
        if fault == "exception":
            raise RuntimeError("injected failure")
        return real_repair(state, proposal, lambda request: Proposal(), **kwargs)

    if fault == "search_disagreement":
        monkeypatch.setattr(offline_replay, "search_counterexample",
                            lambda *a, **kw: replace(real_search(*a, **kw), cases_evaluated=0))
    else:
        monkeypatch.setattr(offline_replay, "repair_proposal", faulty_repair)
    assert offline_replay.main() == 1
    assert "OFFLINE REPLAY FAILED" in capsys.readouterr().err


def test_module_command():
    completed = subprocess.run(
        [sys.executable, "-m", "parallel_agent.offline_replay"],
        capture_output=True, text=True, check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "disposition=passed_bounded_checks" in completed.stdout
    assert "repair_loop_search_evaluations=4" in completed.stdout
