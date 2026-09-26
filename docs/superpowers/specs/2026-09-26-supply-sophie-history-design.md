# Supply: record history, provenance, and Sophie's program view

Status: design approved in conversation 2026-09-26; spec awaiting review.

## 1. Why

The supply_chain v1 covers sourcing through stock, but it has no memory. Every
record is overwritten in place, so when a shipment's ETA slips from 5 Sep to
19 Sep the earlier belief is gone. Nobody can ask "what did we think on
20 Aug?" or "who changed this, and why?"

The real user is Sophie, a program manager running Connect-RUTF procurement
for Dimagi (program 263, org `dimagi-ng-rutf`). Dimagi buys too little to make
suppliers log into anything. So Sophie is the only person inside the system.
Supplier and freight-forwarder email reaches it through an **external AI**,
not through the app. That AI can be ACE she forwards to, Claude over MCP, or
the canopy panel on the page. The AI reads the email and writes through the
same operations everyone uses.

So the app's job is **strong models and UI**: record every change, say who
made it and on what evidence, let Sophie correct AI-entered records, and show
the state as it stood on any past date. Parsing email is out of scope.

## 2. Decisions taken (and why)

| Decision | Choice | Reason |
|---|---|---|
| Who parses email | An external agent, never the app | Keeps the app to models and UI; any agent channel works the same way |
| AI writes | Directly, flagged as AI-entered, correctable after | Fewer clicks. `quote_correct` and `quote_void` already exist |
| Revision timestamp | **Recorded time only** (`recorded_at`, automatic) | One clock. Domain dates (`expected_on`, `received_on`…) already say when things happened. No `known_on` concept in the model |
| Backfilled history for the demo | The **seeder** infers past recorded times from source dates | A demo need. It is not allowed to shape the data model |
| Where revisions are captured | One mechanism set by `call_operation`, written by model signals | Every write already passes that choke point (`operations.py:119`) |
| Scope of Sophie's view | Her program and org. The market stays global | She manages a program, not the world's supply chain |

## 3. Model

### 3.1 `Revision` (new, append-only)

One row per change to one supply record.

| Field | Meaning |
|---|---|
| `program_id` | Scope, copied from the record |
| `content_type`, `object_id` | The record changed |
| `action` | `create` / `update` / `delete` |
| `changes` | JSON `{field: [old, new]}`. For `create`, old is null. For `delete`, the full last state |
| `recorded_at` | Set automatically on save. Never supplied by an API caller |
| `actor` | The Django user who made the call, or null for a command |
| `channel` | `web` / `mcp` / `api` / `command` |
| `operation` | The operation name, e.g. `shipment_update` |
| `source_ref` | Optional. The caller's reference for its evidence, such as an email Message-ID or a document hash |
| `source_excerpt` | Optional. The quoted text that justified the write (≤ 2,000 chars) |
| `result_ref` | The record the operation returned, for idempotent replay |

Indexes: `(content_type, object_id, recorded_at)`,
`(program_id, recorded_at)`, and `(program_id, operation, source_ref)`.

**No `update`, no `delete` on `Revision`.** The model raises on both. It is
the one record that must never lose history.

### 3.2 Capture

- `call_operation` opens a **write context**: a contextvar holding actor,
  channel, operation name and the optional source. Only operations with
  `is_write=True` open it.
- The MCP adapter sets `channel="mcp"`. The HTTP endpoint sets `api`, views
  set `web`, and management commands set `command`. The adapter passes this
  in. `call_operation` gains a `channel` keyword with default `web`.
- `pre_save`, `post_save` and `post_delete` receivers on every concrete
  supply_chain model write the `Revision`. The field diff is taken against the
  row as loaded in `pre_save`. The timestamp-only fields `created`/`modified`
  are excluded from `changes`.
- A save **outside** any write context (a shell, a data migration) still
  writes a revision, with `channel="command"` and no operation. Nothing
  escapes history.
- `Revision` itself, and the stock ledger's own append-only movement rows,
  are excluded from capture. A movement is already a revision of stock.

### 3.3 Source and idempotency

