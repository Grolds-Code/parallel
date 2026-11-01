# Hero redesign review

Scope: hero, narrow header spacing, and the transition to existing candidate
controls. Existing local changes were retained. No API, verifier, historical
artifact, deployment, credential, or checksum changes were made for this task.

The desktop hero uses a 42:58 narrative/evidence grid above 800px. The short
headline has no forced break. One sentence states the authorization distinction.
A secondary historical-replay line separates prerecorded Meta Llama inference
from newly computed offline verification. The selected candidate's full operation
sequence and three shared computed facts occupy one white, forest-accented panel.
The bounded decision has the strongest type; textual statuses retain red/green
backgrounds. A 44px link leads directly to the focusable candidate controls.
The adjacent section now introduces inspection instead of repeating the thesis.

At phone widths the headline is 28–34px with 1.1 line height, the header minimum
height is 56px, and the panel uses compact label/value rows. Technical text wraps;
zero-minimum grid tracks prevent intrinsic text width from expanding the layout.
No clipping or body overflow suppression was introduced. Existing focus outlines,
select labels, busy/live regions, navigation, and touch targets remain intact.
The preview continues to use selectedSummary(); no decision logic was added.

## Viewport review — CSS/DOM only

| Requested viewport | Reviewed layout rules |
| --- | --- |
| 320×568 | 28px heading; single-column hero; compact header and verdict rows |
| 326×504 | Same; shortened narrative places evidence earlier, exact fold unmeasured |
| 360×640 | 28px heading; same single-panel layout |
| 390×844 | 29.25px heading; same single-panel layout |
| 430×932 | 32.25px heading; same single-panel layout |
| 768px wide | 34px heading; single-column hero, standard header height |
| 1024px wide | 42:58 hero; zero-minimum tracks and wrapping operations |
| 1440px wide | 42:58 hero within existing 1400px main container |
| 1707×920 | Same bounded container; heading capped at 50px |

No browser executable, browser connector, Playwright, or Puppeteer was available.
These are cascade and DOM reviews, not rendered viewport tests. Screenshots,
actual first-fold placement at 326×504, page overflow, native selects, and physical
touch/focus behavior remain unverified. No screenshots were claimed or produced.

## Validation

- `UV_CACHE_DIR=/tmp/parallel-uv-cache uv run --no-sync pytest -q --ignore=tests/test_heldout.py`: 567 passed.
- `UV_CACHE_DIR=/tmp/parallel-uv-cache uv run --no-sync python tools/validate_heldout_v1.py`: 58/58 frozen files verified, zero mismatches; 16 passed.
- Direct Node UI harness with locally computed API fixtures: passed.
- `node --check` on app.js and web_demo_ui.cjs: passed.
- `git diff --check`: passed.

Frontend assertions now cover hero reading order, no forced headline breaks or
ribbon, all three semantic verdict labels and colors, exact operation sequence
agreement, selection changes, policy-check errors, and successful recovery after
stale responses. Existing loading/error, witness, long-text, and historical
independence coverage remains.

Files edited: web_assets/index.html, web_assets/style.css, web_assets/app.js,
tests/test_web_demo.py, tests/web_demo_ui.cjs, and this document.
No deployment, commit, push, inference, or Modal contact occurred.
