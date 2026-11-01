# Pre-commit release safety audit — 2026-11-01

Recommendation: PASS for the exact proposed staging set below. This is a local
content/integrity audit, not publication approval or a hosted CI result. Nothing
was staged, committed, pushed, deployed, or sent to inference/Modal services.

## Scope and classification

All 23 initially modified/untracked nonignored files were inventoried and read
or structurally inspected, including complete JSON values and all ten regular
archive members. Credential-pattern and URL/email scans covered their full bytes;
source review distinguished environment lookups and fake test inputs from secrets.
No actual credentials, authentication header values, personal customer records,
raw private reports, or unintended secret literals were found. Pattern scans are
not proof against every possible encoded secret. Existing author attribution in
the pinned project's package metadata is public attribution, not customer data.

Categories overlap: required CI material can also be historical research.

| Exact path | Classification and finding |
| --- | --- |
| `.github/workflows/tests.yml` | Safe public release; required CI workflow; no secrets configured |
| `README.md` | Safe public release; corrected both mandatory lanes and reconstruction inputs; example endpoint only |
| `src/parallel_agent/web_assets/app.js` | Safe public release; current application; relative local API fetch, no authentication data |
| `src/parallel_agent/web_assets/index.html` | Safe public release; synthetic demo and aggregate findings only |
| `src/parallel_agent/web_assets/style.css` | Safe public release; presentation only |
| `tests/test_web_demo.py` | Safe public release; required current CI coverage; fake redaction sentinel |
| `tests/web_demo_ui.cjs` | Safe public release; required Node harness; locally generated synthetic fixtures |
| `docs/hero_verification_lab.md` | Safe public release; implementation review, explicitly unverified rendered viewports |
| `docs/mobile_verification_lab.md` | Safe public release; implementation review; local snapshot path, no snapshot contents or private reports |
| `docs/historical_validation.md` | Safe public release; CI/reconstruction documentation; fake credential examples only |
| `tools/validate_heldout_v1.py` | Safe public release; REQUIRED CI input; pinned reconstruction, integrity checking, isolated offline tests |
| `tests/test_historical_validation.py` | Safe public release; REQUIRED CI regression coverage; temporary tampering fixtures only |
| `tests/fixtures/parallel-heldout-v1-freeze.tar.gz` | Historical research; REQUIRED CI input; safe to publish subject to benchmark disclosure consequences below |
| `benchmarks/heldout_v1/cases.json` | Historical research; synthetic cases, references, probes, expected labels; no personal data |
| `benchmarks/heldout_v1/model_visible_tasks.json` | Historical research; synthetic task payloads |
| `benchmarks/heldout_v1/development_exclusions.json` | Historical research; prior synthetic development payloads and hashes |
| `benchmarks/heldout_v1/manifest.json` | Historical research; inventories, hashes, protocol/runtime settings, feasibility findings; endpoint identity is hashed |
| `benchmarks/heldout_v1/manifest.sha256` | Historical research; immutable integrity seal |
| `docs/heldout_v1_preregistration.md` | Historical research; immutable original protocol, including its original pre-collection status and commands |
| `src/parallel_agent/heldout.py` | Historical research; evaluator; credentials read from environment only; live execution opt-in not invoked |
| `src/parallel_agent/heldout_analysis.py` | Historical research; analysis implementation, no collected reports |
| `src/parallel_agent/heldout_cases.py` | Historical research; deterministic synthetic construction, including reference generation |
| `tests/test_heldout.py` | Historical research; original offline tests; fake transports and explicit synthetic credential sentinels |

The archive contains exactly the five listed benchmark files, preregistration,
three held-out source modules, and historical test module, byte-identical to their
checkout copies. Directory entries and regular files only: no links, special
files, traversal members, extra reports, or credential containers. Original owner
and group label `grol`, modes, and timestamps are retained. These reveal local
provenance, not authentication secrets. Do not repack or redact the frozen archive.
This audit document is an additional safe public release file.

## Research disclosure decision

The synthetic benchmark, reference plans, generator, and historical tests are
suitable for public research release after the concluded collection. The historical
collection remains **incomplete**; publication must not relabel it a completed
measurement or change its results. No raw model outputs or reports are included
in these new research files. Publication does not retroactively invalidate the
original prospective holdout, but exposes tasks, answers, labels, and evaluator
behavior for future training, tuning, and contamination. Future use is replication
or regression testing, not an unseen held-out evaluation. Fresh confirmatory work
needs a separately preregistered, unexposed benchmark with exclusions for this set.
Do not resume the original partial collection as if it were the same experiment.

The CI archive itself publishes all these answers even if loose research files
are omitted. Keeping the benchmark private is incompatible with publishing this
workflow and its self-contained fixture unchanged. The proposed command includes
research material deliberately; no publication action has been taken.

## CI dependency closure

The workflow needs the tracked project/source/tests, `pyproject.toml`, `uv.lock`,
Node 24, Python 3.12, Git, and the untracked validator, archive, and regression test.
The exact proposed set supplies every local dependency. The historical lane
reconstructs from BOTH commit `058eda7bda5aacffbd859a9fbe8ac174a65c37dd` and the
integrity-checked archive overlay. The archive is not a full source snapshot.
The pinned commit exists locally and is an ancestor of HEAD; checkout's
`fetch-depth: 0` supplies it when that history is retained in the release repository.
A source ZIP, shallow history, or a newly initialized repository without that
revision is insufficient. No parent-directory private archive/report is required.

Omitting `tools/validate_heldout_v1.py` or the fixture breaks CI; omitting the new
regression test silently drops required coverage. The loose historical sources
are also included for the proposed research release, though reconstruction reads
their archived bytes, not checkout copies. Actions and locked package installation
require provider access. Hosted dependency installation was not exercised here;
local tests used the already installed environment. No inference credentials,
Modal access, model weights, npm packages, or private reports are needed.

## Validation and preservation

After README correction, with `UV_CACHE_DIR=/tmp/parallel-uv-cache`:

- `uv run --no-sync pytest -q --ignore=tests/test_heldout.py`: **567 passed**, Node harness included.
- `uv run --no-sync python tools/validate_heldout_v1.py`: **16 passed**; pinned revision, archive, manifest, and **58/58** frozen files verified.
- Python compileall for src/tests/tools/deployment and Node syntax checks for app/harness: passed.
- `git diff --check`: passed.

Before/after SHA-256 comparisons of all 23 proposed inputs found only README.md
changed by this audit. No application, frozen research, hashes, manifests, reports,
model outputs, or historical results were edited. This document was then added.
Rendered browser/device behavior remains unverified as recorded in the UI reviews;
this safety audit does not claim visual acceptance testing.

## Explicit exclusions

No initially proposed file must remain private based on this audit. Exclude all
raw private experiment reports, model-response dumps, request/response logs,
authentication headers, credentials, `.env` files, Modal credential containers,
private keys, local configuration, and model weights wherever stored. Do not copy
parent-directory research outputs or the parent archive into the release; the
reviewed repository fixture already supplies the exact archive bytes. Private
outputs outside this checkout were not opened or changed in this audit.

Exclude `.venv/`, `.hypothesis/`, `.pytest_cache/`, every `__pycache__/` and `*.pyc`,
local build/output/cache directories, `/tmp` audit helpers, `.agents/`, `.codex/`,
and `.git/` metadata. Do not use `git add .`, `git add -A`, or force-add ignored files.
Use only the explicit proposed paths in the accompanying staging command.
