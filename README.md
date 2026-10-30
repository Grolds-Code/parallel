# PARALLEL

PARALLEL is a counterexample-guided agent verification and repair system. It
uses a Python 3.12+ in-memory simulator, safety verifier, and bounded search for
proposed customer operations, with an optional provider-independent
chat-completions connector.
There are no runtime dependencies or database writes. The deterministic core and
test suite require no model calls, external APIs, or credentials.

## Proposed project license

The unmodified [Apache License 2.0](LICENSE) and `Apache-2.0` package metadata
are proposed for owner review before public publication, assuming the author
owns all original project code.

This proposal applies to original PARALLEL code. It does not relicense Meta Llama
models or other third-party materials. Model weights are not included. Keep the
separate [Meta Llama attribution](deployment/modal/Notice) and
[model licensing documentation](deployment/modal/README.md) with the optional
Llama deployment; the project's Apache license does not replace those terms.

## Reproduce

With Python 3.12+ and uv installed, install the exact dependencies in `uv.lock`:

```sh
uv sync --locked --group dev
uv run --no-sync pytest -q
```

Dependency installation may require package downloads. In an already provisioned
checkout, run entirely locally:

```sh
.venv/bin/python -m pytest -q
```

Node.js is optional for the browser UI test harness (`tests/web_demo_ui.cjs`),
which pytest runs automatically when `node` is on PATH and otherwise skips.
Use Node.js 24 (also configured in CI). The harness uses Node's built-in modules
and a simulated DOM; no npm packages, browser installation, or browser downloads
are needed. Install Node to include this test in a complete offline run.

GitHub Actions installs the locked Python development dependencies and runs the
same suite with Node enabled. Installation needs package downloads; the tests
need no credentials, paid services, model downloads, or live inference.

Tests include exhaustive protection-flag combinations, invalid operations,
container aliasing, frozen records, transient deletions, and attempts to clear
protection. Hypothesis runs 250 deterministic generated state-machine examples
against an independent reference implementation and 100 storage-order examples.
It shrinks failures to reproducible counterexamples.

## Usage

```python
from parallel_agent import Customer, DatabaseState, DeleteCustomer, Proposal, evaluate

state = DatabaseState((Customer("customer-1", active_subscription=True),))
proposal = Proposal((DeleteCustomer("customer-1"),))
result = evaluate(state, proposal)
assert not result.is_safe
assert result.counterexamples[0].customer_id == "customer-1"
assert state.get("customer-1") is not None  # input is unchanged
```

## Architecture and semantics

- `models.py`: frozen, slotted customer records, database snapshots, typed create,
  replace, and delete operations, and ordered proposals. Runtime validation
  rejects blank IDs, duplicate records, non-boolean flags, and unknown operations.
  Input collections are copied to tuples. States use canonical ID ordering;
  proposal order remains significant. IDs are case-sensitive opaque strings.
- `simulator.py`: copies the initial state into a private working dictionary and
  applies operations sequentially. Its immutable trace records operation index,
  ID, before/after records, and errors for every attempted operation. Creating an
  existing ID or replacing/deleting an absent ID is an explicit error and no-op;
  later operations are still simulated. Replacement replaces the entire record.
- `verifier.py`: checks successful deletions against three explicit constraints:
  active subscriptions, unresolved refunds, and open support cases. Protection
  observed at the start or during a proposal is sticky for that ID throughout
  the proposal. Each violated rule produces a concrete witness containing the
  earliest protected record, the record immediately before deletion, the ID,
  rule, and operation index. Output ordering follows operation order, then rule
  declaration order. Multiple violated rules produce multiple witnesses.
- `evaluate` is the public end-to-end entry point. The result contains the
  proposal and full change trace under `result.simulation`, detected violations,
  counterexamples, final simulated state, and `is_safe`. Safety requires both
  zero operation errors and zero constraint violations. `simulate` and `verify`
  can also be tested separately.

Unsafe deletions are intentionally applied to the **simulated** state, so the
verifier can explain their consequences. The final state of a rejected proposal
must never be treated as approval to execute. Immutable records may be shared
between snapshots, while mutable working storage is always private.

## Scope and limitations

