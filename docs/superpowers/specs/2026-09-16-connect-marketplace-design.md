# Connect Marketplace — org registry, EOI history, and directory

**Status:** phase 1 SHIPPED 2026-09-16 — all five steps are live at
`/labs/marketplace/`. Plan for steps ①② is
`docs/superpowers/plans/2026-09-16-marketplace-org-registry.md`; steps ③–⑤ were
executed directly against the landed code rather than planned separately.
Outreach (phase 2) not started.

**Known gap:** seven response sheets are not yet shared with the labs service
account, so those rounds report `denied` and render as "not ingested".

## The problem

Dimagi needs to find the organisations worth approaching for a new programme,
and today that knowledge is spread across three places that do not join up.

Connect knows who *delivers*. Pulse mirrors that: countries, service types,
visit volume, USD per service, first delivery. But Connect only knows the
organisations that **won** something. An organisation that answered an EOI and
was not selected never gets a Connect row at all, so from Connect's point of
view it does not exist.

The LLO Directory — a Google Sheet — knows the rest: every organisation that
ever applied, their contacts, what they said in each application. It is the
master list, and it is larger than Connect's.

`pulse_partner_import` already reads a little of that sheet, but only to put
names on slugs. That arrangement has identity backwards: it treats the directory
as a lookup table *for Connect*, when the directory is the superset and Connect
is the subset that won. Everything else the sheet knows — contacts, sectors,
scale, application history — is read by nobody.

So there is no way to ask the question the work actually starts from: *which
organisations should we approach, and what do we already know about them?*

## Scope

**In (phase 1) — the data, and enough UI to see it:**

1. `LabsOrg` becomes the master organisation registry, fed by a first-class
   importer that reads the whole directory rather than two columns of it.
2. Historical EOI/RFP rounds and their Google Form responses are ingested as
   real solicitation and response objects, attached to organisations.
3. An org directory: filter the list, drill into one organisation, see its
   identity, its Connect delivery history, and its application history.
4. `PulsePartner` / `PulsePartnerAlias` collapse into the registry, so pulse
   becomes a *view* over one org table rather than a second one.

**Out (phase 2, separate spec):** outreach — campaigns, drafting, sending,
reply tracking. Phase 1 must not foreclose it, and does not build it.

**Also out:** replacing Google Forms as the intake mechanism. Rounds keep being
run on Forms for now. The point of modelling them as solicitations is that when
intake does move into Labs, it is the same objects and the same screens, not a
migration.

## The endgame this is built toward

Connect production will eventually grow an organisation data model, and when it
does, **the prod organisation becomes canonical and everything here hangs off
it**. That is not an aspiration to revisit later; it is a constraint on what may
be written now.

It resolves into one rule:

> **`LabsOrg` holds identity and nothing else.** Every other fact about an
> organisation lives in a table that points at it.

`LabsOrg`'s own docstring already states why, and it is worth restating because
it is the thing most likely to be eroded by a well-meaning patch: *a field one
feature invents makes the whole row unmigratable, because the blocker becomes
Connect not having that field.* A `sector` column added to `LabsOrg` for the
directory's filter is how this project quietly becomes permanent.

With that rule held, the eventual migration is: repoint each satellite table's
foreign key at the prod organisation id, and drop `LabsOrg`. No redesign, no
data reshaping, and no feature has to be renegotiated.

`LabsOrg` already carries both join keys (`connect_organization_id` and
`connect_organization_slug`), and its matching rules are already written down.
Nothing in this spec changes them.

## Data model

### Organisation side — the `marketplace` app

New app `connect_labs/marketplace/`, serving `/labs/marketplace/`.

| Model | Purpose | Natural key |
|---|---|---|
| `OrgProfile` | 1:1 with `LabsOrg`. Everything the directory sheet says about an organisation that is not its identity. | org |
| `OrgContact` | Named people at an organisation. | (org, email) |
| `OrgConnectSlug` | A Connect org slug attributed to an organisation, with a required reason. Today's `PulsePartnerAlias`, moved. | slug |

`OrgProfile` fields, each traceable to a directory column:
`has_used_connect`, `year_established`, `team_size`, `flws_managed`,
`countries[]`, `regions[]`, `sectors[]`, `website`, `office_address`, `notes`,
`msa_link`, `work_order_link`, `joined_at`, `joined_basis`, `lat`, `lon`,
`location_precision`, `location_label`.

