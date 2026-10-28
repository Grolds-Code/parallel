# PARALLEL deterministic execution foundation

A Python 3.12+ in-memory simulator and safety verifier for proposed customer
operations. No runtime dependencies, model calls, external APIs, credentials,
or database writes are involved.

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
concurrency control, authorization, model planner, or repair loop. A future
executor must revalidate against the current state and use an atomic commit
protocol to avoid stale-snapshot races.

Protection flags are trusted input facts. The current rules prohibit deletion;
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