This is a deterministic execution model, not a production executor or formal
proof system. It provides no persistence, transactions against a live database,
concurrency control, or authorization. The optional LLM connector proposes plans
but does not execute them. A future
executor must revalidate against the current state and use an atomic commit
protocol to avoid stale-snapshot races.

Protection flags are treated as input assumptions, not independently verified
real-world facts. Search-generated flags are hypothetical. The current rules prohibit deletion;
they do not prohibit changing flags or independently authorize such changes.
Clearing protection in one proposal and deleting in a later proposal requires
an upstream authorization policy. The sticky policy is intentionally conservative
within one proposal, even if a refund or support case is legitimately resolved.

`verify` expects an authentic trace from `simulate`; manually constructed traces
are not authenticated or replay-validated. Use `evaluate` for untrusted proposed
operations after constructing the validated models. Frozen dataclasses protect
ordinary Python use, not malicious code using reflection. Result classes are
internal output structures rather than a serialized wire protocol.

The model stores only IDs and three boolean protection facts. It does not model
subscription counts, refunds, support entities, cascades, or external effects.
Snapshots are in memory; canonical sorting costs O(n log n), and state lookup is
linear. New operation types require explicit simulator handling and test coverage;
new protection rules should be added alongside witness and reference-model tests.

## Bounded counterexample search

```python
from parallel_agent import search_counterexample

result = search_counterexample(state, proposal, budget=100)
print(result.message)
if result.witness is not None:
    witness = result.witness
    print(witness.state_provenance)  # OBSERVED_STATE or COUNTERFACTUAL_STATE
    replay = evaluate(witness.initial_state, witness.proposal)
    assert replay.simulation.changes == witness.trace
    assert replay.violations == witness.violations
    assert witness.cases_evaluated == result.cases_evaluated
```

`search.py` reuses `evaluate` and stops at the first deletion-constraint violation.
The immutable witness includes the initial state, proposal, complete execution
trace (including errors), all violations, number of evaluated cases, and required
`state_provenance` (`StateProvenance` from `parallel_agent.search`). Operation
errors alone are not constraint witnesses; a search miss does not imply that the
proposal passes `evaluate`.

Interpret evidence according to its scope:

- **Observed violation:** an `OBSERVED_STATE` witness starts from the original
  supplied database snapshot. The proposed operations violate a constraint in
  that simulation. This does not mean the deletion actually happened or that
  flags introduced by the proposal already exist in the real world.
- **Counterfactual vulnerability:** a `COUNTERFACTUAL_STATE` witness starts from
  an alternative candidate generated by search. It shows how the proposal could
  violate a constraint under those assumptions. Hypothetical customer presence
  and flags are not evidence about actual customers. The supplied snapshot may
  pass `evaluate` even when an alternative produces a witness.
- **Operation error:** creating an existing customer or replacing/deleting a
  missing customer fails in the simulation. An error makes that evaluation's
  `is_safe` false, but alone is not a deletion-constraint counterexample. Errors
  can coexist with violations in a witness's complete replay trace.
- **No discovered counterexample:** a missing witness only says the evaluated
  candidates yielded no constraint violation. It does not assert absence of
  operation errors, universal safety, or permission to execute, even on domain
  exhaustion. With zero budget, nothing was evaluated.

Provenance labels the **initial state**, not the origin of every protected record
in the trace. Replaying either classification through `evaluate` yields the same
trace and violations; the verifier treats both as inputs under the same rules.

The required nonnegative integer `budget` caps the number of simulations. Zero
performs no evaluations. Candidates are generated lazily, starting with the
supplied snapshot. For every ID appearing in the snapshot or proposal, the search
considers absence and all eight combinations of subscription, refund, and support
flags. Each ID's options put its original record (or absence) first, followed by
remaining options: absence, then flag masks 0 through 7, with subscription as bit
0, refund as bit 1, and support as bit 2. IDs are sorted lexicographically and the
rightmost ID varies fastest. States are unique and input ordering cannot change
the witness. No unrelated IDs are invented.

The domain contains `9 ** number_of_ids` states (one empty state when there are no
IDs). Search stops when that domain is exhausted, the budget is reached, or a
witness is found. The budget bounds evaluations, not input size or time per
simulation. Even after exhausting the domain, a negative result reports only
**“No counterexample found within the budget.”** It never establishes universal
safety or approves execution.

