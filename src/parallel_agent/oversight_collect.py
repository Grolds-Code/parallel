"""Collect one blinded PARALLEL oversight pilot with an approved local plan."""
import argparse
from dataclasses import replace
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import time
from urllib.parse import urlsplit
from urllib.request import Request

from .llm import LLMConfig, LLMConnector, LLMError, LLMResponseError, LLMTransportError, _strict_json
from .oversight_pilot import _parse_decision, request_jsonl, review_cases


PILOT_COMMIT = "41e8ca9d48813318472a713eaecc4119fcf083c7"
REQUEST_SHA256 = "f07e50b24a2b4b9eb1e0a98dd98758a406674fe2739d380ab875f97da0d60ffb"
MAX_CALLS = 120
MAX_TOKENS = 128
SOCKET_TIMEOUT = 180.0
REQUEST_TIMEOUT = 600.0
RUN_TIMEOUT = 2700.0
ENV_VARS = ("PARALLEL_LLM_BASE_URL", "PARALLEL_LLM_MODEL", "PARALLEL_LLM_API_KEY")


def frozen_requests(path: Path) -> list[dict]:
    raw = path.read_bytes()
    if sha256(raw).hexdigest() != REQUEST_SHA256:
        raise ValueError("request file does not match the frozen pilot SHA-256")
    if raw != request_jsonl(review_cases()).encode("utf-8"):
        raise ValueError("current scorer disagrees with the frozen request file")
    rows = [json.loads(line) for line in raw.splitlines()]
    if len(rows) != MAX_CALLS:
        raise ValueError("frozen request count disagrees with the call cap")
    return rows


def make_plan(modal_redirects: bool) -> tuple[dict, LLMConfig | None]:
    if type(modal_redirects) is not bool:
        raise ValueError("plan must specify a boolean Modal redirect setting")
    try:
        revision = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], stderr=subprocess.DEVNULL, text=True
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        raise ValueError("run the collector from a Git checkout") from None
    missing = [name for name in ENV_VARS if not os.environ.get(name, "").strip()]
    config = None
    endpoint_sha = None
    provider = None
    if not missing:
        config = replace(LLMConfig.from_env(), max_tokens=MAX_TOKENS,
                         timeout_seconds=SOCKET_TIMEOUT, total_timeout_seconds=REQUEST_TIMEOUT,
                         modal_result_redirects=modal_redirects)
        is_modal = urlsplit(config.base_url).hostname.endswith(".modal.run")
        if modal_redirects and not is_modal:
            raise ValueError("Modal result redirects require a modal.run endpoint")
        provider = "Modal" if is_modal else "OpenAI-compatible endpoint"
        endpoint_sha = sha256(config.base_url.rstrip("/").encode("utf-8")).hexdigest()
    plan = {
        "schema": "oversight-collection-v1",
        "pilot_commit": PILOT_COMMIT,
        "collector_commit": revision,
        "source_sha256": {
            filename: sha256(Path(__file__).with_name(filename).read_bytes()).hexdigest()
            for filename in ("oversight_collect.py", "oversight_pilot.py", "llm.py")
        },
        "request_sha256": REQUEST_SHA256,
        "maximum_model_calls": MAX_CALLS,
        "reviewer_model": config.model if config else None,
        "provider": provider,
        "endpoint_sha256": endpoint_sha,
        "missing_environment": missing,
        "ready": config is not None,
        "serving": {"temperature": 0, "max_tokens": MAX_TOKENS, "response_format": "json_object",
                    "socket_timeout_seconds": SOCKET_TIMEOUT, "request_timeout_seconds": REQUEST_TIMEOUT,
                    "run_timeout_seconds": RUN_TIMEOUT, "modal_result_redirects": modal_redirects},
    }
    return plan, config


