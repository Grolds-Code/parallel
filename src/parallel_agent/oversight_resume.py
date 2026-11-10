"""Continue the frozen oversight run using only its unattempted requests."""
import argparse
from dataclasses import dataclass, replace
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import subprocess
from tempfile import NamedTemporaryFile
import time

from .llm import LLMConfig, LLMError, _strict_json
from .oversight_collect import (
    MAX_CALLS, PILOT_COMMIT, REQUEST_SHA256, REQUEST_TIMEOUT, RUN_TIMEOUT,
    SOCKET_TIMEOUT, _attempt, frozen_requests, make_plan,
)
from .oversight_pilot import _parse_decision


ORIGINAL_COLLECTOR = "9da79ff9ec3aff1e4b865a4f844bc63defe7fe06"
TERMINAL = {"ok", "response_error", "transport_error", "model_identity_mismatch", "interrupted"}
HALT = {"transport_error", "model_identity_mismatch", "interrupted"}


@dataclass(frozen=True, slots=True)
class OriginalRun:
    plan: dict
    responses: list[dict]
    unattempted: list[dict]
    failures: list[dict]
    stop_reason: str | None
    elapsed: float
    fingerprints: dict


def _rows(raw: bytes) -> list[dict]:
    rows = [_strict_json(line) for line in raw.splitlines()]
    if any(type(row) is not dict for row in rows):
        raise ValueError("run JSON Lines must contain objects")
    return rows


def _elapsed(record: dict) -> float:
    value = record.get("elapsed_seconds")
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError("invalid recorded request duration")
    return value


def _check_result(response, status: str) -> None:
    if status == "ok":
        if _parse_decision(response).response_failed:
            raise ValueError("successful journal entry lacks a valid decision")
    elif response is not None:
        raise ValueError("failed or unattempted entry must have a null response")


def read_original(requests: Path, directory: Path) -> OriginalRun:
    cases = frozen_requests(requests)
    files = {name: (directory / name).read_bytes() for name in (
        "plan.json", "journal.jsonl", "responses.jsonl",
    )}
    plan = _strict_json(files["plan.json"])
    if (type(plan) is not dict or plan.get("schema") != "oversight-collection-v1"
            or plan.get("collector_commit") != ORIGINAL_COLLECTOR
            or plan.get("pilot_commit") != PILOT_COMMIT or plan.get("request_sha256") != REQUEST_SHA256
            or plan.get("maximum_model_calls") != MAX_CALLS or plan.get("ready") is not True):
        raise ValueError("original run plan does not identify the frozen pilot and collector")
    responses = _rows(files["responses.jsonl"])
    journal = iter(_rows(files["journal.jsonl"]))
    unattempted = []
    failures = []
    elapsed = 0.0
    stop_reason = None
    for case, response in zip(cases, responses, strict=True):
        case_id = case["id"]
        start = next(journal, {})
        if (set(response) != {"id", "response"} or response["id"] != case_id
                or start.get("id") != case_id):
            raise ValueError("original files disagree with frozen request order")
        if start.get("status") == "unattempted":
            reason = start.get("reason")
            if stop_reason is None and reason == "run_deadline":
                stop_reason = reason
            if reason != stop_reason or stop_reason is None:
                raise ValueError("unattempted request has an inconsistent stop reason")
            _check_result(response["response"], "unattempted")
            unattempted.append(case)
            continue
        terminal = next(journal, {})
        status = terminal.get("status")
        if (stop_reason is not None or start != {"id": case_id, "status": "attempt_started"}
                or terminal.get("id") != case_id or type(status) is not str or status not in TERMINAL):
            raise ValueError("original journal lacks a valid start and result for an attempted request")
        duration = _elapsed(terminal)
        elapsed += duration
        _check_result(response["response"], status)
        if status != "ok":
            failures.append({"id": case_id, "status": status, "elapsed_seconds": duration})
        if status in HALT:
            stop_reason = status
    if next(journal, None) is not None:
        raise ValueError("original journal contains extra entries")
    fingerprints = {name: sha256(raw).hexdigest() for name, raw in files.items()}
    fingerprints["oversight_resume.py"] = sha256(Path(__file__).read_bytes()).hexdigest()
    return OriginalRun(plan, responses, unattempted, failures, stop_reason, elapsed, fingerprints)


