# TODOS

## Funder Dashboard

### Grantee Performance Scorecards

**What:** Add configurable performance scorecards that rank grantees by delivery rate, cost efficiency, timeliness, and growth trend with color-coded scores.
**Why:** Helps funders answer "which grantees need attention?" — turns raw data into actionable management decisions.
**Pros:** High-signal feature for funder demos. Makes the platform proactive, not just retrospective.
**Cons:** Scoring thresholds are program-specific (what's "good" for KMC differs from CHC). Requires a config UI for setting per-program targets.
**Context:** Proposed during Baobab demo CEO review (2026-03-21). Deferred because thresholds can't be generalized — each program needs configurable target metrics (e.g., target visits/week, max cost/visit). Implementation: add a config panel on programs where funders set targets, then scorecard compares actual vs. target.
**Effort:** M (human: ~3 days / CC: ~30 min)
**Priority:** P2
**Depends on:** Fund detail page pipeline data (already available via SSE)

### Canvas-to-Image Print Conversion

**What:** Utility function `prepareForPrint()` that converts Chart.js `<canvas>` elements to `<img>` tags before `window.print()`, then restores them after.
**Why:** Chart.js renders to `<canvas>`, which doesn't reliably appear in print/PDF output. Without this, the "Download Report" PDF will show blank chart areas.
**Pros:** Makes PDF export look professional. Reusable across any page with charts.
**Cons:** Minor complexity — need to track canvas→img mapping for restoration.
**Context:** Identified during Baobab demo eng review (2026-03-22). The plan uses client-side `window.print()` + `@media print` CSS for PDF export (#7). Chart.js provides `Chart.toBase64Image()` to serialize canvases. The utility should: (1) find all Chart.js canvases, (2) replace each with an `<img src="data:image/png;base64,...">`, (3) call `window.print()`, (4) restore canvases after print dialog closes.
**Effort:** S (CC: ~10 min)
**Priority:** P1 — blocks demo step 8 ("Report to Bloomberg")
**Depends on:** #7 PDF Export implementation