`joined_at` / `joined_basis` and the location triple move here from
`PulsePartner`. They are directory facts — `joined_at` is the date an
organisation answered an EOI, which Connect never sees — so they belong to the
profile, not to pulse's telemetry tables.

`location_precision` keeps its existing meaning and must not be flattened: a
town matched in an address is a pin, a country is a whole country, and a display
that hides the difference draws a rooftop from the word "Nigeria".

`OrgContact` fields: `full_name`, `role_title`, `is_main_poc`, `email`, `phone`,
`notes`. Rows, not a JSON blob — phase 2 sends mail to these, and a recipient
list needs to be queryable, deduplicable, and individually suppressible.

### EOI side — real models in `solicitations`

The EOI/RFP concept already exists in this repo and was built for exactly this.
`SolicitationRecord` and `ResponseRecord` are proxy models over JSON; their
field vocabulary is adopted **as-is** by new local Django models, so there is one
EOI vocabulary in the codebase rather than two.

`Solicitation` (labs DB), mapped from the directory's EOI/RFP tab:

| Field | Source |
|---|---|
| `title` | Program / Initiative Name |
| `solicitation_type` | Announcement Type — already literally `eoi` / `rfp` |
| `status` | Published → `active`, Closed → `closed` |
| `application_deadline`, `expected_start_date`, `expected_end_date` | the date columns |
| `questions[]` | derived from the response sheet header row (below) |
| `evaluation_criteria[]` | empty for historical rounds; populated when a round is run natively |

`SolicitationResponse` (labs DB), one row per form submission:

| Field | Source |
|---|---|
| `solicitation` | FK to the round |
| **`llo_entity`** | **FK to `LabsOrg`, nullable** |
| `llo_entity_name` / `org_name` | the organisation name exactly as submitted, kept verbatim |
| `responses{}` | the answer row, keyed to `questions[]` |
| `submitted_by_name`, `submitted_by_email` | contact and email columns |
| `submission_date` | Timestamp |

**`llo_entity` as a foreign key is the whole point.** It is a free string today,
and that string is precisely why the solicitations app has never been used in
anger: there was no real organisation to point it at. Making it a relation is
what turns application history into something the directory can join on.

**Where these models live:** `solicitations/models.py`, beside the existing
proxy models — not in `marketplace/`. Putting them in `marketplace/` would
create a second EOI model, which is the duplication this project exists to end.

This makes one existing docstring wrong and it must be corrected in the same
change: `solicitations/data_access.py` describes itself as *"a pure API client
with no local database storage."* After this it has two stores, and the
docstring has to say which applies when — **labs DB** for directory and
historical rounds, **the prod LabsRecord API** for live program solicitations,
unchanged and untouched.

### Provenance rides alongside, never inside

Source-tracking fields are kept separate from the shared solicitation vocabulary,
so the shape stays portable when intake moves off Forms.

On `Solicitation`: `announcement_url`, `form_url`, `response_spreadsheet_id`,
`response_tab`, `column_map`, `sa_access_state`, `sa_access_checked_at`.

On `SolicitationResponse`: `source_row`, `source_url` (deep link to the exact
row), `match_state`, `match_basis`, `matched_by`, `matched_at`.

`(solicitation, source_row)` is unique. Re-running the import is then idempotent
rather than duplicating several hundred rows.

### Verbatim answers are not optional

`responses{}` stores **the whole submitted row**, keyed to the derived question
list, with nothing dropped. The extracted fields (organisation name, emails,
country, website, contact) exist for matching and filtering — they are an index,
not the record.

