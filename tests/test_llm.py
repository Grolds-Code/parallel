"""All HTTP is mocked; no credentials or network services are needed."""
from dataclasses import asdict, replace
import io
import json
from urllib.error import HTTPError, URLError

import pytest

from parallel_agent import (
    Customer, DatabaseState, DeleteCustomer, Disposition, Proposal, repair_proposal,
)
from parallel_agent.llm import (
    LLMConfig, LLMConfigurationError, LLMConnector, LLMResponseError, LLMTransportError,
    _NoRedirect,
)

CONFIG = LLMConfig("https://inference.invalid/v1", "test-model", "test-credential")
RECORD = asdict(Customer("b"))
GOOD = {"operations": [{"type": "create_customer", "customer": RECORD}]}


@pytest.fixture
def http(monkeypatch):
    calls = []
    responses = []

    class Response(io.BytesIO):
        status = 200

        def read(self, size=-1):
            assert size > 0  # No unbounded HTTP reads.
            return super().read(size)

    class Opener:
        def open(self, request, timeout):
            calls.append((request, timeout))
            response = responses.pop(0)
            if isinstance(response, Exception):
                raise response
            return Response(response)

    def build_test_opener(*handlers):
        assert any(isinstance(handler, _NoRedirect) for handler in handlers)
        return Opener()

    monkeypatch.setattr("parallel_agent.llm.build_opener", build_test_opener)

    def queue(proposal=GOOD, *, content=None, **choice_overrides):
        choice = {"finish_reason": "stop", "message": {
            "role": "assistant", "content": json.dumps(proposal) if content is None else content}}
        choice.update(choice_overrides)
        responses.append(json.dumps({"choices": [choice]}).encode())

    return calls, responses, queue


def test_initial_all_operations_and_request(http):
    calls, _, queue = http
    queue({"operations": [*GOOD["operations"],
                          {"type": "replace_customer", "customer": RECORD},
                          {"type": "delete_customer", "customer_id": "b"}]})
    state = DatabaseState()
    proposal = LLMConnector(CONFIG).generate("Create, replace, delete b", state)
    assert len(proposal.operations) == 3
    assert proposal.operations[-1] == DeleteCustomer("b")
    request, timeout = calls[0]
    assert request.full_url == "https://inference.invalid/v1/chat/completions"
    assert request.get_header("Authorization") == "Bearer test-credential"
    assert timeout == 30
    payload = json.loads(request.data)
    assert payload["model"] == "test-model"
    assert payload["stream"] is False
    assert payload["response_format"] == {"type": "json_object"}
    data = json.loads(payload["messages"][1]["content"])
    assert data["supplied_state"] == {"customers": []}
    assert state == DatabaseState()
    assert "test-credential" not in repr(CONFIG)


@pytest.mark.parametrize("protected", [False, True])
def test_successful_repair_preserves_provenance(http, protected):
    calls, _, queue = http
    queue()
    state = DatabaseState((Customer("a", protected),))
    bad = Proposal((DeleteCustomer("missing"), DeleteCustomer("a")))
    result = repair_proposal(state, bad, LLMConnector(CONFIG).for_goal("Create b"),
                             search_budget_per_iteration=30, max_search_evaluations=100,
                             goal=lambda e: e.simulation.final_state.get("b") is not None)
    assert result.disposition is Disposition.PASSED_BOUNDED_CHECKS
    assert result.repair_attempts == 1
    assert state.get("a").active_subscription is protected
    data = json.loads(json.loads(calls[0][0].data)["messages"][1]["content"])
    assert data["supplied_state"]["customers"][0]["active_subscription"] is protected
    step = data["history"][0]
    assert step["simulation_errors"][0]["error"] == "not_found"
    assert step["goal"] == {"met": False, "error": None}
    assert "goal_not_met" in step["failure_kinds"]
    if protected:
        assert step["observed_violations"]
        assert step["counterfactual_witness"] is None
        assert step["observed_witness"]["state_provenance"] == "OBSERVED_STATE"
    else:
        assert step["observed_violations"] == []
        assert step["observed_witness"] is None
        witness = step["counterfactual_witness"]
        assert witness["state_provenance"] == "COUNTERFACTUAL_STATE"
        assert any(c["active_subscription"] for c in witness["initial_state"]["customers"])


@pytest.mark.parametrize("content", ["not json", "```json\n{}\n```", "[]", "null",
    '{"operations": [], "operations": []}', '{"operations": NaN}',
    '{"operations": [], "is_safe": true}', '{"operations": {}}',
    '{"operations": [null]}', '{"operations": [{"type":"execute_sql"}]}',
    '{"operations": [{"type":"delete_customer","customer_id":" "}]}',
    '{"operations": [{"type":"create_customer","customer":{}}]}'])
def test_invalid_json_and_schema(http, content):
    http[2](content=content)
    with pytest.raises(LLMResponseError):
        LLMConnector(CONFIG).generate("goal", DatabaseState())


