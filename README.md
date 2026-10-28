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