This is a response to what the sheets actually look like. Across the rounds
inspected: 14–25 columns, no two question sets alike, the same concept worded
three different ways ("Organization name" / "Please provide the name of your
organization" / "What is the full, registered name of your organization?"), one
sheet with **two blank column headers**, another with **"Email Address" twice**.
Any schema invented to fit those will be wrong for the next round, and a dropped
answer is unrecoverable — the submission cannot be re-collected.

### `questions[]` comes from the header row

Question ids are generated stably from the response sheet's header row, with the
header text kept verbatim as the question text.

The Forms API would give richer metadata (types, options, required-ness), but
only where the form itself is reachable, and several rounds have no resolvable
form link at all. The response sheet is the artefact actually being ingested, so
it is the source of truth; Forms enrichment can be added later without changing
the model.

Blank and duplicate headers get positional ids so they remain addressable rather
than colliding or vanishing.

## The directory sheet becomes the manifest

Configuration lives in the sheet, not in Python. Adding a round is then a
spreadsheet edit, not a deploy — the same principle that moved partner names out
of this repo in the first place.

### EOI/RFP tab — cleanup

Today the tab has one link column that holds four different kinds of thing:
a published `/forms/d/e/…/viewform` URL (which does *not* contain the form id),
a `/forms/d/<id>/edit` URL, a `forms.gle` shortlink, and free text
(`"Multiple forms linked inline"`, `"N/A - same as EOI"`). It cannot be parsed,
and one row is not even one round.

The tab is restructured so **every component of a row is an explicit, separate,
resolvable link**:

| Column | Meaning |
|---|---|
| `Slug` | stable id for the round; the join key everything else uses |
| `Announcement Link` | the public announcement doc, one link, resolvable |
| `Form Link` | the form itself, one link, resolvable |
| `Response Sheet Link` | the response spreadsheet, one link, resolvable |
| `Response Tab` | which tab in it holds submissions |
| `Column Map` | header → extracted field, as a JSON object in the cell; only the handful of fields matching needs (`org_name`, `email`, `country`, `website`, `contact_name`). Absent or blank means "extract nothing", which is valid — the verbatim answers are stored either way. |
| `Labs Access` | whether the labs service account can read the response sheet |
| `Labs Access Checked` | when that was last verified |

**One row per form, not per announcement.** `GW Malaria RFI` is a single row
today whose form link reads "Multiple forms linked inline"; it actually ran four
separate forms (RDT, PMC, ITN SBC, IPTsc), each with its own response sheet. It
becomes four rows. A round with no responses of its own is not a round.

The apparent duplicate copy of the ITN SBC response sheet is treated as a
duplicate and not ingested, pending confirmation from the directory's owner.

### The `Labs Access` column is written by labs, never by hand

`marketplace_import --check-access` attempts a real read of each configured
response sheet as the labs service account, and writes the verdict and a
timestamp back into the row.

It is machine-written on purpose. A hand-maintained "yes, access granted" box is
a claim that goes stale the moment a sheet is moved, re-owned, or recreated, and
it fails in the worst direction: it asserts that everything is fine while the
importer reads nothing. The column is only worth having if it reports what is
actually true at the moment it was checked, which means the thing that checks it
has to be the thing that writes it.

The service account holds read-write access to the directory sheet (`canEdit:
true`, granted 2026-09-16). That privilege is deliberately narrow in use:

> **Labs writes exactly two cells per round — `Labs Access` and `Labs Access
> Checked` — and nothing else in the workbook, ever.**

The ingest path itself stays strictly read-only. A bug in matching or parsing
must not be able to reach the master registry, and confining every write to one
function with one purpose is what keeps that true. `--prune` semantics do not
apply to the sheet at all: labs never deletes a row it did not write.

`sa_access_state` on the model holds the same verdict in the database, and is
what the importer and the UI act on. The sheet column exists so a person
granting access can see the result of what they did without reading a log.

### Response mapping tab

The verdicts a person already reached on which submission belongs to which
organisation are promoted to a first-class tab the importer reads, in the same
shape as the existing `Connect Org Mapping` tab: submission → organisation, with
a **required reason**.

## Matching — carry the verdicts, never recompute them

Several hundred historical submissions have already been reconciled against the
organisations list by hand, matched on name, acronym, email and email domain,
with every non-exact match reviewed by a person and adjudicated.

That work is authoritative and the importer must not re-derive it.

1. **Exact match** on a contact email, then on a normalised organisation name →
   linked automatically.
2. **Mapping tab** → linked, carrying the recorded reason as `match_basis`.
3. **Everything else** stays `unmatched`, visible in a review queue, and is
   never guessed.

Rule 3 is the one that matters. This repo already learned it in
`partner_names.py`, which refuses to guess because **a wrong parent name is
worse than a visible slug** — and the cost here is higher than a wrong label,
because a misattributed submission puts one organisation's application on
another organisation's record.

`match_basis` is required on every non-exact link. An attribution without a
stated reason is a guess that someone will later trust.

## Data quality is a deliverable, not a side effect

The directory is maintained by hand, over years, by several people, and nobody
is fully confident in it. That is a stated condition of the project rather than
a defect to be discovered later, and it has a consequence: **an importer that
silently coerces bad input is actively harmful here.** `"50+"` quietly becoming
`None`, a contact row with no email quietly vanishing, two rows for one
organisation differing only in whitespace — each is a fact about the sheet that
the sheet itself should learn, and each is invisible if the importer's only
output is a success count.

So every import produces a **data-quality report**: not a log line, but an
enumerated list of what could not be read cleanly, addressed to the people who
maintain the sheet.

At minimum it names:

- a number column that did not parse (`"50+"`, `"approx 200"`, `"circa 2010"`)
- a contact row with no email address, or with an address that is not one
- a contact pointing at an organisation with no row on the Organizations tab
- an organisation whose country cell does not resolve to a country
- two organisation rows whose names differ only by case, whitespace or accent
- an organisation with no contact at all, and one with no country
- a slug attribution refused for want of a stated reason

Each finding carries its **row number**, so it is actionable rather than merely
true.

The report is printed by the import and — because the sheet is where the people
who can fix these actually work — is also written back to a dedicated
`Labs Findings` tab, which labs owns entirely and rewrites each run. That tab is
the one exception to labs' otherwise narrow write surface, and it is safe
precisely because labs owns every row in it: it never edits a cell a person
wrote.

Clarity improvements to the directory's own structure (clearer headers, split
columns, explicit links) are in scope and welcome. The constraint is unchanged:
labs writes only what labs owns, and a person's cell is never overwritten by an
import.

