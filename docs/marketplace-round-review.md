# Marketplace rounds: intake, criteria and review

How an EOI/RFP round's submissions get into labs, how its evaluation criteria
are versioned, and how people and AI review the submissions. Current state as of
2026-10-19; every claim cites the file it comes from. Ownership and access in
general (who may read a call, a response, a review) is
`docs/superpowers/specs/2026-10-15-solicitations-owned-by-organisations-design.md`
and, authoritatively, `connect_labs/solicitations/access.py`.

## Data model

All in `connect_labs/solicitations/local_models.py`, in the labs database (not
prod LabsRecords).

**`Solicitation`**: one round or call. Note that it represents one *form*, not
one announcement: an announcement that ran two forms is two rows.
- `source` tells three kinds apart. `directory` is a marketplace EOI round
  imported from the LLO Directory sheet. `labs` is a call created in labs (web
  form, MCP, agents). `labs_record` is reserved for calls carried over from prod
  LabsRecords, and none were. `Solicitation.objects.directory_rounds()` /
  `.calls()` split them. The marketplace pages read directory rounds only.
- `owner_org` (a `LabsOrg`). `marketplace.eoi.upsert_rounds` sets Dimagi
  as the owner of every directory round, which is what lets Dimagi's members
  read and review its responses.
- Criteria: `evaluation_criteria` (the current list, each
  `{id, name, weight, scoring_guide, rationale}`, weights are percentages),
  `rubric_version` (`v1`, `v2`, ...), and `criteria_basis` (how the criteria were
  arrived at and what was dropped). A round's Google Form went out without
  criteria, so they are written after its submissions are read.
- `summary_url`: the round's written summary (usually a shortlist Google Doc).
  The round links to it and does not copy it.
- Google Forms intake (directory rounds): `form_url`, `response_spreadsheet_id`,
  `response_tab` (default `Form Responses 1`), `column_map`, `sa_access_state`
  (`unknown` / `ok` / `denied` / `missing`) with `sa_access_checked_at`, and
  `last_ingested_at` (`was_ingested` is "responses were ever read").
  `past_calls.py` never hands a page the form address, sheet, tab or column map.

**`SolicitationResponse`**: one submission. `responses` holds the whole row
verbatim, keyed by question id (from the sheet header, via
`marketplace/responses.build_questions`). `llo_entity` is a real FK to the
matched `LabsOrg`, with `match_state` (`unmatched`, `email`, `name`, `human`,
`not_an_llo`) and `match_basis`. `source_row` and `source_url` point back at the
sheet row. The pair (`solicitation`, `source_row`) is unique, so a re-import
updates the same row and does not duplicate it.