Every write operation's schema gains an optional top-level `source`:
`{ref: string, excerpt?: string}`. It is validated like any other field.

If a write arrives with a `source.ref` that already has a revision in this
program **for the same operation**, nothing is written. The call returns the
record in `result_ref` with `"replayed": true`. That is what makes "Sophie
forwarded the same email twice" harmless, whichever agent processes it. The
check sits in `call_operation`, before dispatch.

A write with no `source` is never deduplicated. Human edits through web
forms don't carry one.

### 3.4 Provenance shown to people

- `channel` is `web` → the actor's display name, e.g. "Sophie".
- `channel` is `mcp` or `api` → "via AI · Sophie" when the actor is
  a person, and "ACE (agent)" when the actor is an agent account. An
  agent account is a user flagged as one. A single boolean on the user's
  labs profile is enough, and the plan confirms where it lives.
- `source_excerpt` opens from the badge.
- `Quote.entered_by` (program/supplier) is left as it is. It answers a
  different question: whose figure it is, not who typed it.

### 3.5 As-of reads

`as_of(queryset, when)` returns the records as they stood at `when`:

- It excludes records whose `create` revision is later than `when`.
- It includes records deleted after `when`, rebuilt from the `delete`
  revision's snapshot.
- For each record, it rewinds each field by walking revisions newer than
  `when` from newest to oldest and applying their old values. Instances come
  back unsaved and read-only.

Records that predate this feature have no `create` revision. A one-off
migration writes a `create` revision for every existing row, stamped with
the row's `created` time, marked `channel="command"`.

**Scope of as-of in v1:** the overview, tender detail (including the quote
comparison), and order/contract detail with its shipments, invoices and
payments. The stock, distribution, network and market pages are **not**
available as-of. With an as-of date active they show a banner saying so. No
existing SQL aggregation in the stock ledger is rewritten.

## 4. UI

### 4.1 Program framing

The supply pages already require a program context (`DomainHomeView`). The
header names the program and the org in their role: "Connect-RUTF · Dimagi,
buyer of record". The overview shows only this program's tenders and orders.

### 4.2 Overview (`/supply/`)

One line per open or recent tender and order: stage, what it is waiting on,
last change (when and who), and a stale flag. Stale means:

- an outreach with no reply ≥ 14 days after `sent_on`, or
- an `expected_on` in the past with no receipt, or
- a quote whose basis is `not_specified` on a field the comparison needs.

Rows whose latest change came through `mcp` or `api` show the AI badge. The
existing funnel and checks panels stay.

### 4.3 Tender and order detail: timeline

The current state stays at the top. Underneath is a **timeline** of the
record and its children (quotes, outreach, contract, shipments, invoices,
payments), newest first.

Each revision is one line in plain language, rendered from `changes` by a
per-model field formatter: "ETA 5 Sep → 19 Sep", "Quote recorded: $X per
carton (basis: not specified)". Each line carries the actor badge, and the
source excerpt opens from it. AI-entered quote lines show **Correct** and
**Void**, linking to the existing views.

### 4.4 As-of control

- There is one date control in the supply header. It sets `?as_of=YYYY-MM-DD`,
  which the supply links carry forward.
- While it is active, a persistent banner reads "Viewing as of 20 Aug 2026 —
  back to today", and all write controls are hidden. The server refuses
  writes carrying `as_of`.
- The timeline shows only revisions up to that date.

### 4.5 Global market

`/supply/market/` stays a live, cross-buyer listing of public tenders. For a
tender it shows what the tender publishes and nothing else: no revisions, no
source excerpts, no other suppliers' quotes, and no as-of. **Revisions and
excerpts are only visible to members of the program**, which follows the
existing access to the underlying records.

## 5. Demo data (seeder)

- The demo runs on synthetic **program 10672**, which mirrors 263. It is
  seeded by `seed_rutf_rounds` (`scripts/walkthroughs/oes-demo/seed_remote.py`).
  The facts come from the Drive document as now, never the repo.