## Pulse becomes a view

`PulsePartner` and `PulsePartnerAlias` are removed; their data moves to
`LabsOrg` + `OrgProfile` + `OrgConnectSlug`.

The surface is small and fully enumerable:

- `partner_names._load()` — the only reader for name resolution. It currently
  loads `(name, short)` pairs and `(slug, partner_name)` alias pairs. It loads
  the same two shapes from the new tables instead. **`resolve()` keeps its
  signature**, so every caller in `api.py`, `views.py` and `network_api.py` is
  untouched.
- `network_api.py` — two querysets (partners with a country, for the map; all
  partners, for the network payload).
- `pulse_partner_import` — superseded by the new importer.

**Not touched:** `PulseOrganization`. It is the raw mirror of what Connect's
export said, a cache of production rather than a labs opinion, and it should
stay that. Collapsing it would mean labs editing its own copy of Connect's data.

**Also not touched:** the two dating guards in `network_api.py` — the
2025-01-14 `completed_works` floor (Connect bulk-created 81k rows at one instant
that day) and the preference for server-assigned `sync_ts` over handset
`field_ts`. They are load-bearing and tested; they stay exactly as they are.

This is the riskiest step in the project and it ships on its own, with the
existing pulse tests green, before anything is built on top of it. Those tests
change only in how they seed a partner, never in what they assert — see
Testing.

## Directory UI

`/labs/marketplace/` — list with filters, and a detail page per organisation.

Filters: country, sector, has-used-Connect, **delivering vs. bench**, applied-to
a given round, FLW capacity band, has a contact email. Plus name search.

The bench — organisations recruited that have never delivered anything — is
already computable from pulse and is the single most useful segment on the page,
because it is the list of people who said yes once and were never activated.

Detail page: identity and Connect link; profile; contacts; a delivery panel from
pulse; and application history — every round the organisation applied to, what
they answered, and a deep link to the exact source row.

Hundreds of organisations and hundreds of submissions. Ordinary querysets with
indexes; no search infrastructure, no pagination cleverness.

## Failure modes this design commits to preventing

**A silent partial import.** Access is granted per file, by deliberate decision,
so a new round's response sheet is unreadable by default. The failure to avoid
is an import that succeeds while skipping a sheet it could not open — the round
then shows zero applicants and looks merely unpopular.

So: the importer **fails loudly and names the sheet**, rather than logging and
continuing. `--check-access` is a preflight that reports every configured round
the service account can and cannot read, so granting is a checklist before a run
instead of a post-mortem after one. The same instinct is already in this repo:
`NotConfiguredEmailBackend` returns 0 and warns rather than reporting a success
it did not achieve.

**"No applicants" that is really "not ingested".** The directory distinguishes
the two states explicitly. A round that has never been successfully read says so
on the page.

**A half-loaded sheet deleting live data.** `--prune` is off by default, exactly
as `pulse_partner_import` and `targeting_import` already do it, and for the same
reason: a partial read should never look like a deletion.