**`RubricVersion`** (PR #2606): one row per criteria version of a round,
current version included: `version`, `criteria`, `basis`, `created_by`,
`created_at`, unique per (`solicitation`, `version`). The round itself carries
only the current criteria.

**`SolicitationReview`**: one reviewer's scoring of one response.
- `kind`: `ai` or `human`. At most one *standing* review per
  (`response`, `reviewer`, `kind`), so an AI pass run under a person's account
  never replaces that person's own review.
- `based_on` + `stance`: a person can start from another review and record
  `agrees`, `amended` (copied and edited), `adds` (stores only the additions,
  read as the original plus them) or `disagrees`. The source review is never
  changed.
- `criteria_scores` (criterion id → 1–10), `criteria_notes` (criterion id →
  reason), `strengths`, `concerns`, `ask_next`.
- `outcome`, a key from `solicitations/vocabulary.py` `OUTCOMES`: `pick`
  (Recommended), `backup` (Recommended backup), `waitlist`, `pass` (Not
  recommended). The shortlist is `pick` and `backup`. Labs-run calls also keep
  `recommendation` and the award flow; a directory round is decided outside labs.
- `flags`: keys from `vocabulary.FLAGS` (`unsupported`, `inconsistent`,
  `templated`, `padded`, `incomplete`, `out-of-scope`, `capacity`, `cost`,
  `early-start`, `safety`). Legacy free-text flags are mapped by
  `LEGACY_FLAGS`, and unknown flags are refused.
- `claims`: the review's statements, each citing the response text it rests on
  (`solicitations/evidence.py`). Quotes are checked against the named answer
  when the review is saved, so an AI review cannot cite words the applicant did not write.
- `provenance`: model, skill, sources, and `rubric_version` (the criteria
  version the scores were given on, also exposed as the `rubric_version`
  property).
- `score`: 1–100, computed by `weighted_score(criteria_scores,
  evaluation_criteria)` whenever criteria scores exist (unscored criteria drop
  out with their weight); otherwise the score the reviewer gave.
- `superseded_at`: set when a re-score on newer criteria took its place. The
  default manager `objects` is `CurrentReviews` (standing reviews only, and
  related managers like `response.reviews` use it too). `all_versions` reads history.

## Google Forms intake (directory rounds only)

Code: `marketplace/directory.py` (parsing), `marketplace/eoi.py`
(upsert, access check, ingest), `marketplace/management/commands/marketplace_import.py`.

- The master sheet is the LLO Directory (`directory.DIRECTORY_ID`). Its
  `EOI/RFP` tab (`directory.ROUNDS_TAB`) has one row per form.
- `marketplace_import --eoi` reads the directory (orgs, contacts, mapping), then
  `eoi.upsert_rounds` (create/update a `Solicitation` per row, never deletes),
  then `eoi.check_access` (a real read of every round's response sheet), then
  `eoi.ingest_round` for each readable round. `--check-access` alone runs the
  upsert and the access check without ingesting. Ingest re-derives the round's
  `questions` from the header, matches each submission to a `LabsOrg` (exact
  contact email, exact normalised name, an earlier round's matched email, or a
  human verdict from the `EOI Response Mapping` tab; `marketplace/matching.py`),
  and stamps `last_ingested_at`.
- Each round's response sheet is a separate file and must be shared explicitly
  with `connect-labs-sa@connect-labs.iam.gserviceaccount.com` (`eoi._SA_EMAIL`).
  Viewer access is enough. An unreadable sheet is recorded as `denied` (or `missing`
  when no link is recorded), and the rounds list renders **"not ingested"** for
  any `sa_access_state` other than `ok` (`templates/marketplace/rounds.html`),
  never "no applicants".
- Write-back: labs writes only three columns on the `EOI/RFP` tab, and only
  when their headers are present: `Labs Access` (the verified state),
  `Labs Access Checked` (timestamp) and `Next Step` (derived by
  `eoi.next_step_for`). Rows are keyed on the parsed row number, and columns
  are found by header text, never by position.
- Schedule: Celery beat `pulse-partner-directory` at 04:20 daily
  (`config/settings/base.py`) runs `pulse.tasks.import_partner_directory`,
  which calls `marketplace_import` with `eoi=True`.
- The service-account key comes from `LABS_SYNTHETIC_GDRIVE_SA_KEY`.

**Labs-sourced calls have no in-labs Forms importer.** `check_access` and
`ingest_round` iterate directory rounds only. A call created in labs that
collects answers on a Google Form gets them into labs through an external agent
calling the `create_response` MCP tool (as the call's manager, entered on the
firm's behalf).

### Runbook: adding a new EOI round

1. Add a row to the `EOI/RFP` tab. The parser requires these headers (matched
   by text, ignoring case, spacing and a bracketed note; `directory.ROUND_COLUMNS`):
   `Slug`, `Program / Initiative Name`, `Announcement Type`, `Status`,
   `Published Date`, `Application Deadline`, `Selection Decision Date`,
   `Program Start Date`, `Program End Date`, `Target Countries / Regions`,
   `Announcement Link`, `Form Link`, `Response Sheet Link`, `Response Tab`,
   `Column Map`, `Notes`, `Connect Programme`.
   - `Slug` is required and unique. A row with no slug, or a duplicate slug, is
     skipped and reported. A slug already used by a labs call is skipped as a
     collision.
   - `Announcement Type` starting `rfp` → RFP, otherwise EOI. `Status`
     starting `publish` → active, otherwise closed.
   - Dates accept `12 March 2026`, `March 12 2026`, `12 Mar 2026` or
     `2026-03-12`. Text like "Rolling" becomes no date.
   - `Response Sheet Link`: the response spreadsheet's URL, from which the ID is
     extracted. `Response Tab`: blank means `Form Responses 1`.
   - `Column Map`: optional JSON `{field: "exact header text"}` for the fields
     auto-detection gets wrong. The fields are `org_name`, `email`, `contact_name`,
     `country` and `website` (`responses.DETECTORS`). Invalid JSON falls back to
     auto-detection, with a warning.
   - `Connect Programme`: Connect's `delivery_type` for the round, canonicalised
     by `marketplace/programs.canonical`.
2. Share the response sheet with
   `connect-labs-sa@connect-labs.iam.gserviceaccount.com` (Viewer).
3. Wait for the 04:20 beat run, or run it by hand:
   `make manage CMD="marketplace_import --check-access"` to verify access, then
   `make manage CMD="marketplace_import --eoi"` to ingest.
4. Read the result. In the command output, a readable round prints
   `<slug>: N submissions, M matched, K unmatched`. On the sheet, `Labs Access`
   should read `OK — readable` and `Next Step` should be empty. Unmatched submissions
   wait for a person's verdict on the `EOI Response Mapping` tab (`/labs/marketplace/unmatched/`).
5. Set the criteria (below) before reviewing.

## Criteria versions

- `marketplace_round_criteria_set` → `SolicitationsDataAccess.set_round_criteria`
  (`solicitations/data_access.py`) replaces `evaluation_criteria` and
  `criteria_basis`, sets `rubric_version` to `v{max(current, every version ever
  recorded) + 1}` (a deleted version's number is never reused), and writes a
  `RubricVersion` row. Only someone who may read the round's responses may set
  it (`access.can_set_round_criteria`).
- A review is linked to a version by `provenance.rubric_version`, which is
  stamped with the round's current version whenever criteria scores are saved
  and the caller did not name one.
- An AI re-score on new criteria **supersedes**: in `create_review`, an
  existing AI review by the same caller whose `rubric_version` differs from the
  incoming one gets `superseded_at` set, and a new review is created. A re-run
  on the same version replaces that review in place. Human reviews are never
  superseded this way.
- `archive_round_version` (`marketplace_round_version_archive`) restores an
  earlier version's criteria and its AI reviews (kept outside labs) as
  superseded history. It never targets the current version.
- `delete_round_version` (`marketplace_round_version_delete`) deletes an earlier
  version and every review scored on it (AI and people's, standing or
  superseded). It never deletes the current version.
- Version history and the comparison are computed live and never stored:
  `marketplace/queries.version_history` (versions with AI/person review counts),
  `queries.scores_at(round, version)`, and `queries.compare_at(round, earlier,
  later)` (whose recommendation moved, the mean shift, per-criterion weight,
  mean and shortlist gap).

## UI routes

- `/labs/marketplace/rounds/` lists the rounds, with "not ingested" shown where
  the sheet could not be read.
- `/labs/marketplace/rounds/<slug>/` is the round page (`views.round_detail`).
  Its owner sees the responses. **Start reviewing** (**Next to check** once
  people have reviewed) opens the next response, and **Compare scores** appears
  once the round has criteria.
- `/labs/marketplace/rounds/<slug>/scores/?version=vN&compare=vM` is the Scores
  page (`views.round_scores`; 404 to anyone who cannot read the round's
  responses). It shows every applicant against every criterion. `version` defaults
  to the current one. `compare` may name any other version: the two are ordered
  by version history before `compare_at` runs, and with no `compare` the version
  before the shown one is used. Earlier versions can be deleted from this page
  (`rounds/<slug>/versions/<v>/delete/`), and `rounds/<slug>/summary/` sets the
  summary link.
- `/solicitations/response/<pk>/` is a response with its reviews. For a directory round
  it renders `solicitations/round_response.html`, with every claim linked to the
  passage it cites.
- `/solicitations/response/<pk>/review/` is the reviewer's form. On another person's
  or the AI's review, the response page offers **I agree** (`?from=<id>&stance=agrees`),
  **Add to it** (`stance=adds`), **Copy and edit as mine** (no stance, which saves
  as `amended`) and **I disagree** (`stance=disagrees`). The AI's per-criterion
  suggestions stay hidden until the reviewer has scored that criterion.

## Who reads reviews

`access.can_read_reviews` = `can_manage` (owner org member or platform
support, for labs calls) or `can_read_round_responses` (Dimagi members or
platform support, for directory rounds). The reviewed organisation and the
respondent cannot read reviews: `get_reviews_for_response` returns `[]` for
them, and the award email (`data_access._notify_awarded`) carries no review
content. `can_review` additionally refuses a reviewer's own side.

## MCP tools

Marketplace (`connect_labs/mcp/tools/marketplace.py`):
- `marketplace_orgs_get`: directory organisations by slug, with their delivery
  and application history; contacts only with `include_contacts`.
- `marketplace_rounds_list`: rounds with counts; `slug` for one round,
  `include_applicants` for who answered it.
- `marketplace_round_responses`: every submission to a round, with its matched org (owner only).
- `marketplace_round_scores`: the Scores page's figures; `version`, `compare_with`.
- `marketplace_round_versions`: every criteria version with its review counts.
- `marketplace_round_version_archive`: put an earlier version and its AI reviews into history.
- `marketplace_round_version_delete`: delete an earlier version and every review on it.
- `marketplace_round_summary_set`: set or clear `summary_url` (https only).
- `marketplace_round_criteria_set`: replace the criteria and bump `rubric_version`.

Reviews (`mcp/tools/reviews.py`):
- `list_reviews` / `get_review`: the standing reviews of a response, or one review by id.
- `create_review`: create or update the caller's review (`kind`, `based_on`, `stance`,
  `criteria_scores`, `outcome`, `flags`, `claims`, `provenance`). Requires
  `public_record_acknowledged=true`.
- `update_review` / `delete_review`: the reviewer's own review (or platform support).

Solicitations (`mcp/tools/solicitations.py`):
- `list_solicitations` / `get_solicitation`: calls the caller owns, plus listed calls.
- `create_solicitation` / `update_solicitation` / `delete_solicitation`: labs calls, owner only.
- `list_responses` / `get_response`: responses, for the owner or the respondent.
- `create_response`: respond, or (as the call's manager) enter a response on a firm's behalf.
- `award_response`: award a labs call's response; never a directory round.