- The seeder **replays round 1 as dated history**: requests, quotes, award,
  contract, dispatch, the ETA slip, receipt and payment. Each step is written
  through its operation with a realistic `source` (a synthetic Message-ID and
  an excerpt).
  - Some steps are written as the ACE agent over `mcp`. Others are written
    as Sophie over `web`.
  - Each step carries a **seed-only `recorded_at` override** in the write
    context, inferred from the step's source date.
  - The override is accepted only when `scopes.is_synthetic(program_id)` is
    true, and it is never exposed in an operation schema.
- One step is **replayed twice** with the same `source.ref`, to show
  idempotency.
- Round 2 is seeded as it is today: open, three quotes that can't be compared,
  five non-responders. It is recorded "recently".
- The market is seeded with a few other synthetic buyers' public tenders, so
  Sophie's round 2 isn't alone on the board.
- The seeder stays idempotent, and a purge is only allowed through
  `require_synthetic`.

## 6. Narrative (DDD) — `supply-sophie-rutf`

1. **Overview, today.** Sophie's program: round 1 delivered, round 2 open with
   three quotes, five non-responders and stale flags. AI badges are visible.
2. **Round 1 timeline.** The chain from quote to payment, with the ETA slip and
   the source excerpt behind it.
3. **As of 20 Aug.** The overview re-renders: round 1 is in transit with ETA
   5 Sep, and round 2 doesn't exist yet. Back to today.
4. **Round 2 comparison.** The missing bases are flagged. Sophie corrects one
   AI-entered quote, and the correction appears on its timeline.
5. **The market.** Her public tender among other buyers', showing only public
   facts.
6. **Award with rationale.** It appears on the overview.

`.canopy/ddd/context.md` is rewritten for this narrative. It currently
describes the nutrition demo.

## 7. How the DDD loop runs for this (v1 mode)

The loop audit (2026-09-26) found that the loop is tuned for polish. Across
the five supply narratives, 19 of 23 judged iterations scored 2 while open
findings fell from 61 to 21. That happened because:

- the score is the minimum cell,
- every iteration re-renders and re-judges every scene (about 14 min and 600k
  tokens a round), and
- rounds sat 1.5–3.5 h apart waiting on deploys.

For this run:

1. **Gap walk before any render.** Walk each scene's narration against the
   operations, views and seed data, and list what doesn't exist. Build all of
   it. No render until the walk comes back empty.
2. **Judge once, then fix from the backlog.** One full render and judge
   builds the backlog. Fix all mechanical findings in parallel, as **one** PR
   and one deploy. Then re-judge only the scenes that had findings. Do a full
   render and judge every third batch, and at the end.
3. **Deploy gate.** Render only when `/health/` `git_sha`, sampled until
   every sample agrees, equals the fix PR's merge commit.
4. **Progress is measured by open-finding count, mean cell score and
   confirmed-cap count.** Stop on a plateau of those, not of the minimum cell.
   Convergence stays min cell ≥ 4 and user ≥ 4.
5. **One narrative at a time** against labs. The video phase runs only after
   convergence.

Items 2–4 are done by hand this run. Turning them into canopy plugin changes
(`run_pipeline.py` progress signal, scene-scoped re-judging, the deploy gate,
video-after-convergence) is a separate follow-up in the canopy repo, informed
by how this run goes.

## 8. Testing

- Capture:
  - each write operation produces revisions with the right actor, channel and
    diff;
  - a save outside a context still produces one;
  - `Revision` refuses update and delete.
- Idempotency:
  - the same `source.ref` with the same operation writes once and returns
    `replayed`;
  - a different operation with the same ref writes;
  - no source never deduplicates.
- As-of:
  - a record created after T is absent;
  - a field changed after T is rewound;
  - a record deleted after T is present;
  - backfill rows from the migration behave as creations at `created`.
- Scoping: a non-member sees no revisions or excerpts. The market tender
  serializer carries neither.
- The seed override is refused on a non-synthetic program.
- As-of refuses writes.
- Every new guard gets mutated out once to confirm a test goes red.

## 9. Out of scope

Email parsing, an inbox, sending email, supplier logins beyond what exists,
as-of for stock/network/distribution/market, a `known_on` date, and canopy
plugin changes (follow-up).
