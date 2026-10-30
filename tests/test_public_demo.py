"""ASGI exercised in-process: no listening socket, SDK, model, or cloud service."""
import asyncio
from dataclasses import asdict
import json
import socket

import pytest

from parallel_agent import public_demo, web_demo, llm
from parallel_agent import evaluate, forbid_original_customer_operations


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    def blocked(*args, **kwargs):
        pytest.fail("Public lab attempted network or model access")
    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)
    monkeypatch.setattr(llm.LLMConfig, "from_env", blocked)
    monkeypatch.setattr(llm.LLMConnector, "__init__", blocked)
    monkeypatch.setenv("PARALLEL_LLM_API_KEY", "SECRET-INFERENCE")
    monkeypatch.setenv("MODAL_TOKEN_SECRET", "SECRET-MODAL")
    monkeypatch.setenv("HF_TOKEN", "SECRET-HF")


def request(path="/", query=b"", method="GET", headers=(), body=b"", more=False, **scope):
    events = []
    async def receive():
        return {"type": "http.request", "body": body, "more_body": more}
    async def send(event):
        events.append(event)
    asyncio.run(public_demo.application({
        "type": "http", "path": path, "raw_path": path.encode(),
        "query_string": query, "method": method, "headers": headers, **scope,
    }, receive, send))
    start, end = events
    response_headers = dict(start["headers"])
    assert all(response_headers[k] == v for k, v in public_demo.SECURITY_HEADERS)
    assert b"access-control-allow-origin" not in response_headers
    assert b"set-cookie" not in response_headers
    assert b"SECRET" not in end["body"]
    if method != "HEAD":
        assert int(response_headers[b"content-length"]) == len(end["body"])
    return start["status"], response_headers, end["body"]


@pytest.mark.parametrize("path", ["/", "/app.js", "/style.css"])
def test_only_existing_assets(path):
    status, headers, body = request(path)
    assert status == 200
    assert body == web_demo.route(path)[2]
    head = request(path, method="HEAD")
    assert head[0] == 200 and head[2] == b""
    assert head[1] == headers


@pytest.mark.parametrize("query", [
    b"utm_source=chatgpt.com",
    b"utm_source=chatgpt.com&utm_medium=referral&utm_campaign=launch",
    b"utm_term=bounded-checks&utm_content=link_1.2~demo",
    b"&".join(key + b"=" + b"a" * 128 for key in sorted(public_demo.REFERRAL_KEYS)),
])
def test_homepage_referrals_ignored(query, monkeypatch):
    monkeypatch.setattr(web_demo, "build_evidence", lambda *_: pytest.fail("Referral computed"))
    assert request(query=query) == request()
    assert request(query=query, method="HEAD") == request(method="HEAD")


@pytest.mark.parametrize("query", [
    b"unknown=value", b"utm_other=value", b"scenario=create", b"UTM_SOURCE=chatgpt.com",
    b"utm_source=chatgpt.com&unknown=value", b"utm_source=", b"utm_source",
    b"utm_source=a&utm_source=a", b"utm_source=a&utm_source=b",
    b"&utm_source=a", b"utm_source=a&", b"utm_source=a&&utm_medium=b",
    b"utm_source=a=b", b"utm_source=a;utm_medium=b", b"utm_source=a+b",
    b"utm_source=a b", b"utm_source=<script>", b"utm_source=a#fragment",
    b"%75tm_source=a", b"utm_source=%61", b"utm_source=%", b"utm_source=%GG",
    b"utm_source=%00", b"utm_source=%26scenario%3Dcreate", b"utm_source=%ff",
    b"utm_source=\xff", b"utm_source=\x00", b"utm_source=a\r\n", b"utm_source=" + b"a" * 129,
])
def test_invalid_homepage_referrals(query, monkeypatch):
    monkeypatch.setattr(web_demo, "route", lambda *_: pytest.fail("Invalid request dispatched"))
    assert request(query=query)[0] == 400


def test_homepage_referral_target_limit():
    assert request(query=b"x" * 1024)[0] == 400
    assert request(query=b"x" * 1025)[0] == 414


@pytest.mark.parametrize("path,status", [
    ("/app.js", 400), ("/style.css", 400), ("/api/evidence", 400),
    ("/missing", 404), ("/api/inference", 404), ("/api/evidence/", 404),
])
def test_referrals_not_accepted_elsewhere(path, status, monkeypatch):
    monkeypatch.setattr(web_demo, "build_evidence", lambda *_: pytest.fail("Invalid request computed"))
    assert request(path, b"utm_source=chatgpt.com")[0] == status
    assert request(path, b"x" * 65)[0] == 414


@pytest.mark.parametrize("scenario,safe,goal", [
    ("delete", False, False), ("replacement", False, True), ("create", True, True),
])
def test_real_engine_deterministic_and_model_independent(scenario, safe, goal, monkeypatch):
    calls = []
    real = web_demo.evaluate
    def observed(state, proposal, **kwargs):
        calls.append((state, proposal))
        return real(state, proposal, **kwargs)
    monkeypatch.setattr(web_demo, "evaluate", observed)
    result = request("/api/evidence", f"scenario={scenario}".encode())
    assert result[0] == 200 and calls
    data = json.loads(result[2])
    state, proposal = calls[0]
    computed = evaluate(state, proposal, action_check=forbid_original_customer_operations)
    assert data["evaluation"]["simulation"] == json.loads(json.dumps(asdict(computed.simulation)))
    assert data["evaluation"]["is_safe"] == computed.is_safe == safe
    assert data["evaluation"]["goal_met"] is goal
    assert data["search"]["budget"] == 3
    assert data["repair"]["search_evaluations"] == 4
    assert "OFFLINE REPLAY" in data["source"]
    assert "Historical live run" in data["historical_run"]["label"]
    assert data["historical_run"]["elapsed_seconds"] == 63.73
    assert request("/api/evidence", f"scenario={scenario}".encode()) == result