## Counterexample-guided repair (offline)

`repair_proposal` accepts an injectable local callable. The optional `LLMConnector`
translates its structured request into a prompt and parses a typed `Proposal`.
Only explicit connector calls make network requests; the repair engine itself
never makes network calls or executes database writes.

```python
from parallel_agent import CreateCustomer, Disposition, repair_proposal

# Preserve the actual task: ensure a new customer exists.
def goal(evaluation):
    return evaluation.simulation.final_state.get("new-customer") is not None

def offline_repair(request):
    evidence = request.evidence
    # Inspect evidence.failures, observed_evaluation, and search.witness here.
    return Proposal((CreateCustomer(Customer("new-customer")),))

result = repair_proposal(
    state, proposal, offline_repair,
    max_repair_attempts=3,
    search_budget_per_iteration=100,
    max_search_evaluations=400,
    goal=goal,
)
if result.disposition is Disposition.PASSED_BOUNDED_CHECKS:
    print(result.accepted_proposal)  # candidate only, not execution approval
print(result.detail)
```

Every distinct candidate is independently simulated and verified against the
**original supplied state**, then searched over the existing bounded candidate
domain. Prior simulated final states and hypothetical witnesses never replace
that state. History entries include the proposal, full observed evaluation,
search result and replayable witness, goal outcome, and separate failure kinds:
observed constraint violations, simulation errors, counterfactual vulnerabilities,
and unmet goals. Witness provenance remains explicit. `OBSERVED_STATE` refers to
the supplied snapshot, not authenticated database facts; even that simulation
can introduce hypothetical records. `COUNTERFACTUAL_STATE` records are assumptions,
never verified facts about the supplied database. Search targets constraint
violations, not counterfactual operation errors or counterfactual goal failures.

The goal callable receives the observed `VerificationResult` and must return an
actual `bool`. It can inspect initial/final states and the full trace. A false
result triggers repair; exceptions or malformed goal results terminate closed.
Without a goal, task completion is not checked: empty or no-op proposals can pass
the constraint checks. Supply a task-specific goal to prevent abandonment.

`max_repair_attempts` counts callback invocations, excluding the initial proposal.
The per-iteration search allowance must be positive to proceed, and the remaining
total budget must cover the full allowance before an iteration starts. Only
actual search evaluations are charged; early witness/domain exhaustion can leave
budget available. A completed search with no witness may pass bounded checks even
if it uses the last available evaluation. An insufficient allowance for a needed
iteration terminates with `SEARCH_BUDGET_EXHAUSTED`; zero never yields acceptance.
Independent observed evaluations are additional, bounded by attempts plus one.
These are count limits, not time, memory, input-size, or callback execution limits.

The repair request includes immutable history, the original snapshot, attempt
number, remaining attempts, and remaining search evaluations. Callbacks must
return exactly a validated `Proposal`, not JSON, text, a safety verdict, or a
replacement state. Invalid responses and callback exceptions fail closed. Exact
proposal repetitions (including cycles) terminate without redundant evaluation.
The result records attempts, search evaluations, last valid proposal, history,
and disposition; `accepted_proposal` is `None` on every failure. A repeated or
budget-blocked last proposal has no new history entry because it was not evaluated.
Callbacks are trusted local Python code, not a sandbox for hostile code.

`PASSED_BOUNDED_CHECKS` means only that observed checks and any supplied goal
passed and no constraint witness was found within the configured search budget
and candidate domain. It is not universal safety, a proof of task intent, or
permission to execute. Existing simulator, verifier, and search APIs are unchanged.

## Optional LLM connector

`LLMConnector` supports the OpenAI-compatible `POST /chat/completions` protocol,
independently of the model vendor. Configure the API base URL (including any
`/v1` prefix, excluding `/chat/completions`), model identifier, and credential.
There are no provider defaults, embedded credentials, SDK dependencies, or calls
at import time. For a separately provisioned endpoint serving Meta Llama:

```sh
export PARALLEL_LLM_BASE_URL='https://your-inference-host.example/v1'
export PARALLEL_LLM_MODEL='meta-llama/Llama-3.3-70B-Instruct'
# Or inject the credential from a secret manager.
read -r -s -p 'Inference API credential: ' PARALLEL_LLM_API_KEY
export PARALLEL_LLM_API_KEY
```

