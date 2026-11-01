# PARALLEL held-out v1 preregistration

Status: offline freeze, no held-out inference or performance results. Fixed intervention:
Git `058eda7bda5aacffbd859a9fbe8ac174a65c37dd`. This protocol adds an evaluator;
it does not change that revision's prompt, model configuration, parser, oracle,
verifier, counterfactual search, or repair loop. The manifest pins their bytes,
all case definitions, evaluator code, this protocol, deployment configuration,
model-visible tasks, reference plans, expected labels, and development exclusions.
`manifest.sha256` seals the manifest. Local hashes detect drift, not malicious
rewriting; archive/sign the freeze externally before any authorized collection.

## Question and estimand

Does fixed v3 guided repair improve independently successful accepted outcomes
relative to simple retry on new synthetic tasks in the existing domain? The
primary estimand is the mean paired guided-minus-retry difference across the
24 preregistered jointly satisfiable tasks, including initial successes and
failed requests. The four impossible-acceptance diagnostics are separate and
never included in repair-failure or primary success denominators. No development
results are included. No generalization claim follows from this preparation.

## Population, provenance, and exclusion

28 tasks, seven families, four tasks each; all are used once in the frozen
sampling order. `heldout_cases.construct` records deterministic construction.
Within-family variants increase the number of necessary operations or stages;
they are not merely identifier or Boolean substitutions.

| Family | New structure and intended challenge | Group |
| --- | --- | --- |
| flag_reconciliation | 3–6 existing records need exact replacements; preserve two mixed-protection records | satisfiable |
| mixed_expansion | 1–4 replacements plus two creations and two preserved records | satisfiable |
| staged_onboarding | 2–5 IDs must first be created with allowed intermediate flags, then replaced | satisfiable |
| optional_action_selection | 3–6 replacements; additional allowed actions would spoil preserved records or add unwanted records | satisfiable |
| mixed_staged_expansion | two replacements, 1–4 staged creations, a direct creation, and preservation | satisfiable |
| bulk_preservation | 3–6 new records with distinct exact flags, preserving three mixed-protection records | satisfiable |
| counterfactual_contraction | 3–6 replacements, protected preservation, and mandatory unprotected deletion | diagnostic |

Construction uses only existing domain semantics. Mixed protected/unprotected
records occur throughout. Replacement is legal even for protected records;
protection restricts deletion. Allowed operations are listed in reverse reference
order, so the allowlist is not a supplied execution plan. An allowlist necessarily
reveals legitimate actions; that is the existing task interface, not reference leakage.

The prior benchmark has 40 case definitions, including duplicate tasks. All
payloads are explicitly retained in `development_exclusions.json` with legacy and
v3 canonical hashes. All three pilots select from that benchmark. The historical
live/offline demo and web demo preserve one existing ID and create one new ID;
that structural family is already excluded regardless of wording, flags, or IDs.
Tests and documentation are inventoried and hashed as development material.
Procedurally generated unit-test inputs are not an enumerated model-task corpus;
unrecorded past prompts or external tasks cannot be exhaustively excluded.

Canonical task hashing uses sorted JSON object keys, ASCII escaping, compact
separators, exact Boolean values, and retained array order over `structured_task`.
Evaluator IDs, families, references, probes and labels are excluded. Novelty checks
also remove wording, identifiers and array order, linking each record's initial
state, target state, equality status, and multiset of allowed operations. A second
coarse fingerprint removes Boolean values. Both fingerprints must be distinct
within the set. Against every prior case, reject coarse record-role edit distance
<=1 (insertion, deletion or substitution costs one). Store nearest prior cases
and distances. Within-family tasks intentionally remain structurally related;
we do not claim 28 unrelated task families or independent random population draws.

The same project and assistant designed the evaluation after inspecting the
development cases. This establishes a prospective, previously uninferred-on
synthetic task holdout relative to recorded development, not independent authorship,
unknown training-data absence, or external validation. The finite domain has only
three operations and eight flag combinations. Scaling composition is part of
novelty; it is not a new domain. No model feedback was used in construction.

## Offline feasibility and diagnostic proof

Each reference must succeed in the independent dictionary interpreter and is
checked separately by the unchanged gate at 16 search states. All 24 main
references pass. They use no deletion, so no sticky-deletion witness can arise;
the oracle separately checks exact state, policy membership and execution errors.
Probes are empty plans and intentionally fail the observed goal; they are solely
fixture checks and are never initial model responses.

For each diagnostic, the exact target omits the lexicographically greatest ID.
Only its deletion is allowed for that ID; no creation/replacement can eliminate
it. The supplied record has no protection, but the third state in the fixed
search assigns active_subscription=True to that ID. Every observed-successful
allowed proposal must delete it, producing that counterfactual violation within
16 evaluations regardless of operation order. Other allowed IDs sort earlier;
no allowed extra ID can push the witness outside the budget. Thus a jointly
successful accepted proposal is impossible, not merely an unsuccessful reference
search. Diagnostics are observed-satisfiable, not observed specification conflicts.

## Frozen collection

Manifest stores case checksum, model-visible task hashes, case order, arm order,
seeds, intended runtime, configured model and endpoint SHA-256 hashes. Neither
credentials nor raw endpoint URLs are persisted. Environment model/URL must match
before constructing a connector. The local deployment source pins weights and
tokenizer revision, chat-template source, vLLM/transformers versions and hardware
choices. This offline work cannot attest the actual remote process, weights or
hardware; that remains a methodological limitation requiring operator confirmation
before authorized collection. No automatic deployment, GPU startup or API access.

