# Independent current and historical validation

Both lanes are mandatory, from the repository root with the locked development
Python environment installed (Python 3.12+ and Node are required):

```sh
uv run --no-sync pytest -q --ignore=tests/test_heldout.py
uv run --no-sync python tools/validate_heldout_v1.py
```

The first command tests the evolving application, including the Verification Lab
DOM harness and the reconstruction regression tests. It excludes only the original
historical module, whose assertions require frozen source bytes. The second runs
all 16 original historical tests, unchanged, in a separate Python process. The
first lane's isolation regression also runs those tests in a child process; that
nested run is not added to the current lane's test count. Neither lane substitutes
for the other. Running unrestricted pytest against the evolving checkout can
still fail the original freeze checks: this is intentional drift detection.

## Reconstruction and integrity

`tools/validate_heldout_v1.py` creates an automatically cleaned temporary tree,
exports Git commit `058eda7bda5aacffbd859a9fbe8ac174a65c37dd` with `git archive`,
and overlays the exact synthetic freeze archive. The commit supplies the original
application; the archive supplies the ten later frozen evaluator/fixture files.
No current checkout source is copied, and no Git worktree or history is modified.

Pinned SHA-256 values in the validator are:

- Archive: `5704a00ddc1cc7f2cea83870c73786cdf1f045563af8b062c4c2326f7f33f065`
- Manifest: `122fd160537ac8e79b82f3766aeef2dd69ca82bfaeee14539ba7fa2c47502403`

Before tests execute, the validator checks the resolved commit, archive bytes,
manifest bytes and seal, manifest commit and 58-entry inventory, and every file
hash. Missing inputs and any drift fail closed. It never regenerates or repairs a
manifest. Archive extraction rejects traversal, links, and special files. File
mismatch diagnostics list all offending paths. Regression tests first establish a
clean reconstruction before changing a benchmark case, then require both the new
preflight and the original freeze checker to report that exact case path.

The child starts with `python -I -S`: caller paths, `PYTHONPATH`, user site hooks,
and editable-install `.pth` hooks are not loaded. Only the interpreter's dependency
directories and the historical `src` directory are added explicitly, with frozen
source first. Pytest plugin autoload and inherited pytest options are disabled;
package origins are checked before and after testing. Credentials under the
PARALLEL, Modal, and Hugging Face environment prefixes are removed from the child.
The original tests use fake transports; this command has no live inference mode.

## Portable archive review and CI

`tests/fixtures/parallel-heldout-v1-freeze.tar.gz` is a byte-identical copy of the
existing parent-directory archive, which remains untouched. Content review covered
the complete member inventory, JSON schemas and synthetic construction, source
and protocol content, and URL/email and credential-bearing fields. Its ten regular
files are five benchmark JSON/seal files, the preregistration, three evaluator
modules, and the original test module. There are no raw private reports, real
credentials, customer personal records, or embedded private endpoint URLs. The
endpoint and model identity fields are hashes. Test strings such as
`never-serialize-secret`, `offline-fake`, and `https://example.invalid/v1` are
explicit fake inputs. Customer IDs and Boolean flags are synthetic. Original tar
metadata includes the local owner/group label `grol`, modes, and timestamps;
metadata is preserved for exact archive integrity and is not applied on extraction.

CI checks out full Git history (`fetch-depth: 0`) and uses the repository archive,
so it does not depend on a private parent directory. Both validation steps are
mandatory and fail on missing inputs. Node is installed, so the current lane's UI
harness runs rather than skipping. The default is deliberately not a fallback
search. To validate the original private copy explicitly:

```sh
uv run --no-sync python tools/validate_heldout_v1.py --archive ../parallel-heldout-v1-freeze.tar.gz
```

The workflow becomes self-contained when these files are included in a future
change; no commit, push, publication, or hosted CI run was performed here. The
pinned commit must remain reachable in the checkout's Git object database. A
shallow checkout or source ZIP without that revision is insufficient and fails.
CI dependency installation requires access to its package/action providers; the
validation itself uses no inference, Modal, deployment, or GPU services.

## Reproducibility limits

This reproduces frozen source and offline assertions, not a live model experiment
or its results. It does not attest remote weights, tokenizer, serving process,
hardware, timing, or historical credentials. Original reports are not read,
copied, or changed. Frozen v3 code, cases, references, preregistration, manifest,
and tests remain unmodified. Historical frontend bytes exist only in the temporary
tree; the current Verification Lab continues to evolve independently.

The Git commit and SHA-256 pins are integrity anchors, not an external signature
or protection against someone rewriting the validator and its pins together.
The runner uses the installed interpreter and test dependencies (CI installs the
locked environment); it does not restore a historical OS or guarantee bitwise
runtime behavior across Python/platform versions. Isolation prevents accidental
checkout imports, not arbitrary hostile installed dependency code. Offline tests
use fake transports; they do not establish new model performance or universal
robustness beyond the bounded checks.

Additional required checks:

```sh
uv run --no-sync python -m compileall -q src tests tools deployment
node --check src/parallel_agent/web_assets/app.js
node --check tests/web_demo_ui.cjs
git diff --check
```

The UI harness is invoked by `tests/test_web_demo.py::test_offline_ui_behavior`
with actual locally generated API fixtures. It reads the HTML default scenario
(`replacement`) and exercises replacement, creation, deletion, all six evidence
steps, failed/malformed responses, stale responses, and changed verdict evidence.