The URL is a placeholder; use the model identifier configured on your endpoint.
This does not provision hosting or download weights. Use HTTPS remotely; HTTP
is supported for local inference. Do not commit credentials or log headers.

```python
from parallel_agent import (
    DatabaseState, Disposition, LLMConfig, LLMConnector, LLMError, repair_proposal,
)

state = DatabaseState()
task = "Create customer new-customer with all protection flags false."
connector = LLMConnector(LLMConfig.from_env())
try:
    initial = connector.generate(task, state)  # Explicit network call; candidate only.
except LLMError as exc:
    print(f"Planning failed: {exc}")
else:
    result = repair_proposal(
        state, initial, connector.for_goal(task),
        max_repair_attempts=3,
        search_budget_per_iteration=100,
        max_search_evaluations=400,
        goal=lambda e: e.simulation.final_state.get("new-customer") is not None,
    )
    if result.disposition is Disposition.PASSED_BOUNDED_CHECKS:
        print(result.accepted_proposal)  # Still not permission to execute.
    else:
        print(result.disposition, result.detail)
```

`for_goal` returns the existing `RepairCallable`; deterministic callbacks and
existing APIs are unchanged. A trusted goal predicate remains necessary:
textual goals do not establish completion. Candidates go through the simulator,
verifier, and bounded search when passed to `repair_proposal`. The connector
only returns validated proposals, never safety verdicts, replacement snapshots,
executable code, tool invocations, or persistent writes.

The exact proposal schema is an object containing only `operations`, an ordered
array of these variants (this illustrates syntax, not an approved plan):

```json
{"operations": [
  {"type": "create_customer", "customer": {
    "customer_id": "new-customer", "active_subscription": false,
    "unresolved_refund": false, "open_support_case": false
  }},
  {"type": "replace_customer", "customer": {
    "customer_id": "new-customer", "active_subscription": false,
    "unresolved_refund": false, "open_support_case": true
  }},
  {"type": "delete_customer", "customer_id": "another-customer"}
]}
```

All fields are required; IDs must be nonblank strings and flags actual JSON
booleans. Local validation rejects extra fields, unsupported operations,
duplicate keys, nonfinite numbers, markdown, malformed records, and safety
claims. Empty proposals are structurally valid, so goal checks must prevent
abandonment. JSON-object mode is requested, but local validation is authoritative.
Exactly one complete assistant response with `finish_reason: "stop"` is required;
truncation, refusals, and tool/function calls are rejected.

Repair requests contain the original `supplied_state` and full iteration history.
`observed_violations`, `simulation_errors`, `counterfactual_witness`,
`observed_witness`, and `goal` are separate fields; `failure_kinds` distinguishes
unmet goals. Witnesses retain provenance, initial state, proposal, trace, and
violations. Simulated final states and trace records are simulation evidence.
Counterfactual presence and flags never enter the observed snapshot. Even
observed simulations can introduce proposed facts that are not real observations.