Sampling order is a seeded shuffle of all 28 IDs (seed 20261031). Within each
family assign two guided-first and two retry-first orders, then shuffle those
orders with a separate seed 5807. Literal arrays are frozen in the manifest,
so future PRNG-version changes do not change selection. No outcome-based sampling,
replacement tasks, reruns, extra repair attempts, tuning, or optional stopping.
One invocation covers the entire set, with a hard global cap of **84 generation
calls**: 28 shared initials plus at most 28 guided and 28 retry revisions.
Each arm receives the same parsed immutable initial, task facts, original state,
512-token generation cap, 16-operation cap and temperature 0. At most one revision
per arm. Initial gate acceptance skips revision in both arms. Worst-case output
allowance is 43,008 tokens, not expected usage. Complex plans may truncate at this
unchanged v3 limit; record this rather than increase the budget post hoc.

Reuse `V3Connector`, `repair_context_v3`, `retry_context`, and `repair_proposal`
without changing them. Guided receives the existing v3 evidence; retry receives
only mode and its shared previous proposal. No other arm's history, result or
usage enters a request. Each arm starts from the original state. Search budgets
are 16 per iteration and 32 per arm (maximum 1,792 search simulations over both
arms; observed evaluations are additional). No calls on offline validation.
References, expected labels, family names, selection reasons and feasibility
findings are evaluator-only; only `structured_task` crosses the task boundary.

Transport remains the existing authenticated connector, with validated same-origin
HTTPS Modal 303 polling, at most 20 redirects, no transport-generation retries,
180-second request timeout, 2,400-second total request/poll timeout, and 9,600-second
global monotonic deadline. Remaining global time clamps each request. Every attempted
generation, including failed parses/transports, is charged before invocation. Poll
GETs are not generation calls. Deadline/interrupt produces a sanitized partial report;
remote cancellation cannot be guaranteed. Failures never trigger substitute tasks.

Default `python -m parallel_agent.heldout` is entirely offline, validates the freeze,
and prints a deterministic plan and feasibility audit. `--execute` is necessary
for future live use and is not authorized by this document or this preparation.
Do not rerun a partially collected freeze: archive the partial report and explicitly
preregister any later replication as a new run. No automatic resume or report writes.
The CLI emits JSON on stdout; choose a new output path externally, never overwrite
historical reports. Safe serialization reuses v3's redaction and allowlisted metadata;
raw response/error strings, credentials and raw identifiers are not persisted.

## Endpoints and missing data

Primary: accepted AND independent oracle task_success. Failed/missing responses
count as zero; all 24 main tasks stay in the denominator. Recovery: the same
endpoint restricted to main tasks whose shared initial parsed proposal was rejected
by the gate (including observed-successful but gate-rejected initials). Failed
initial requests have no eligibility classification; report those separately.

Secondary: observed independent task success; false acceptance; repeated proposal
stopping; physical inference calls; prompt/completion tokens; search evaluations;
arm wall time and verification time; total call latency. Independently successful
means exact target plus no observed sticky-protection, policy or simulation failure.
Acceptance means only this bounded gate, never universal robustness.

Missing final oracle measurements remain null; observed success is a complete-pair
endpoint with explicit missing counts. Primary success still fails closed. False
acceptance is zero for closed failures; never equate missing responses with observed
success. Report accepted count and false acceptance conditional on acceptance as
well as all-task rate. Token usage is null unless reported for every contributing
call; never infer tokens from text. Charge shared initial calls and tokens half to
each arm, additionally report actual physical totals. Arm latency excludes the
shared initial; shared and total request latency are reported separately. Search
and timing missingness remain explicit. Missing pairs are not imputed for secondary
numeric metrics; record all failure categories and report completeness.

## Paired statistical analysis

Use tasks as pairs. For each endpoint report guided and retry means, paired mean
difference, n, and a deterministic 10,000-resample paired percentile bootstrap 95%
interval (seed 20261031). Binary endpoints additionally report discordant counts
and exact two-sided McNemar p values; the primary p value alone is confirmatory,
all other tests descriptive, with no multiplicity-adjusted claims. Empty strata
are null. All-equal pairs can produce degenerate bootstrap intervals; these do
not establish absence of uncertainty in a broader population.

Report the main 24-task group, separate four-task diagnostic group and every
family. Recovery is undefined in diagnostic analysis. Family estimates (n=4) and
within-template dependence severely limit inference; these confidence intervals
are descriptive conditional on this designed set, not evidence of random sampling
from all tasks. Also provide a family-cluster bootstrap sensitivity interval for
the primary difference across the six main families; only six clusters makes it
unstable. Do not merge development or feasibility-fixture outputs with model
performance. Report incomplete collection before interpreting any estimates.

## Validation and amendment rule

Run full offline pytest, held-out dry run, compileall, and git diff --check.
New tests cover exact/coarse uniqueness, prior exclusions, offline reference
feasibility, diagnostic proof, manifest/source drift, reference and arm isolation,
fixed budgets, failed-call charging, deadlines, sanitization, deterministic selection,
and statistical reproducibility. Existing tests cover frozen connector security.
Any change to cases, protocol, analysis, or limits after freeze creates a new
version; do not quietly regenerate the old freeze, and never tune v3 on these tasks.