def test_default_and_head_evidence():
    assert request("/api/evidence") == request("/api/evidence", b"scenario=delete")
    assert request("/api/evidence", method="HEAD")[2] == b""


@pytest.mark.parametrize("query", [
    b"scenario=", b"scenario=unknown", b"scenario=DELETE", b"scenario=null",
    b"scenario=create&scenario=delete", b"scenario=create&scenario=create",
    b"scenario=create&", b"scenario", b"budget=999999", b"code=print(1)",
    b"model=other", b"proposal={}", b"scenario=%63reate", b"scenario=create%00",
    b"scenario=create;budget=1", b"scenario=create&x=1", b"%ff", b"\xff",
    b"scenario=create&utm_source=chatgpt.com",
    b"utm_source=chatgpt.com&scenario=create",
    b"scenario=create%26utm_source%3Dchatgpt.com",
])
def test_invalid_query_never_computes(query, monkeypatch):
    monkeypatch.setattr(web_demo, "build_evidence", lambda *_: pytest.fail("Invalid request computed"))
    assert request("/api/evidence", query)[0] == 400


@pytest.mark.parametrize("path", [
    "/.env", "/README.md", "/web_demo.py", "/../web_demo.py", "/web_assets/",
    "/%2e%2e/.env", "//api/evidence", "/api/evidence/", "/api/inference",
    "/v1/chat/completions", "/execute", "/api/search", "/docs", "/openapi.json",
    "/hf-cache", "/proc/self/environ", "/app.js/anything", "/app.js\x00",
])
def test_no_other_routes(path):
    assert request(path)[0] == 404


def test_noncanonical_raw_path_and_limits():
    assert request("/app.js", raw_path=b"/%61pp.js")[0] == 404
    assert request("/" + "x" * 129)[0] == 414
    assert request("/api/evidence", b"x" * 65)[0] == 414
    assert request("/app.js", b"x=1")[0] == 400


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE", "OPTIONS", "TRACE", "CONNECT"])
def test_non_read_methods(method, monkeypatch):
    monkeypatch.setattr(web_demo, "build_evidence", lambda *_: pytest.fail("Write request computed"))
    status, headers, _ = request("/api/evidence", method=method, body=b'{"code":"evil"}')
    assert status == 405 and headers[b"allow"] == b"GET, HEAD"


@pytest.mark.parametrize("kwargs", [
    {"body": b"{}"}, {"more": True}, {"headers": [(b"content-length", b"1")]},
    {"headers": [(b"content-length", b"-1")]},
    {"headers": [(b"transfer-encoding", b"chunked")]},
    {"headers": [(b"content-length", b"0"), (b"content-length", b"100")]},
])
def test_bodies_rejected(kwargs):
    assert request("/api/evidence", **kwargs)[0] == 400


@pytest.mark.parametrize("api", ["simulate", "verify", "evaluate", "search_counterexample", "repair_proposal",
                                 "forbid_original_customer_operations"])
def test_errors_redacted_and_no_stale_verdict(monkeypatch, api):
    assert request("/api/evidence")[0] == 200
    def broken(*args, **kwargs):
        raise RuntimeError("SECRET /private/path")
    monkeypatch.setattr(web_demo, api, broken)
    status, _, body = request("/api/evidence")
    assert status == 500 and b"is_safe" not in body and b"no verdict" in body


def test_asset_failure_redacted(monkeypatch):
    monkeypatch.setattr(web_demo, "route", lambda _: (_ for _ in ()).throw(OSError("SECRET")))
    assert request()[0] == 500


def test_lifespan_and_websocket():
    async def exercise(scope, incoming):
        sent = []
        async def receive():
            return incoming.pop(0)
        async def send(event):
            sent.append(event)
        await public_demo.application(scope, receive, send)
        return sent
    assert asyncio.run(exercise({"type": "lifespan"}, [
        {"type": "lifespan.startup"}, {"type": "lifespan.shutdown"},
    ])) == [{"type": "lifespan.startup.complete"}, {"type": "lifespan.shutdown.complete"}]
    assert asyncio.run(exercise({"type": "websocket"}, [])) == [
        {"type": "websocket.close", "code": 1008}]


@pytest.mark.parametrize("fault", ["goal_error", "repair_error"])
def test_retained_engine_diagnostics_are_not_published(monkeypatch, fault):
    from dataclasses import replace
    real = web_demo.repair_proposal
    def faulty(*args, **kwargs):
        result = real(*args, **kwargs)
        if fault == "goal_error":
            return replace(result, history=(replace(result.history[0], goal_error="SECRET"),))
        return replace(result, disposition="repair_error", detail="SECRET")
    monkeypatch.setattr(web_demo, "repair_proposal", faulty)
    status, _, body = request("/api/evidence")
    assert status == 500 and b"is_safe" not in body


@pytest.mark.parametrize("disconnect", [False, True])
def test_receive_timeout_and_disconnect(disconnect):
    events = []
    async def receive():
        if disconnect:
            return {"type": "http.disconnect"}
        raise TimeoutError("SECRET")
    async def send(event):
        events.append(event)
    asyncio.run(public_demo.application({"type": "http", "method": "GET"}, receive, send))
    if disconnect:
        assert not events
    else:
        assert events[0]["status"] == 408
        assert b"SECRET" not in events[1]["body"]
        assert all(dict(events[0]["headers"])[k] == v for k, v in public_demo.SECURITY_HEADERS)
