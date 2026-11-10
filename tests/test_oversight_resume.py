import json

import pytest

from parallel_agent import oversight_collect, oversight_resume
from parallel_agent.oversight_pilot import read_responses, request_jsonl, review_cases


def _valid_response(row, config):
    return {"decision": "approve", "review_priority": 0}, {"status": "ok"}


@pytest.fixture
def interrupted_run(tmp_path, monkeypatch):
    for key, value in zip(oversight_collect.ENV_VARS,
                          ("https://review.example.invalid/v1", "test-model", "test-token"), strict=True):
        monkeypatch.setenv(key, value)
    requests = tmp_path / "requests.jsonl"
    requests.write_text(request_jsonl(review_cases()), encoding="utf-8", newline="\n")
    rows = oversight_collect.frozen_requests(requests)
    plan, config = oversight_collect.make_plan(False)
    plan["collector_commit"] = oversight_resume.ORIGINAL_COLLECTOR
    failed = rows[109]["id"]

    def respond(row, config):
        if row["id"] == failed:
            return None, {"status": "transport_error"}
        return _valid_response(row, config)

    monkeypatch.setattr(oversight_collect, "_attempt", respond)
    directory = tmp_path / "run"
    initial = oversight_collect.collect(rows, plan, config, directory)
    assert initial["attempted"] == 110 and initial["valid_responses"] == 109
    return requests, directory, rows


def test_resume_only_sends_original_unattempted_rows_and_preserves_files(
        interrupted_run, monkeypatch):
    pytest.importorskip("fcntl")
    requests, directory, rows = interrupted_run
    names = ("plan.json", "journal.jsonl", "responses.jsonl")
    before = {name: (directory / name).read_bytes() for name in names}
    sent = []

    def respond(row, config):
        sent.append(row["id"])
        return _valid_response(row, config)

    monkeypatch.setattr(oversight_resume, "_attempt", respond)
    audit = oversight_resume.resume(requests, directory, False)
    assert audit["unattempted"] == 10 and audit["valid_responses"] == 109
    assert sent == [] and not (directory / "continuation").exists()
    summary = oversight_resume.resume(requests, directory, True)
    assert sent == [row["id"] for row in rows[110:]]
    assert summary["total_attempted_or_reserved"] == 120
    assert summary["valid_responses"] == 119
    assert summary["unattempted"] == 0
    combined = directory / "responses-completed.jsonl"
    decisions = read_responses(combined, review_cases())
    assert [case_id for case_id, decision in decisions.items() if decision.response_failed] == [
        rows[109]["id"],
    ]
    assert {name: (directory / name).read_bytes() for name in names} == before
    assert oversight_resume.resume(requests, directory, True) == summary
    assert len(sent) == 10


def test_resume_reserves_an_uncertain_attempt_after_a_crash(interrupted_run, monkeypatch):
    pytest.importorskip("fcntl")
    requests, directory, rows = interrupted_run
    sent = []

    def crash(row, config):
        sent.append(row["id"])
        raise RuntimeError("simulated abrupt process exit")

    monkeypatch.setattr(oversight_resume, "_attempt", crash)
    with pytest.raises(RuntimeError, match="simulated abrupt process exit"):
        oversight_resume.resume(requests, directory, True)

    def respond(row, config):
        sent.append(row["id"])
        return _valid_response(row, config)

    monkeypatch.setattr(oversight_resume, "_attempt", respond)
    summary = oversight_resume.resume(requests, directory, True)
    assert sent == [row["id"] for row in rows[110:]]
    assert summary["valid_responses"] == 118
    assert summary["total_attempted_or_reserved"] == 120
    decisions = read_responses(directory / "responses-completed.jsonl", review_cases())
    assert decisions[rows[110]["id"]].response_failed


def test_resume_rejects_a_journal_that_disagrees_with_the_original_response(interrupted_run):
    requests, directory, rows = interrupted_run
    journal = directory / "journal.jsonl"
    records = [json.loads(line) for line in journal.read_text().splitlines()]
    for record in records:
        if record["id"] == rows[109]["id"] and record["status"] == "transport_error":
            record["status"] = "ok"
    journal.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
    with pytest.raises(ValueError, match="successful journal entry lacks a valid decision"):
        oversight_resume.resume(requests, directory, False)


def test_resume_checks_model_configuration_before_submitting(interrupted_run, monkeypatch):
    requests, directory, _ = interrupted_run
    monkeypatch.setenv("PARALLEL_LLM_MODEL", "changed-model")
    monkeypatch.setattr(oversight_resume, "_attempt",
                        lambda *args: pytest.fail("changed configuration submitted a request"))
    with pytest.raises(ValueError, match="differ from the original plan"):
        oversight_resume.resume(requests, directory, True)
    assert not (directory / "continuation").exists()
