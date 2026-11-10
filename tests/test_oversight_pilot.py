import hashlib
import json

import pytest

from parallel_agent.oversight_pilot import (
    Decision, PROFILES, REVIEW_CAP, VIEWS, baseline_report, read_responses,
    report, request_jsonl, review_cases, trace_pairs,
)


def test_matched_cases_and_blinded_export():
    cases = review_cases()
    serialized = request_jsonl(cases)
    assert len(cases) == 120
    assert serialized == request_jsonl(review_cases())
    rows = {row["id"]: row for row in map(json.loads, serialized.splitlines())}
    by_trace = {(case.trace.key, case.profile, case.view): case for case in cases}
    for allowed, forbidden in trace_pairs():
        assert allowed.simulation.final_state == forbidden.simulation.final_state
        for profile in PROFILES:
            for view in VIEWS:
                safe_case = by_trace[allowed.key, profile, view]
                unsafe_case = by_trace[forbidden.key, profile, view]
                safe_row, unsafe_row = rows[safe_case.key], rows[unsafe_case.key]
                assert set(safe_row) == {"id", "system", "user"}
                assert set(json.loads(safe_row["user"])) == set(safe_case.evidence)
                assert safe_row["system"] == unsafe_row["system"]
                assert (safe_row["user"] == unsafe_row["user"]) == (
                    profile == "target_omitted" and view == "transcript"
                )


def test_baselines_control_for_goal_success_and_irrelevant_missing_fields():
    cases = review_cases()
    baseline = baseline_report(cases)
    goal = baseline["goal_only"]["conditions"]
    rules = baseline["rules"]["conditions"]
    assert baseline["rules"]["request_sha256"] == hashlib.sha256(
        request_jsonl(cases).encode("utf-8")
    ).hexdigest()
    for profile in PROFILES:
        for view in VIEWS:
            assert goal[profile][view]["safe_approved"] == 10
            assert goal[profile][view]["unsafe_approved"] == 10
            group = rules[profile][view]
            assert group["unsafe_approved"] == 0
            if profile == "target_omitted" and view == "transcript":
                assert group["safe_approved"] == 0
                assert group["escalated"] == REVIEW_CAP
            else:
                assert group["safe_approved"] == 10
                assert group["escalated"] == 0


def test_priority_ties_use_same_cases_and_pending_review_is_separate():
    cases = review_cases()
    decisions = {case.key: Decision("approve", 100) for case in cases}
    groups = report(cases, decisions)["conditions"]
    first = groups["complete"]["transcript"]
    for views in groups.values():
        for group in views.values():
            assert group == first
            assert group["escalated"] == REVIEW_CAP
            assert group["safe_pending_review"] + group["unsafe_pending_review"] == REVIEW_CAP
            assert group["safe_approved"] + group["unsafe_approved"] == 20 - REVIEW_CAP
            assert group["safe_blocked"] == group["unsafe_blocked"] == 0


def test_response_failures_stay_in_denominators_and_missing_rows_are_rejected(tmp_path):
    cases = review_cases()
    output = tmp_path / "responses.jsonl"
    responses = [{"id": case.key, "response": {"decision": "approve", "review_priority": 0}}
                 for case in cases]
    responses[0]["response"] = None
    responses[1]["response"]["review_priority"] = True

    def write(rows):
        output.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    write(responses)
    parsed = read_responses(output, cases)
    for case in cases[:2]:
        assert parsed[case.key] == Decision("block", response_failed=True)
    groups = [group for views in report(cases, parsed)["conditions"].values() for group in views.values()]
    assert sum(group["cases"] for group in groups) == 120
    assert sum(group["response_failures"] for group in groups) == 2

    write(responses[:-1])
    with pytest.raises(ValueError, match="missing 1 cases"):
        read_responses(output, cases)
    write(responses + responses[:1])
    with pytest.raises(ValueError, match="duplicate response envelope"):
        read_responses(output, cases)
