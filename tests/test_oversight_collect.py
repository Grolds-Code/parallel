import json
from pathlib import Path
import sys

import pytest

from parallel_agent.llm import LLMConnector, LLMTransportError
from parallel_agent.oversight_collect import (
    ENV_VARS, MAX_CALLS, MAX_TOKENS, REQUEST_SHA256, collect, frozen_requests, main, make_plan,
)
from parallel_agent.oversight_pilot import read_responses, request_jsonl, review_cases


@pytest.fixture
def request_path(tmp_path):
    path = tmp_path / "requests.jsonl"
    path.write_bytes(request_jsonl(review_cases()).encode("utf-8"))
    return path


@pytest.fixture
def configured(monkeypatch):
    for key, value in zip(ENV_VARS, ("https://review.example.invalid/v1", "test-reviewer", "test-credential"),
                          strict=True):
        monkeypatch.setenv(key, value)
    return make_plan(False)


def _envelope(model, decision):
    return json.dumps({
        "model": model,
        "choices": [{"finish_reason": "stop",
                     "message": {"role": "assistant", "content": json.dumps(decision)}}],
        "usage": {"prompt_tokens": 200, "completion_tokens": 15, "total_tokens": 215},
    }).encode("utf-8")


def test_offline_preflight_reports_missing_env_and_rejects_changed_inputs(
        monkeypatch, request_path, capsys):
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(LLMConnector, "_transport",
                        lambda *args: pytest.fail("preflight called the model"))
    monkeypatch.setattr(sys, "argv", ["oversight_collect", "preflight", str(request_path)])
    main()
    plan = json.loads(capsys.readouterr().out)
    assert plan["ready"] is False
    assert plan["missing_environment"] == list(ENV_VARS)
    assert plan["request_sha256"] == REQUEST_SHA256
    assert plan["maximum_model_calls"] == MAX_CALLS
    request_path.write_bytes(request_path.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="frozen pilot SHA-256"):
        frozen_requests(request_path)


def test_live_command_checks_plan_before_opening_output(
        monkeypatch, request_path, configured, tmp_path, capsys):
    plan, _ = configured
    plan["serving"]["temperature"] = 1
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    output = tmp_path / "run"
    monkeypatch.setattr(LLMConnector, "_transport",
                        lambda *args: pytest.fail("changed plan called the model"))
    monkeypatch.setattr(sys, "argv", ["oversight_collect", "collect", str(request_path),
                                     str(plan_path), str(output), "--execute"])
    with pytest.raises(SystemExit, match="2"):
        main()
    assert "preflight plan differs" in capsys.readouterr().err
    assert not output.exists()


def test_collection_sends_only_blinded_messages_and_caps_calls(
        monkeypatch, request_path, configured, tmp_path):
    plan, config = configured
    rows = frozen_requests(request_path)
    submitted = []

    def respond(self, request):
        body = json.loads(request.data)
        row = rows[len(submitted)]
        assert body == {
            "model": config.model, "temperature": 0, "max_tokens": MAX_TOKENS,
            "stream": False, "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": row["system"]},
                         {"role": "user", "content": row["user"]}],
        }
        submitted.append(row["id"])
        return _envelope(config.model, {"decision": "block", "review_priority": 0})

    monkeypatch.setattr(LLMConnector, "_transport", respond)
    directory = tmp_path / "run"
    result = collect(rows, plan, config, directory)
    assert len(set(submitted)) == result["attempted"] == result["valid_responses"] == MAX_CALLS
    assert result["stop_reason"] is None
    assert len(read_responses(Path(result["response_path"]), review_cases())) == MAX_CALLS
    journal = [json.loads(line) for line in (directory / "journal.jsonl").read_text().splitlines()]
    assert len(journal) == 2 * MAX_CALLS
    assert journal[0] == {"id": rows[0]["id"], "status": "attempt_started"}
    assert journal[1]["id"] == rows[0]["id"]
    assert journal[1]["usage"]["total_tokens"] == 215
    for path in directory.iterdir():
        text = path.read_text(encoding="utf-8")
        assert config.api_key not in text and config.base_url not in text
    with pytest.raises(FileExistsError):
        collect(rows, plan, config, directory)
    assert len(submitted) == MAX_CALLS


@pytest.mark.parametrize("reason", ["transport_error", "model_identity_mismatch", "interrupted"])
def test_collection_halts_safely_and_retains_full_denominators(
        monkeypatch, request_path, configured, tmp_path, reason):
    plan, config = configured
    rows = frozen_requests(request_path)
    calls = 0

    def respond(self, request):
        nonlocal calls
        calls += 1
        if calls == 1:
            return _envelope(config.model, {"decision": "block", "review_priority": True})
        if reason == "transport_error":
            raise LLMTransportError("sample private transport text")
        if reason == "interrupted":
            raise KeyboardInterrupt
        return _envelope("other-model", {"decision": "approve", "review_priority": 0})

    monkeypatch.setattr(LLMConnector, "_transport", respond)
    directory = tmp_path / "run"
    result = collect(rows, plan, config, directory)
    assert calls == result["attempted"] == 2
    assert result["unattempted"] == MAX_CALLS - 2
    assert result["stop_reason"] == reason
    decisions = read_responses(Path(result["response_path"]), review_cases())
    assert len(decisions) == MAX_CALLS
    assert all(decision.response_failed for decision in decisions.values())
    journal = (directory / "journal.jsonl").read_text()
    assert "sample private transport text" not in journal
    assert "response_error" in journal and "unattempted" in journal
