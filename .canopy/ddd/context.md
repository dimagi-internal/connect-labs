# DDD Context — Supply: Sophie's RUTF program (`supply-sophie-rutf`)

## Project

connect-labs, the labs/rapid-prototyping environment for Connect. The narrative in play is
`supply-sophie-rutf`: the core-labs supply domain at `/supply/` (`connect_labs/supply_chain/`),
where the labs DB is the system of record. It demos **record history, provenance and a
program-scoped view** for the Connect-RUTF procurement program.

Other supply narratives (`supply-test-kits`, `supply-chc-copacks`, `supply-chlorine-stopgap`,
`supply-dispenser-import`, `supply-iptsc-shortfall`, `oes-supply-base`) and older ones
(`nutrition-demo`, microplans, solicitations) live in `docs/walkthroughs/`; their run history is
under `.canopy/ddd/runs/`.

## base_url

https://labs.connect.dimagi.com

Auth: labs browser session at `~/.ace/labs-session.json` (seeded out-of-band via
`/ace:labs-login`). Specs carry NO `auth` block — cookies are seeded before the render and the
recorder gets `--storage-state ~/.ace/labs-session.json`. The session cookie's local expiry
(~12h) is honoured by Playwright even while the server session lives: an all-red preflight with no
hint means re-mint the session, not a broken recipe.

Artifacts publish to the canopy-web `connect` workspace (`.canopy/ddd/config.yaml`).

## The narrative

- Source of truth for WHAT it shows: `docs/superpowers/specs/2026-09-26-supply-sophie-history-design.md`
  (§1 why, §3 model, §4 UI, §5 seeder, §6 scene list, §7 how the loop runs this time).
  Sophie's real workflow: `docs/superpowers/specs/2026-09-11-rutf-procurement-design.md` (§0-1,
  §5.6 basis flags, §11 append-only quotes, §17 buyer of record).
- Draft spec (single file, not yet split): `docs/walkthroughs/supply-sophie-rutf.yaml`.
  WhyBrief: `docs/walkthroughs/supply-sophie-rutf.why_brief.yaml`.
- Run dir: `.canopy/ddd/runs/supply-sophie-rutf-2026-09-26-001/` (evidence, why-brief, verdicts).
- **Persona:** Sophie, program manager for Connect-RUTF at Dimagi (buyer of record). She is the
  ONLY user inside the system. Suppliers and the freight forwarder email her; an external AI
  (ACE, Claude over MCP, or the canopy panel) records the facts through the same operations.
  The AI is never a persona.
- **Scenes (7):** overview today → round 1 timeline with the ETA slip and its source → as of
  20 Aug → round 2 comparison (each quote's one missing fact, the question to ask, AI badges) →
  the answer arrives (a supplier states sachets per carton; ACE records it as an MCP correction
  with the reply as source; the quote ranks with cost per carton and per child) → the market
  (round 2 exactly as a supplier sees it: the request, nothing private) → provisional award with rationale,
  shown on the overview.
- **Controller ruling (2026-09-26):** the earlier "AI misread freight, Sophie corrects it" beat is
  dropped: in the seed each round 2 quote fails for one reason except the second, which fails on
  two, so no correction could make it comparable without faking figures. The reply beat replaces
  it; the reply lands between scenes 4 and 5 off camera (why-brief G10: the recorder has no
  between-scene step yet).
- **Narrative-agreement (concept_change) gate: AGREED.** The owner agreed this story in
  conversation on 2026-09-26 (design spec status line and §6). It was not posted to the review
  surface; record that provenance, don't re-ask. After any material story change, re-gate.

## The data

- Synthetic **program 10672**, mirroring real program 263 (org `dimagi-ng-rutf`), seeded by
  `seed_rutf_rounds` in `scripts/walkthroughs/oes-demo/seed_remote.py`. Real facts (suppliers,
  prices, dates) come from the Drive document at runtime and NEVER enter the repo.
- Walkthrough setup (to be built): `scripts/walkthroughs/supply-sophie-rutf/seed.py` on the
  `scripts/walkthroughs/supply-test-kits/seed.py` pattern, emitting `program_id`,
  `round1_tender_id`, `round2_tender_id`, `quote_pack_missing_id`, `as_of_date` to `outputs.json` (ignored by that directory's own `.gitignore`, already in place).
  `rerun: per_render` — scene 7 writes (award); the reply before scene 5 is a seeder write.
- The seeder must replay round 1 as dated history (some writes as the ACE agent over mcp, some as
  Sophie over web, one replayed twice), seed round 2's three quotes as AI-entered, add an
  off-camera step recording the pack-configuration reply as an MCP correction by ACE, and make round 2 public. No other buyers' tenders are
  seeded (controller ruling: invented buyer orgs would pollute the real org directory).

## Current phase

PHASE 0 + SPEC AUTHORED (2026-09-26). Evidence audit, why-brief (why-qa pass, why-eval pass 4),
unified spec (validate pass, spec-qa pass, narrative-coherence pass) and actionability eval
(iteration 1 warn 3 -> narration revised -> iteration 2 pass 4) are done. The concept_change gate
is recorded as agreed in the run dir's `narrative-agreement.yaml`. Nothing rendered, recorded, judged or deployed.

**Next:** build the CAPABILITY gaps (why-brief G1-G5, G10 and the spec's per-scene features),
i.e. the design spec §3-§5, then run §7's gap walk. No render until that walk comes back empty.
Then `split_spec` the draft into `supply-sophie-rutf.recipe.yaml` + lock via canopy-web.

## How the loop runs this time (design spec §7)

1. Gap walk before any render; build everything it finds.
2. Judge once, then fix the backlog as ONE PR and one deploy; re-judge only scenes with findings;
   full render every third batch and at the end.
3. Deploy gate: render only when `/health/` `git_sha`, sampled until every sample agrees, equals
   the fix PR's merge commit.
4. Progress = open-finding count, mean cell score, confirmed-cap count. Convergence stays min
   cell ≥ 4 and user ≥ 4.
5. One narrative at a time against labs; video only after convergence.

## Repo facts that bite supply renders (see learnings.md for detail)

- Deploy only from `main`; a labs deploy mid-seed yields new-schema/old-code errors — check
  `gh run list --workflow deploy-labs.yml` before blaming the product.
- Tom-select pickers: hold after `type` before `Enter`, and press `Tab` before a submit.
- `wait_for` text only the SAVED page has, never text the form also contains.
- Start effecting scenes with a short hold or a `wait_for` on the scene's own page, or the
  before-frame is the previous scene's page.
- `ddd upload` only finds `<slug>.yaml`; with a split recipe, compose a temporary one and put
  `why_brief.yaml` in the run dir.
- This repo is PUBLIC: no real supplier names, prices, emails, people other than first name
  "Sophie", or Drive contents in committed files.
