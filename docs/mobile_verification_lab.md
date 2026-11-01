# Verification Lab mobile review

## Scope and deployment evidence

This change is local only. No deployment, inference, Modal request, model adapter,
API, manifest, credential, or frozen research artifact was changed.
The working tree already contained a redesign and research changes before this
review; those changes were retained.

The saved `/tmp/parallel-public-home.html` snapshot (filesystem timestamp
2026-10-30) has the older “Catch the violation. Verify the correction.” hero,
outcomes before investigation, and no redesigned candidate/three-fact preview
or experimental findings section. The local page differs from that snapshot.
Its capture method and the current live deployment cannot be verified from the
snapshot. No claim is made that local changes are deployed. No live request was
made, respecting the instruction not to contact Modal.

## Root causes and changes

- Responsive rules accumulated in several rounds. Late declarations overrode
  earlier sizing and spacing. A final, explicit screen-only responsive layer now
  controls tablet and phone layouts without changing the print breakpoints.
- The header had a fixed height and competing branding/provenance widths. It
  now has an intrinsic height, a gap, and constrained phone branding.
- Three vertically expanded preview facts and oversized spacing delayed the
  controls. Compact label/value rows retain all three independently computed
  facts; the jump link has a 44px target.
- Native select labels were long enough to clip on phones. Concise option names
  fit the control; the full selected operation sequence and its computed
  implications remain directly below it and in the preview. Phone/tablet selects
  use 16px text, and recompute occupies its own full-width touch target.
- The historical cards stayed side by side at intermediate widths. They now
  stack through 800px in DOM order: original rejected proposal, then recorded
  correction. Their verdict words, colors, border accents, actions, witnesses,
  and checks stay together. Candidate actions likewise precede their decision.
- Step buttons used 11px text. They now use 13px text and at least 44px height;
  phone steps have two columns. Pagination has a separate position row on phones.
- Fixed-layout customer tables compressed four technical columns beyond useful
  readability. They now scroll within a named, keyboard-focusable region with a
  420px minimum table width. Research tables retain a wrapping three-column
  comparison, so Guided and Retry remain visible together.
- Long source strings, badges and grid children lacked uniform containment.
  Minimum-size resets and wrapping constrain them. JSON remains complete and
  wrapped, with a keyboard-focusable bounded scroll area.
- Details summaries now have 44px targets; scroll regions have explicit focus
  outlines. No functionality depends on hover. No animation or new dependency
  was introduced. Reduced-motion users retain immediate scrolling.

All development/held-out counts, exclusion caveats, historical timings and source
claims are unchanged. The underlying verification and API behavior are unchanged.

## Viewport audit (not rendered browser tests)

No Chromium, Firefox, Playwright, Puppeteer, or browser connector was available.
The fallback was a CSS/DOM review and an extended Node harness. The widths below
were reviewed against the actual cascade, not measured in a layout engine:

| Width | Expected layout from CSS |
| --- | --- |
| 320, 360, 375, 390, 430px | Compact header, two-column steps, single-column candidate and outcomes, label/value preview, separate pagination position row |
| 768, 800px | Single-column workspace/candidate/outcomes/research; three-column steps; compact preview |
| 801, 1024px | Desktop workspace sidebar and paired cards; existing three-column preview |
| 1280, 1440px | Existing desktop grids and bounded content width |

At every width, customer tables alone may scroll horizontally. Long prose and
technical identifiers wrap. There is no body overflow-hiding workaround.

The Node harness checks all three candidates across six investigation steps,
preview/detail consistency, natural proposal/decision and rejection/correction
order, table semantics and keyboard access, complete JSON, pagination bounds,
512-character annotations, errors, loading, stale requests and changed verdicts.
It does not simulate browser geometry, native select popups, touch, zoom or screen
readers. Those behaviors, visual balance, focus-ring clipping and actual absence
of page overflow still require browser/device verification before release.

## Validation

Both mandatory commands passed with `UV_CACHE_DIR=/tmp/parallel-uv-cache` because
the default cache is read-only in this environment:

- `uv run --no-sync pytest -q --ignore=tests/test_heldout.py`: 567 passed.
- `uv run --no-sync python tools/validate_heldout_v1.py`: 58/58 archived files
  verified with zero mismatches; 16 tests passed.
- Direct Node UI harness: passed, including the new mobile structural checks.
- JavaScript syntax checks for app and harness: passed.
- `git diff --check`: passed.

Files edited for this task: `src/parallel_agent/web_assets/index.html`,
`src/parallel_agent/web_assets/style.css`, `src/parallel_agent/web_assets/app.js`,
`tests/web_demo_ui.cjs`, and this review document.
