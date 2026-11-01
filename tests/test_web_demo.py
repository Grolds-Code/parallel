"""Offline evidence and route tests: no external service or model required."""
import json
import socket

import pytest

from parallel_agent import web_demo


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*args, **kwargs):
        pytest.fail("Evidence must not access the network")
    monkeypatch.setattr(socket, "socket", blocked)


@pytest.mark.parametrize("scenario,safe,goal,violations,actions,cases", [
    ("delete", False, False, 1, 1, 1),
    ("replacement", False, True, 0, 1, 3),
    ("create", True, True, 0, 0, 3),
])
def test_computed_comparisons(scenario, safe, goal, violations, actions, cases):
    data = web_demo.build_evidence(scenario)
    e = data["evaluation"]
    assert e["is_safe"] is safe
    assert e["goal_met"] is goal
    assert len(e["violations"]) == violations
    assert len(e["action_violations"]) == actions
    assert data["search"]["cases_evaluated"] == cases
    assert data["repair"]["disposition"] == "passed_bounded_checks"
    assert data["repair"]["search_evaluations"] == 4
    correction = data["repair"]["accepted_proposal"]["operations"]
    assert len(correction) == 1
    assert correction[0]["type"] == "create_customer"
    assert correction[0]["customer"]["customer_id"] == "demo-002"


def test_observed_witness_and_policy():
    data = web_demo.build_evidence()
    witness = data["search"]["witness"]
    assert witness["state_provenance"] == "OBSERVED_STATE"
    assert witness["violations"][0]["constraint"] == "active_subscription"
    assert witness["trace"][0]["before"]["active_subscription"] is True
    assert witness["trace"][0]["after"] is None
    assert data["witness_replayed"] is True
    assert data["evaluation"]["action_violations"][0]["constraint"] == "original_customer_untouched"


def test_repeated_requests_start_from_original_state():
    before = web_demo.build_evidence("create")
    web_demo.build_evidence("delete")
    assert web_demo.build_evidence("create") == before


@pytest.mark.parametrize("path,kind", [("/", "text/html"), ("/app.js", "text/javascript"), ("/style.css", "text/css"), ("/api/evidence", "application/json")])
def test_routes(path, kind):
    status, content_type, body = web_demo.route(path)
    assert status == 200 and content_type == kind and body


@pytest.mark.parametrize("path,status", [("/api/evidence?scenario=unknown", 400), ("/api/evidence?scenario=", 400), ("/api/evidence?scenario=create&scenario=delete", 400), ("/api/evidence?budget=999999", 400), ("/../web_demo.py", 404), ("/.env", 404), ("/api/inference", 404)])
def test_invalid_routes(path, status):
    assert web_demo.route(path)[0] == status


def test_scenario_validation():
    with pytest.raises(ValueError):
        web_demo.build_evidence("unknown")


def test_api_matches_computation():
    status, _, body = web_demo.route("/api/evidence?scenario=replacement")
    assert status == 200
    assert json.loads(body) == json.loads(json.dumps(web_demo.build_evidence("replacement")))


@pytest.mark.parametrize("api", ["simulate", "verify", "evaluate", "search_counterexample", "repair_proposal"])
def test_computation_failure_has_no_verdict(monkeypatch, api):
    def fail(*args, **kwargs):
        raise RuntimeError("SECRET must never appear in response")
    monkeypatch.setattr(web_demo, api, fail)
    status, _, body = web_demo.route("/api/evidence")
    assert status == 500
    assert b"SECRET" not in body and b"is_safe" not in body
    assert "no verdict" in json.loads(body)["error"]


def test_missing_asset(monkeypatch, tmp_path):
    monkeypatch.setattr(web_demo, "ASSETS", tmp_path)
    assert web_demo.route("/")[0] == 500


def test_interface_contract():
    html = web_demo.route("/")[2].decode()
    assert 'lang="en"' in html and 'aria-live="polite"' in html
    assert 'for="scenario"' in html
    assert "Prerecorded Meta Llama inference; verification freshly computed offline" in html
    assert "not universal safety" in html
    js = web_demo.route("/app.js")[2].decode()
    assert "textContent" in js and "innerHTML" not in js
    assert "response.ok" in js and "No verdict available" in js


def test_server_is_loopback_only(monkeypatch):
    import sys
    bound = []
    class Server:
        server_port = 8765
        def __init__(self, address, handler):
            bound.append(address)
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def serve_forever(self):
            raise KeyboardInterrupt
    monkeypatch.setattr(web_demo, "ThreadingHTTPServer", Server)
    monkeypatch.setattr(sys, "argv", ["web_demo"])
    web_demo.main()
    assert bound == [("127.0.0.1", 8765)]


def test_historical_metrics_and_fixed_path():
    baseline = web_demo.build_evidence()
    historical = baseline['historical_run']
    assert historical['model_repair_attempts'] == 1
    assert historical['search_evaluations'] == 4
    assert historical['elapsed_seconds'] == 63.73
    assert 'Historical live run' in historical['label']
    assert 'README.md' in historical['source']
    for scenario in web_demo.SCENARIOS:
        data = web_demo.build_evidence(scenario)
        assert data['repair'] == baseline['repair']
        first, final = data['repair']['history']
        assert first['observed_evaluation']['is_safe'] is False
        assert final['observed_evaluation']['is_safe'] is True
        assert final['goal_met'] is True
        assert first['search']['witness']['state_provenance'] == 'OBSERVED_STATE'
        assert 'within the budget' in final['search']['message']


def test_offline_ui_behavior(tmp_path):
    import shutil
    import subprocess
    from pathlib import Path

    node = shutil.which('node')
    if not node:
        pytest.skip('Node is required for the offline DOM harness')
    fixtures = tmp_path / 'evidence.json'
    fixtures.write_text(json.dumps({s: web_demo.build_evidence(s) for s in web_demo.SCENARIOS}))
    result = subprocess.run([node, 'tests/web_demo_ui.cjs', str(fixtures)],
                            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_judge_claims_and_default_scenario():
    html = web_demo.route('/')[2].decode()
    assert '<option value="replacement" selected>' in html
    assert 'Right state. Wrong actions.' in html
    assert '2 successful guided repairs' in html
    assert html.count('2/24') == 2
    assert html.count('0/21') == 2
    assert 'held-out collection · incomplete' in html
    assert '1 response-schema failure' in html
    assert 'shared initial successes' in html
    assert 'synthetic finite-domain results' in html
    assert 'not general reliability or safety guarantees' in html
    assert 'Historical findings, not recomputed by this page' in html
    assert 'No live model inference' in html
    # Public API defaults and provenance remain compatible; only UI selection changes.
    assert web_demo.build_evidence()['scenario'] == 'delete'