@pytest.mark.parametrize("field,value", [("active_subscription", 1), ("unresolved_refund", "false"),
                                         ("open_support_case", None), ("customer_id", 7)])
def test_malformed_customer(http, field, value):
    http[2]({"operations": [{"type": "replace_customer", "customer": {**RECORD, field: value}}]})
    with pytest.raises(LLMResponseError):
        LLMConnector(CONFIG).generate("goal", DatabaseState())


def test_missing_credentials_and_env(monkeypatch):
    monkeypatch.setenv("PARALLEL_LLM_BASE_URL", CONFIG.base_url)
    monkeypatch.setenv("PARALLEL_LLM_MODEL", CONFIG.model)
    monkeypatch.delenv("PARALLEL_LLM_API_KEY", raising=False)
    with pytest.raises(LLMConfigurationError, match="api_key"):
        LLMConfig.from_env()
    monkeypatch.setenv("PARALLEL_LLM_API_KEY", CONFIG.api_key)
    assert LLMConfig.from_env() == CONFIG


@pytest.mark.parametrize("error,match", [
    (TimeoutError("secret"), "timed out"), (URLError(TimeoutError()), "timed out"),
    (URLError("secret"), "connection failed"), (OSError("secret"), "connection failed"),
    (HTTPError(CONFIG.base_url, 500, "secret", {}, None), "HTTP 500"),
    (HTTPError(CONFIG.base_url, 401, "secret", {}, None), "HTTP 401"),
    (HTTPError(CONFIG.base_url, 302, "secret", {}, None), "HTTP 302"),
])
def test_transport_failures_sanitized(http, error, match):
    http[1].append(error)
    with pytest.raises(LLMTransportError, match=match) as caught:
        LLMConnector(CONFIG).generate("goal", DatabaseState())
    assert "secret" not in str(caught.value)
    assert len(http[0]) == 1  # No implicit retries.


@pytest.mark.parametrize("override", [
    {"finish_reason": "length"}, {"finish_reason": "tool_calls"},
    {"message": {"role": "assistant", "content": "{}", "tool_calls": [{}]}},
    {"message": {"role": "assistant", "content": None}},
    {"message": {"role": "assistant", "content": "{}", "refusal": "no"}},
])
def test_incomplete_or_tool_responses(http, override):
    http[2](**override)
    with pytest.raises(LLMResponseError):
        LLMConnector(CONFIG).generate("goal", DatabaseState())


@pytest.mark.parametrize("raw", [b'{}', b'null', b'{"choices":[]}', b'\xff', b'[' * 2000])
def test_malformed_envelope(http, raw):
    http[1].append(raw)
    with pytest.raises(LLMResponseError):
        LLMConnector(CONFIG).generate("goal", DatabaseState())


def test_limits_and_no_truncation(http):
    http[1].append(b"x" * 101)
    with pytest.raises(LLMResponseError, match="Response exceeds"):
        LLMConnector(replace(CONFIG, max_response_bytes=100)).generate("goal", DatabaseState())
    with pytest.raises(LLMResponseError, match="Request exceeds"):
        LLMConnector(replace(CONFIG, max_request_bytes=10)).generate("goal", DatabaseState())
    assert len(http[0]) == 1
    http[2]({"operations": GOOD["operations"] * 2})
    with pytest.raises(LLMResponseError, match="limit"):
        LLMConnector(replace(CONFIG, max_operations=1)).generate("goal", DatabaseState())


def test_connector_failure_closes_repair(http):
    http[2](content="invalid")
    result = repair_proposal(DatabaseState(), Proposal((DeleteCustomer("missing"),)),
                             LLMConnector(CONFIG).for_goal("goal"))
    assert result.disposition is Disposition.REPAIR_ERROR
    assert result.accepted_proposal is None


@pytest.mark.parametrize("kwargs", [
    {"timeout_seconds": float("nan")}, {"timeout_seconds": 0}, {"max_operations": True},
    {"base_url": "file:///tmp/model"}, {"base_url": "https://user:secret@example.com"},
    {"base_url": "https://example.com?key=secret"}, {"api_key": "secret\nheader"},
])
def test_invalid_configuration(kwargs):
    with pytest.raises(LLMConfigurationError):
        replace(CONFIG, **kwargs)


def test_redirects_disabled():
    assert _NoRedirect().redirect_request(None, None, 302, None, None, "https://other.invalid") is None


@pytest.mark.parametrize("url", [
    "http://example.com/v1", "http://localhost/v1", "http://localhost.evil.invalid",
    "http://127.0.0.1.evil.invalid", "http://127.1", "http://2130706433",
    "http://0177.0.0.1", "http://0x7f000001", "http://0.0.0.0",
    "http://[::]", "http://[::ffff:127.0.0.1]", "http://[::1%25eth0]",
    "http://192.168.1.1", "http://169.254.169.254", "http://[2001:db8::1]",
    "http://127.0.0.1:bad", "http://127.0.0.1:65536",
    "http://user:secret@127.0.0.1", "http://@127.0.0.1",
    "http://127.0.0.1?secret", "http://127.0.0.1#secret",
    " http://127.0.0.1", "http://127.0.0.1 /v1", "http://127.0.0.1\\@evil.invalid",
])
def test_insecure_or_ambiguous_endpoints_rejected(url):
    with pytest.raises(LLMConfigurationError) as caught:
        replace(CONFIG, base_url=url)
    assert "secret" not in str(caught.value)
    assert url not in str(caught.value)


