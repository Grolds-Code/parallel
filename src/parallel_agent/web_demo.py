"""Loopback-only, dependency-free browser demonstration; no inference endpoints."""
import argparse
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from parallel_agent import (
    CreateCustomer, Customer, DatabaseState, DeleteCustomer, Proposal,
    ReplaceCustomer, evaluate, forbid_original_customer_operations,
    repair_proposal, search_counterexample, simulate, verify,
)

ASSETS = Path(__file__).with_name("web_assets")
SCENARIOS = ("delete", "replacement", "create")


def build_evidence(scenario="delete"):
    """Compute fresh evidence from immutable synthetic inputs on every request."""
    if scenario not in SCENARIOS:
        raise ValueError("Unknown scenario")
    original = Customer("demo-001", active_subscription=True)
    created = Customer("demo-002")
    state = DatabaseState((original,))
    target = DatabaseState((original, created))
    correction = Proposal((CreateCustomer(created),))
    proposals = {
        "delete": Proposal((DeleteCustomer(original.customer_id),)),
        "replacement": Proposal((CreateCustomer(created), ReplaceCustomer(original))),
        "create": correction,
    }
    proposal = proposals[scenario]
    simulation = simulate(state, proposal)
    evaluation = verify(simulation, action_check=forbid_original_customer_operations)
    if evaluation != evaluate(state, proposal, action_check=forbid_original_customer_operations):
        raise RuntimeError("Verification disagreement")
    search = search_counterexample(state, proposal, budget=3)
    witness_replayed = None
    if search.witness is not None:
        witness = search.witness
        replay = evaluate(witness.initial_state, witness.proposal)
        witness_replayed = (replay.simulation.changes == witness.trace
                            and replay.violations == witness.violations)
        if not witness_replayed:
            raise RuntimeError("Witness replay disagreement")
    repair = repair_proposal(
        state, proposals["delete"], lambda request: correction,
        goal=lambda result: result.simulation.final_state == target,
        action_check=forbid_original_customer_operations,
        max_repair_attempts=1, search_budget_per_iteration=3,
        max_search_evaluations=6,
    )
    return {
        "scenario": scenario, "source": "RECORDED MODEL OUTPUT / OFFLINE REPLAY",
        "model": "unsloth/Llama-3.2-3B-Instruct", "initial_state": asdict(state),
        "target_state": asdict(target), "proposal": asdict(proposal),
        "evaluation": {**asdict(evaluation), "is_safe": evaluation.is_safe,
                       "goal_met": simulation.final_state == target},
        "search": {**asdict(search), "message": search.message},
        "witness_replayed": witness_replayed,
        "historical_run": {
            "source": "README.md · Strict Meta Llama repair: live outcome and OFFLINE REPLAY",
            "model_repair_attempts": 1, "search_evaluations": 4,
            "elapsed_seconds": 63.73,
            "label": "Historical live run; not offline recomputation timing",
        },
        "repair": {**asdict(repair), "history": [
            {**asdict(item), "observed_evaluation": {
                **asdict(item.observed_evaluation),
                "is_safe": item.observed_evaluation.is_safe,
            }, "search": {**asdict(item.search), "message": item.search.message}}
            for item in repair.history
        ], "accepted_proposal": (
            asdict(repair.accepted_proposal) if repair.accepted_proposal else None)},
        "scope": "Bounded evidence only; not universal safety or execution approval.",
    }


def route(path):
    """Small allowlisted router, also usable without a listening socket in tests."""
    url = urlsplit(path)
    if url.path == "/api/evidence":
        query = parse_qs(url.query, keep_blank_values=True)
        if set(query) - {"scenario"} or len(query.get("scenario", [])) > 1:
            return 400, "application/json", b'{"error":"Invalid query"}'
        scenario = query.get("scenario", ["delete"])[0]
        if scenario not in SCENARIOS:
            return 400, "application/json", b'{"error":"Unknown scenario"}'
        try:
            data = build_evidence(scenario)
            return 200, "application/json", json.dumps(data).encode()
        except Exception:
            # Do not serialize exception details, environment, or stale verdicts.
            return 500, "application/json", b'{"error":"Evidence computation failed; no verdict available."}'
    files = {"/": ("index.html", "text/html"),
             "/app.js": ("app.js", "text/javascript"),
             "/style.css": ("style.css", "text/css")}
    if url.path in files and not url.query:
        name, kind = files[url.path]
        try:
            return 200, kind, (ASSETS / name).read_bytes()
        except OSError:
            return 500, "text/plain", b"Interface unavailable"
    return 404, "text/plain", b"Not found"


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        status, kind, body = route(self.path)
        self.send_response(status)
        self.send_header("Content-Type", kind + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass  # Request strings need not be logged.


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    with ThreadingHTTPServer(("127.0.0.1", args.port), Handler) as server:
        print(f"PARALLEL offline demo: http://127.0.0.1:{server.server_port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
