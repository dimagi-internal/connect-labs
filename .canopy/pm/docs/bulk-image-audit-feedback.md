# PM item map — "Bulk Image Audit tool feedback"

Source doc: https://docs.google.com/document/d/1OWW5OAvUZAMIlo8xdmkn4ckHTW7SCIi4cO6gjFRBXAA
Maintained per canopy PM skill Lesson 21: re-reads of this doc are DELTA passes against this map —
diff (a) new strike-throughs/comments in the doc, (b) merges since `last_verified`, (c) dispositions
whose "under discussion" status may have resolved. Update rows as they change; don't re-derive.

Last full pass: 2026-07-09 (Hal). `last_verified` = main as of 6cd125d3.

## Must-have

| # | Ask (verbatim gist) | Code verdict | Alignment | Disposition |
|---|---|---|---|---|
| 1 | Display FLW name during review | EXISTS (struck in doc; FLW name resolution shipped ~#768 era) | aligned | shipped |
| 2 | Filter by FLW | EXISTS (struck in doc) | aligned | shipped |
| 3 | Loading large numbers of images | EXISTS (struck; JJ "done") | aligned | shipped |
| 4 | Return to a saved/incomplete audit, modify, resubmit | PARTIAL — backend `audit_uncomplete` endpoint (`audit/urls.py:88`) + JS `uncompleteAudit()` (`templates/audit/bulk_assessment.html:1805`) both exist; **no UI element calls the function** (dead code). Completed sessions: pass/fail read-only, notes editable. | aligned | **proposed 2026-07-09 (S): add Reopen button on completed banner — pending JJ approval** |
| 5 | Flag previously-audited images (+ include/exclude choice at creation) | MISSING — anticipated by stub: wizard FLW-preview payload carries `"prior_audit_tags": []` with literal `TODO: Fetch from audit history` (`audit/views.py:1510`) | aligned — "exclude previously audited" as a wizard Step-3 filter follows the #884 filter pattern; composes with workflow-driven creation | **proposed 2026-07-09 (M) — pending JJ approval** |
| 6 | Audit a Program, not just an opp | EXISTS via program-owned workflows (JJ direction-thesis; struck). `get_audit_sessions` fans out across program member opps (24b193ba, Wouter, 2026-07-09) | per-thesis: standalone program-audit UI would be an anti-pattern | closed (handled by workflows) |
| 7 | Audit UX bundle: success confirmation, idempotent completion, date stamping, recovery, readable errors | PARTIAL (completed_at stamped; rest varies) | JJ thesis: audit creation should be workflow-driven; ST "can't recall context" | under discussion — do not build from the doc. Note: auto-save (GTH 1) covers the "recovery" slice |
| 8 | Note field → reason-for-failure dropdown | MISSING | JJ open design question: how does the reason drive AUTOMATED follow-up (task assignment)? Not a build order | under discussion — candidate design note: failure-reason → task-type mapping |

## Good-to-have

| # | Ask | Code verdict | Alignment | Disposition |
|---|---|---|---|---|
| 1 | Auto-save audit progress | MISSING — pass/fail clicks mutate local state only (`bulk_assessment.html:1264` `updateAssessmentLocal`); persistence = manual "Save progress" button; `beforeunload` warning acknowledges lost-work risk (`:990`) | aligned (review-screen only) | **proposed 2026-07-09 (S): debounced save to existing `audit_save` endpoint — pending JJ approval** |
| 2 | Zoom on image | EXISTS — #867 + #881 (Hal, 2026-07-08) | aligned | shipped |
| 3 | Reuse existing audit sessions | WONT-DO (JJ: workflows make this a non-issue) | — | closed |
| 4 | Delete audit sessions | EXISTS — #858 (bulk delete, Abhishek) + #884 UX polish (spinner/notification/auto-refresh; per-row delete removed) | aligned | shipped — doc line can be struck |
| 5 | Embedded criteria + 5-photo reference set + per-criterion tooltips | MISSING; content-dependent (needs authored per-image-type criteria; Step 5 "Audit Field Configuration" #770 is config plumbing, not content) | unclear owner for content | not obvious — design note territory |
| 6 | % passed on audit session list page | MISSING — list table has Pass/Fail badge only (`audit/tables.py:57` `overall_result`); review screen computes % client-side for the threshold check (#884) | aligned | **proposed 2026-07-09 (S): persist percent at completion into session data; render column — pending JJ approval** |
| 7 | Edit audit configuration (threshold etc.) | PARTIAL — pass-threshold settable at creation (#884, slider 75–100); no post-creation editing | post-creation edit has recompute-the-results semantics | not obvious — needs design decision |

## Dedup log

- 2026-07-09: doc actively worked by humans — Abhishek Krishnan merged #884 (GTH 4 + 7-partial) same day;
  Wouter Vink merged program fan-out. Checked: open PRs (2, unrelated), remote branch names, recent
  merged titles — no in-flight work on MH 4, MH 5, GTH 1, GTH 6 at proposal time.