**Silent re-guessing of a human's verdict.** Covered above; enforced by a test
that a non-exact match without a mapping row stays unmatched.

## Testing

- Importer: idempotency (a second run changes nothing), `--prune` off by
  default, verbatim answer preservation, blank and duplicate headers surviving
  as addressable questions.
- Matching: exact email, exact normalised name, mapping-tab verdicts, and — the
  one that matters — a near-miss that stays **unmatched**.
- Access: an unreadable sheet fails the run and names itself; `--check-access`
  writes the verdict back.
- Pulse: three test modules construct `PulsePartner` rows directly
  (`test_partner_names`, `test_network_view`, `test_org_drilldown`), so their
  **seeding lines must change** — they are replaced by one shared helper that
  builds a `LabsOrg` + `OrgProfile` from the same arguments.

  **No pulse test assertion may change.** That is the real safety property: the
  collapse moves where partner identity is stored and must not alter a single
  thing pulse concludes from it. If an assertion has to be edited to go green,
  the collapse is wrong and the rewrite is hiding a behaviour change.
- Fixtures are synthetic. No real organisation names, contacts, emails or
  submissions in test data.

Per this repo's own guidance: a test that only asserts absence proves nothing.
Each rule above is verified by mutating the rule and watching the test go red.

## PII and this repository

Contact names, email addresses and phone numbers live **in the labs database
only**. Never in fixtures, tests, sample data, management-command defaults, or
committed documents — `dimagi-internal/connect-labs` is public, and history is
not erasable by deleting a line later.

Submission text is third-party content: what an organisation wrote about itself
while applying for work. It is treated as confidential and never rendered on an
unauthenticated surface. `/labs/marketplace/` is login-gated in full.

Configuration in the sheet (round slugs, links, column maps) is not sensitive
and is fine to carry. Response sheet ids live in the sheet rather than in source
anyway, as a consequence of the manifest design.

## Rollout order

1. **Org registry + importer.** `LabsOrg` fed from the directory; `OrgProfile`,
   `OrgContact`, `OrgConnectSlug` populated. Nothing consumes it yet.
2. **Pulse collapse.** Pulse reads the registry; its tests pass unchanged.
3. **Sheet cleanup.** EOI/RFP tab restructured, Malaria split into four rounds,
   mapping tab promoted, `Labs Access` column added and populated by
   `--check-access`.
4. **EOI ingest.** `Solicitation` and `SolicitationResponse` models, rounds and
   submissions imported, matching applied.
5. **Directory UI.** List, filters, detail page.

Each step is independently useful and independently revertable. Step 2 is the
one with real regression risk and is deliberately isolated.

## Dependencies and open items

**Seven response sheets are not yet readable by the labs service account**
(`connect-labs-sa@connect-labs.iam.gserviceaccount.com`). They live in a shared
drive it is not a member of. Access is being granted **per file, explicitly** —
adding the service account to the whole drive was considered and rejected, as
access should be a deliberate per-file decision rather than a side effect of
where a file happens to live.

Until those grants land, four Malaria rounds plus the Learning Partners and KMC
round 1 EOIs cannot be ingested. This blocks step 4 for those rounds only;
steps 1, 2, 3 and 5 proceed regardless, and the affected rounds show as "not yet
ingested" rather than as empty.

**Awaiting confirmation from the directory's owner:** the `GW Malaria RFI` split
into four rounds, and treating the second ITN SBC response sheet as a duplicate.
Both are proceeding unless they object.

**Write access to the directory sheet is granted** (`canEdit: true`, verified
2026-09-16), so `--check-access` maintains the two access columns itself. The
privilege is confined to those two cells per round; see "The `Labs Access`
column is written by labs".

**Restructuring the EOI/RFP tab** (step 3) is a one-off operator edit, not
something the importer does. It is a structural change to the master registry
and wants a person's eye on it, and keeping it out of the import path preserves
the property that a buggy ingest cannot reshape the sheet.

## Phase 2, noted only so phase 1 does not foreclose it

Outreach: segment the directory, draft per-organisation mail, send via the
existing SES path (`send_labs_email`, Celery-only, already live), record what was
sent, and track replies. `OrgContact` as rows and the `Outreach`-as-log
precedent in `supply_chain` — *a log, not a state machine, because re-inviting
is a real event worth keeping* — are what phase 1 leaves in place for it.