`LLMConfig` accepts positive `timeout_seconds` (default 30),
`max_response_bytes` (262144), `max_request_bytes` (1048576),
`max_operations` (100), and `max_tokens` (4096). Response reads have a hard byte
cap; oversized requests fail without silently dropping evidence. Redirects are
disabled by default. Explicit `modal_result_redirects=True` permits only verified
same-origin HTTPS 303 result retrievals using GET without replaying the POST.
`total_timeout_seconds` (default `None`, inheriting `timeout_seconds`) bounds the
elapsed transport budget and
`max_result_redirects` (20) caps result hops. See the
[Modal configuration and incident notes](deployment/modal/README.md#observed-live-repair-transport-failure).
There are no automatic retries.

For Modal, configure a 40-minute total deadline with a separate bounded socket
timeout (constructing the connector does not send a request):

```python
from dataclasses import replace
from parallel_agent import LLMConfig, LLMConnector

connector = LLMConnector(replace(
    LLMConfig.from_env(),
    modal_result_redirects=True,
    timeout_seconds=180,
    total_timeout_seconds=2400,
    max_result_redirects=20,
))
```

Configuration, transport, and response failures have distinct `LLMError`
subclasses with sanitized messages omitting response bodies and credentials.
During repair, exceptions produce the existing `REPAIR_ERROR` disposition with
no accepted proposal.

Remaining limitations: endpoints must support JSON-object mode and `max_tokens`;
a live Meta Llama repair round trip has completed, as recorded below; automated
compatibility tests use mocked HTTP. The timeout bounds blocking
socket operations; the separate monotonic total deadline is checked across hops
and bounded body reads. OS DNS resolution and urllib header processing can delay
reporting deadline exhaustion. Request serialization precedes its byte-limit check. LLM output is
not deterministic even at temperature zero. Schema validity does not establish
task correctness or authorization. Providers receive tasks, snapshots, and repair
evidence; choose an endpoint appropriate for that data. There is no persistence,
execution, live-database freshness check, retry, streaming, or universal safety
proof. All connector tests run offline with mocked HTTP.

## Optional Modal deployment recipe

A separate [Modal deployment recipe](deployment/modal/README.md) serves
`unsloth/Llama-3.2-3B-Instruct` through authenticated OpenAI-compatible chat
completions on one scale-to-zero GPU (L4 preferred, A10 fallback). Its result redirects require explicit `LLMConfig` opt-in.
Deployment dependencies are outside the local package; tests use fake Modal and
HTTP implementations and never download weights or start a GPU.

**Built with Llama.** See the [Meta Llama attribution](deployment/modal/Notice).

## Explicit action constraints and the first live repair

The first confirmed live Meta Llama repair reported
`passed_bounded_checks`, **1 repair attempt, 4 search evaluations, and 21.35
seconds**. This confirmed a completed live repair round trip, but exposed an
evaluation gap: the task requested creating demo-002 while preserving demo-001
and said “Do not delete or modify existing customers.” The accepted proposal
contained both `CreateCustomer(demo-002)` and `ReplaceCustomer(demo-001)`, with
identical final field values. The final-state goal passed despite the forbidden
replacement. That historical disposition is not evidence of full task compliance.
The fix was initially tested offline. A subsequent strict live success is
recorded below.

Keep three concerns distinct:

- State invariants and protection rules describe allowed data conditions. The
  existing verifier enforces sticky deletion protections over the simulated trace.
- Requested goals describe task completion. The optional `goal` receives a
  `VerificationResult` and returns a strict boolean. Final-state equality does
  not establish that forbidden operations were avoided.
- Action constraints describe which attempted operations are allowed, even when
  they leave the final state unchanged.

Supply an explicit trusted action checker independently of the goal:

```python
from parallel_agent import forbid_original_customer_operations

result = repair_proposal(
    state, proposal, offline_repair,
    goal=goal,
    action_check=forbid_original_customer_operations,
)
# Also supported by evaluate(state, proposal, action_check=...) and verify(...).
```

An `ActionCheck` receives the immutable `Simulation` and returns a tuple of
`ActionViolation(constraint, operation_index, customer_id, message)`. Empty means
no action violations. Custom checkers should be pure and deterministic. The
built-in helper forbids **every attempted operation** targeting an ID present in
the original snapshot, including identical replacements, failed operations, and
delete/recreate sequences. It permits operations on new IDs. Every repair is
checked against the same original snapshot; protection is never derived from an
intermediate or final state.

Action evidence is separate from deletion violations:
`VerificationResult.action_violations` and `action_error` affect `is_safe`.
Repair history and connector prompts include that evidence and separate failure
kinds. A violation triggers repair if budget permits. Exceptions, malformed
checker results, and evidence referencing a nonexistent operation or mismatched
ID terminate with `action_check_error` and no accepted proposal. As with goal
checker errors, a broken checker is not delegated to the proposal repair callback.

Action checks run on the observed simulation, independently of bounded search,
which continues to search deletion protections only. They consume no search
evaluations. Acceptance requires all supplied action constraints to pass, as
well as existing checks and any goal. Omitting `action_check` preserves existing
behavior. Task text is not automatically translated into constraints: callers
must supply the policy. Trusted callbacks are not sandboxed, and their logical
correctness or determinism cannot be guaranteed by this interface.


## Strict Meta Llama repair: live outcome and OFFLINE REPLAY

The observed strict live run used `unsloth/Llama-3.2-3B-Instruct` on Modal,
starting from `Customer("demo-001", active_subscription=True)` and unsafe proposal
`DeleteCustomer("demo-001")`. The goal was to create demo-002 with all flags false
while preserving demo-001, with `action_check=forbid_original_customer_operations`.

It reported **`passed_bounded_checks`, one model repair attempt, four search
evaluations, and 63.73 seconds elapsed**. Iteration 1 failures were
`action_violation`, `observed_violation`, and `goal_not_met`. Iteration 2 contained
only `CreateCustomer(demo-002)`, with no failures, goal met, and no action
violations. This strict success is separate from the earlier 21.35-second run
whose accepted replacement violated the requested action policy.

In a provisioned checkout:

```sh
uv run --offline python -m parallel_agent.offline_replay
# Or without dependency resolution:
.venv/bin/python -m parallel_agent.offline_replay
```

This is an **OFFLINE REPLAY, not fresh Llama inference**. A fixed local callback
returns the recorded correction. Public `repair_proposal`, `evaluate`, and
`search_counterexample` APIs compute verification outcomes and counterexamples.
Output shows the unsafe proposal, observed-state counterexample, action-policy
failure, corrected proposal, and final verification. Unexpected results or
exceptions exit nonzero. No APIs, GPUs, Modal, credentials, or database writes
are involved.

The replay chooses search limits of 3 per iteration and 6 total, yielding
1 + 3 = 4 repair-loop search evaluations. These are replay settings, not a claim
about unrecorded live budget settings. Independent verification/search reruns
and witness replay add local evaluations beyond the loop count. The 63.73 seconds
belongs only to the historical live run; the replay does not reproduce model
generation or latency. Bounded success is not universal safety or execution approval.

Offline tests, including failure exit handling: `uv run pytest -q`.

## Local browser demonstration

Launch from the repository root:

```sh
uv run --offline python -m parallel_agent.web_demo
```

Open **http://127.0.0.1:8765**. Stop with Ctrl-C; optionally choose another
port with `--port 8766`. The standard-library server binds only to loopback.
No new dependencies, credentials, network services, GPUs, or inference are needed.

The six-step investigation shows the synthetic snapshot, simulated operations,
replayable observed counterexample, original-customer action policy, recorded
Meta Llama create-only correction, and bounded verification. The comparison
selector evaluates unsafe deletion, create plus an identical original-customer
replacement, and create only. The identical replacement meets the final-state
goal but violates the explicit action policy.

`/api/evidence?scenario=delete` (also `replacement` or `create`) computes evidence
using public `simulate`, `verify`, `evaluate`, `search_counterexample`, and
`repair_proposal` APIs. Every proposal starts from the original immutable snapshot.
The repair panel always replays the recorded delete → create-only sequence,
independently of the selected comparison. The callback is **RECORDED MODEL OUTPUT /
OFFLINE REPLAY**, never fresh inference. Search budgets are 3 per iteration and
6 total; the repair loop consumes 4 search evaluations. Independent comparison
checks and witness replay are additional evaluations. Search checks deletion
constraints; goal and action policy are separate observed-state checks.

Computation failures return an error with no verdict; Recompute retries locally.
Static routes are allowlisted, with no arbitrary file access or inference proxy.
This is a local demonstration, not a production web service. Bounded verification
is not universal safety or execution approval. The existing CLI replay remains
available unchanged. Offline backend and route coverage is in
`tests/test_web_demo.py`; run `uv run pytest -q`.

### Optional public CPU-only lab

A separate [public Modal deployment recipe](deployment/modal/PUBLIC_DEMO.md)
defines `parallel-public-demo` with the same offline verification evidence and
allowlisted assets, strict read-only ASGI routes, no GPU or attached secrets,
and one container that scales to zero. It leaves the authenticated
`parallel-llama` service and the loopback development server unchanged.
See the recipe for offline SDK validation, exact deployment/rollback commands,
resource limits and approximate costs. This deployment has not been run.

### Offline evaluation benchmark

The [evaluation protocol](docs/benchmark_protocol.md) documents 40 deterministic
synthetic customer-management tasks, independent goals and operation policies,
metric definitions, and a later controlled Meta Llama repair-versus-retry study.
Run `.venv/bin/python -m parallel_agent.benchmark` from this checkout for a JSON
fixture replay. This invokes no models and reports no model-performance results.