def _attempt(row: dict, config: LLMConfig) -> tuple[dict | None, dict]:
    body = json.dumps({
        "model": config.model,
        "messages": [{"role": role, "content": row[role]} for role in ("system", "user")],
        "temperature": 0, "max_tokens": config.max_tokens, "stream": False,
        "response_format": {"type": "json_object"},
    }).encode("utf-8")
    if len(body) > config.max_request_bytes:
        raise ValueError("frozen review request exceeds the configured byte limit")
    request = Request(config.base_url.rstrip("/") + "/chat/completions", data=body,
                      headers={"Authorization": "Bearer " + config.api_key,
                               "Content-Type": "application/json", "Accept": "application/json"})
    receipt = {"status": "transport_error", "response_sha256": None, "usage": None}
    try:
        raw = LLMConnector(config)._transport(request)
    except LLMTransportError:
        return None, receipt
    except LLMResponseError:
        receipt["status"] = "response_error"
        return None, receipt

    receipt["status"] = "response_error"
    receipt["response_sha256"] = sha256(raw).hexdigest()
    try:
        envelope = _strict_json(raw)
        if type(envelope) is not dict:
            return None, receipt
        usage = envelope.get("usage")
        if type(usage) is dict:
            receipt["usage"] = {
                key: usage[key] for key in ("prompt_tokens", "completion_tokens", "total_tokens")
                if type(usage.get(key)) is int and usage[key] >= 0
            }
        if envelope.get("model") != config.model:
            receipt["status"] = "model_identity_mismatch"
            return None, receipt
        choices = envelope["choices"]
        if type(choices) is not list or len(choices) != 1:
            return None, receipt
        choice = choices[0]
        message = choice["message"]
        if (choice["finish_reason"] != "stop" or message.get("role") != "assistant"
                or message.get("tool_calls") or message.get("function_call") or message.get("refusal")
                or type(message["content"]) is not str):
            return None, receipt
        response = _strict_json(message["content"])
    except (KeyError, AttributeError, TypeError, LLMResponseError):
        return None, receipt
    if _parse_decision(response).response_failed:
        return None, receipt
    receipt["status"] = "ok"
    return response, receipt


def collect(rows: list[dict], plan: dict, config: LLMConfig, directory: Path) -> dict:
    if len(rows) != MAX_CALLS or len({row["id"] for row in rows}) != MAX_CALLS:
        raise ValueError("collection requires exactly 120 unique frozen request IDs")
    directory.mkdir(mode=0o700, exist_ok=False)
    (directory / "plan.json").write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
    deadline = time.monotonic() + RUN_TIMEOUT
    attempted = 0
    completed = 0
    stop_reason = None
    with ((directory / "journal.jsonl").open("x", encoding="utf-8") as journal,
          (directory / "responses.jsonl").open("x", encoding="utf-8") as responses):
        for row in rows:
            case_id = row["id"]
            response = None
            left = deadline - time.monotonic()
            if left <= 0 and stop_reason is None:
                stop_reason = "run_deadline"
            if stop_reason is None:
                print(json.dumps({"id": case_id, "status": "attempt_started"}), file=journal, flush=True)
                os.fsync(journal.fileno())
                attempted += 1
                started = time.monotonic()
                bounded = replace(config, timeout_seconds=min(SOCKET_TIMEOUT, left),
                                  total_timeout_seconds=min(REQUEST_TIMEOUT, left))
                try:
                    response, receipt = _attempt(row, bounded)
                except KeyboardInterrupt:
                    receipt = {"status": "interrupted"}
                if receipt["status"] in ("transport_error", "model_identity_mismatch", "interrupted"):
                    stop_reason = receipt["status"]
                receipt["elapsed_seconds"] = round(time.monotonic() - started, 3)
            else:
                receipt = {"status": "unattempted", "reason": stop_reason}
            completed += response is not None
            print(json.dumps({"id": case_id, **receipt}), file=journal, flush=True)
            print(json.dumps({"id": case_id, "response": response}), file=responses, flush=True)
    return {"attempted": attempted, "valid_responses": completed,
            "unattempted": len(rows) - attempted, "stop_reason": stop_reason,
            "response_path": str(directory / "responses.jsonl")}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    preflight = commands.add_parser("preflight", help="inspect the frozen inputs and environment offline")
    preflight.add_argument("requests", type=Path)
    preflight.add_argument("--modal-result-redirects", action="store_true")
    live = commands.add_parser("collect", help="submit one live run using a preflight plan")
    live.add_argument("requests", type=Path)
    live.add_argument("plan", type=Path)
    live.add_argument("directory", type=Path)
    live.add_argument("--execute", action="store_true", required=True)
    args = parser.parse_args()
    try:
        rows = frozen_requests(args.requests)
        if args.command == "preflight":
            plan, _ = make_plan(args.modal_result_redirects)
            print(json.dumps(plan, indent=2, sort_keys=True))
            return
        approved = _strict_json(args.plan.read_bytes())
        try:
            redirects = approved["serving"]["modal_result_redirects"]
        except (KeyError, TypeError):
            raise ValueError("expected a plan generated by preflight") from None
        plan, config = make_plan(redirects)
        if config is None or json.dumps(approved, sort_keys=True) != json.dumps(plan, sort_keys=True):
            raise ValueError("preflight plan differs from the current reviewer configuration or commit")
        print(json.dumps(collect(rows, plan, config, args.directory), indent=2, sort_keys=True))
    except (LLMError, OSError, ValueError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