def _live_config(original: OriginalRun) -> LLMConfig:
    try:
        redirects = original.plan["serving"]["modal_result_redirects"]
    except (KeyError, TypeError):
        raise ValueError("invalid serving configuration in original run plan") from None
    current, config = make_plan(redirects)
    current["collector_commit"] = ORIGINAL_COLLECTOR
    if config is None or json.dumps(current, sort_keys=True) != json.dumps(original.plan, sort_keys=True):
        raise ValueError("current source, reviewer settings or endpoint differ from the original plan")
    ancestry = subprocess.run(["git", "merge-base", "--is-ancestor", ORIGINAL_COLLECTOR, "HEAD"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if ancestry.returncode:
        raise ValueError("current checkout must descend from the original collector")
    return config


def _existing(original: OriginalRun, directory: Path) -> dict[str, dict]:
    binding = directory / "original.json"
    if binding.exists():
        if _strict_json(binding.read_bytes()) != original.fingerprints:
            raise ValueError("original files changed after continuation started")
    records = {}
    eligible = {row["id"] for row in original.unattempted}
    for path in directory.glob("case-*.json"):
        case_id = path.stem.removeprefix("case-")
        record = _strict_json(path.read_bytes())
        if (not binding.exists() or case_id not in eligible or type(record) is not dict
                or record.get("id") != case_id or "response" not in record):
            raise ValueError("continuation contains an invalid request record")
        status = record.get("status")
        if status != "attempt_started":
            if type(status) is not str or status not in TERMINAL:
                raise ValueError("continuation contains an unknown request status")
            _elapsed(record)
        _check_result(record["response"], status)
        records[case_id] = record
    return records


def _sync_directory(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_write(path: Path, data: str) -> None:
    temporary = None
    try:
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as file:
            temporary = Path(file.name)
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
        temporary.replace(path)
        _sync_directory(path.parent)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _continue(original: OriginalRun, directory: Path, config: LLMConfig,
              records: dict[str, dict]) -> str | None:
    used = original.elapsed + sum(
        REQUEST_TIMEOUT if record["status"] == "attempt_started" else _elapsed(record)
        for record in records.values()
    )
    deadline = time.monotonic() + RUN_TIMEOUT - used
    for row in original.unattempted:
        case_id = row["id"]
        if case_id in records:
            continue
        left = deadline - time.monotonic()
        if left <= 0:
            return "request_time_budget"
        path = directory / f"case-{case_id}.json"
        marker = {"id": case_id, "status": "attempt_started", "response": None}
        with path.open("x", encoding="utf-8") as file:
            json.dump(marker, file)
            file.flush()
            os.fsync(file.fileno())
        _sync_directory(directory)
        started = time.monotonic()
        bounded = replace(config, timeout_seconds=min(SOCKET_TIMEOUT, left),
                          total_timeout_seconds=min(REQUEST_TIMEOUT, left))
        try:
            response, receipt = _attempt(row, bounded)
        except KeyboardInterrupt:
            response, receipt = None, {"status": "interrupted"}
        record = {"id": case_id, "response": response, **receipt,
                  "elapsed_seconds": round(time.monotonic() - started, 3)}
        _atomic_write(path, json.dumps(record) + "\n")
        records[case_id] = record
        if record["status"] in HALT:
            return record["status"]
    return None


def _summary(original: OriginalRun, records: dict, stop: str | None, combined: Path | None) -> dict:
    original_attempted = MAX_CALLS - len(original.unattempted)
    valid = original_attempted - len(original.failures)
    valid += sum(record["status"] == "ok" for record in records.values())
    return {"original_attempted": original_attempted, "original_failures": original.failures,
            "original_stop_reason": original.stop_reason, "continuation_attempted_or_reserved": len(records),
            "total_attempted_or_reserved": original_attempted + len(records),
            "valid_responses": valid, "unattempted": len(original.unattempted) - len(records),
            "continuation_stop_reason": stop, "combined_response_path": str(combined) if combined else None}


def resume(requests: Path, directory: Path, execute: bool) -> dict:
    original = read_original(requests, directory)
    continuation = directory / "continuation"
    if not execute:
        return _summary(original, _existing(original, continuation), None, None)
    if original.stop_reason != "transport_error" or not original.unattempted:
        raise ValueError("this continuation requires requests left by an original transport failure")
    config = _live_config(original)
    try:
        import fcntl
    except ImportError:
        raise ValueError("run the live continuation from WSL or a POSIX shell") from None
    continuation.mkdir(mode=0o700, exist_ok=True)
    _sync_directory(directory)
    with (continuation / "lock").open("a+b") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("another continuation is already running") from None
        records = _existing(original, continuation)
        if not (continuation / "original.json").exists():
            _atomic_write(continuation / "original.json", json.dumps(original.fingerprints) + "\n")
        stop = _continue(original, continuation, config, records)
        combined = None
        if len(records) == len(original.unattempted):
            combined = directory / "responses-completed.jsonl"
            merged = "".join(
                json.dumps({"id": row["id"], "response": records.get(row["id"], row)["response"]}) + "\n"
                for row in original.responses
            )
            if combined.exists():
                if combined.read_text(encoding="utf-8") != merged:
                    raise ValueError("existing combined responses differ from the journal records")
            else:
                _atomic_write(combined, merged)
        return _summary(original, records, stop, combined)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("requests", type=Path)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--execute", action="store_true", help="submit only confirmed unattempted requests")
    args = parser.parse_args()
    try:
        print(json.dumps(resume(args.requests, args.directory, args.execute), indent=2, sort_keys=True))
    except (LLMError, OSError, ValueError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