@pytest.mark.parametrize("url", [
    "https://inference.invalid/v1", "http://127.0.0.1:8080/v1",
    "http://127.0.0.2/v1", "http://[::1]:8080/v1",
])
def test_secure_or_literal_loopback_endpoints(http, url):
    http[2]()
    LLMConnector(replace(CONFIG, base_url=url)).generate("goal", DatabaseState())
    assert http[0][0][0].full_url == url + "/chat/completions"


@pytest.mark.parametrize("factory", [Customer, DeleteCustomer])
@pytest.mark.parametrize("value", [None, True, 1, 1.5, [], {}, "", " \t"])
def test_model_identifiers_reject_invalid_runtime_values(factory, value):
    with pytest.raises((TypeError, ValueError)):
        factory(value)


@pytest.mark.parametrize("field", ["active_subscription", "unresolved_refund", "open_support_case"])
@pytest.mark.parametrize("value", [0, 1, "false", None, [], {}])
def test_model_flags_require_boolean(field, value):
    with pytest.raises(TypeError):
        Customer("a", **{field: value})


@pytest.mark.parametrize("value", [None, True, 1, [], {}])
def test_malformed_delete_identifier(http, value):
    http[2]({"operations": [{"type": "delete_customer", "customer_id": value}]})
    with pytest.raises(LLMResponseError):
        LLMConnector(CONFIG).generate("goal", DatabaseState())


@pytest.mark.parametrize("raw", [
    b'{"choices":[null]}', b'{"choices":[{"message":[]}]}',
    b'{"choices":[],"choices":[]}', b'{"choices":NaN}',
])
def test_additional_malformed_envelopes(http, raw):
    http[1].append(raw)
    with pytest.raises(LLMResponseError):
        LLMConnector(CONFIG).generate("goal", DatabaseState())


@pytest.mark.parametrize("protected", [False, True])
def test_serialized_repair_traces_discriminate_operations(http, protected):
    from parallel_agent import CreateCustomer, ReplaceCustomer
    from parallel_agent.verifier import evaluate

    state = DatabaseState((Customer("a", protected),))
    proposal = Proposal((CreateCustomer(Customer("b")), ReplaceCustomer(Customer("a")),
                         DeleteCustomer("a"), CreateCustomer(Customer("b"))))
    kinds = ["create_customer", "replace_customer", "delete_customer", "create_customer"]
    simulation = evaluate(state, proposal).simulation
    serialized = json.loads(json.dumps(asdict(simulation)))
    assert [c["operation"]["type"] for c in serialized["changes"]] == kinds
    assert [op["type"] for op in serialized["proposal"]["operations"]] == kinds

    http[2]()
    repair_proposal(state, proposal, LLMConnector(CONFIG).for_goal("goal"),
                    search_budget_per_iteration=100, max_search_evaluations=200)
    data = json.loads(json.loads(http[0][0][0].data)["messages"][1]["content"])
    step = data["history"][0]
    observed = step["observed_simulation"]
    assert observed["state_provenance"] == "OBSERVED_STATE"
    assert [c["operation"]["type"] for c in observed["trace"]] == kinds
    assert step["simulation_errors"][0]["operation"]["type"] == "create_customer"
    key = "observed_witness" if protected else "counterfactual_witness"
    witness = step[key]
    assert witness is not None
    assert [c["operation"]["type"] for c in witness["trace"]] == kinds
    assert [op["type"] for op in witness["proposal"]["operations"]] == kinds
    assert witness["state_provenance"] == ("OBSERVED_STATE" if protected else "COUNTERFACTUAL_STATE")
    # Clearing protection in a replacement does not erase the deletion evidence.
    violation = witness["violations"][0]["counterexample"]
    assert any(violation["protected_record"][f] for f in
               ("active_subscription", "unresolved_refund", "open_support_case"))
    assert not any(violation["record_before_delete"][f] for f in
                   ("active_subscription", "unresolved_refund", "open_support_case"))


def test_identifier_subclasses_cannot_override_identity():
    class MisleadingIdentifier(str):
        def strip(self):
            return "valid"

    for factory in (Customer, DeleteCustomer):
        with pytest.raises(ValueError):
            factory(MisleadingIdentifier(""))


@pytest.mark.parametrize("code", [301, 302, 303, 307, 308])
def test_redirect_handler_blocks_every_redirect(code):
    from urllib.request import Request

    request = Request(CONFIG.base_url, data=b"{}",
                      headers={"Authorization": "Bearer " + CONFIG.api_key})
    assert _NoRedirect().redirect_request(
        request, None, code, "redirect", {}, "http://other.invalid") is None
