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

### 3.1 `OperationCall` and `Revision` (new, append-only)

Provenance belongs to the **call**, and history belongs to the **record**. A
single call can change several records: an award, for instance, writes the
award and updates the tender. So there are two tables.

**`OperationCall`**: one row per write operation that ran.

| Field | Meaning |
|---|---|
| `program_id` | The access scope of the call (nullable) |
| `operation` | The operation name, e.g. `shipment_update` |
| `actor` | The user who made the call (FK, nullable for a command). `PROTECT`: a user with supply history cannot be hard-deleted — deactivate them instead |
| `actor_is_agent` | Copied at write time from `LABS_AGENT_ACCOUNT_EMAILS` |
| `channel` | `web` / `mcp` / `api` / `command` / `supplier` (a partner acting for itself: a market bid, an update-link report) |
| `acting_org_id` | For `supplier`: the organisation the partner acted for (a plain integer, no FK) |
| `source_ref` | Optional. The caller's reference for its evidence, such as an email Message-ID or a document hash |
| `source_excerpt` | Optional. The quoted text that justified the write (≤ 2,000 chars) |
| `payload_digest` | sha256 of the canonical JSON of the validated payload, `source` left out |
| `result` | JSON. The operation's return value, returned again on replay. Over 64 KB it is stored as `{"truncated": true, "summary": …}` (counts and ids), and a replay returns that |
| `recorded_at` | When the call ran |

A unique constraint on `(program_id, operation, source_ref, payload_digest)`
applies when `source_ref` is not empty.

**`Revision`**: one row per change to one supply record.

| Field | Meaning |
|---|---|
| `call` | FK to `OperationCall`. Null for a save outside any operation |
| `program_id` | Derived from the record |
| `content_type`, `object_id` | The record changed |
| `action` | `create` / `update` / `delete` |
| `changes` | JSON `{field: [old, new]}`. For `create`, old is null. For `delete`, the full last state |
| `recorded_at` | Automatic. Never supplied by an API caller |

Indexes: `(content_type, object_id, recorded_at)` and
`(program_id, recorded_at)`.

**Both tables are append-only.** Their `save()` refuses updates and their
`delete()` raises. They are the records that must never lose history.

### 3.2 Capture

- `call_operation` opens a **write context**: a contextvar holding actor,
  channel, operation name and the optional source. Only operations with
  `is_write=True` open it.
- The MCP adapter sets `channel="mcp"`. The HTTP endpoint sets `api`, views
  set `web`, and management commands set `command`. The adapter passes this
  in. `call_operation` gains a `channel` keyword with default `web`.
- The market and update links run their writes under `SYSTEM` access, so
  they pass `channel="supplier"`, the acting organisation and (on the market)
  the signed-in user. These attribution keywords — and a `then` hook that runs
  the caller's follow-up saves inside the same call, such as marking a quote
  as the supplier's own — are for trusted in-process callers only. No MCP or
  HTTP adapter passes them.
- `pre_save`, `post_save`, `pre_delete` and `post_delete` receivers on every
  concrete supply_chain model write the `Revision`. Each is connected with its
  model as the sender: a receiver with no sender would make every model in
  the project look like it had delete listeners and turn off Django's fast
  delete everywhere. `pre_delete` is also connected to the non-supply models a
  supply through model points at (an organisation), for the link rows their
  delete takes with it, and to nothing else. `m2m_changed` is connected to the
  many-to-many link tables (Django writes and deletes those rows without the
  per-row signals the others rely on). The field diff is taken against the
  row as loaded in `pre_save`. `pre_delete` stashes the row's program and a
  flat snapshot on the instance before anything is actually deleted, so a
  cascade still resolves each child's program before its parent goes. The
  timestamp-only fields `created`/`modified` are excluded from `changes`. A
  `save(update_fields=[…])` records only those fields.
- A save **outside** any write context (a shell, a data migration) still
  writes a revision, with `channel="command"` and no operation. Nothing
  escapes history.
- `Revision` itself is excluded from capture. Append-only rows such as
  stock movements are captured too, as creations only, so as-of rewinding
  can remove them.

### 3.3 Source and idempotency

Every write operation's schema gains an optional top-level `source`:
`{ref: string, excerpt?: string}`. It is validated like any other field.

If a write arrives with a `source.ref` that already has an `OperationCall` in
this program **for the same operation with the same payload** (compared by
`payload_digest`, taken before provenance stamping so two agents forwarding
the same email agree), nothing is written. The stored `result` is returned
with `"replayed": true`. The same evidence producing the same write is
recorded once. The same `source.ref` with a **different** payload — one email
quoting two products — is an ordinary second write, and both calls keep the
ref for provenance. The unique constraint makes this
safe under concurrent duplicates: the loser of the race gets the winner's
result. That is what makes "Sophie
forwarded the same email twice" harmless, whichever agent processes it. The
check sits in `call_operation`, before dispatch.

A write with no `source` is never deduplicated. Human edits through web
forms don't carry one.

### 3.4 Provenance shown to people

- `channel` is `web` → the actor's display name, e.g. "Sophie".
- `channel` is `mcp` or `api` → "via AI · Sophie" when the actor is
  a person, and "ACE (agent)" when the actor is an agent account.
  Agent accounts are listed in the setting `LABS_AGENT_ACCOUNT_EMAILS`
  (default `["ace@dimagi-ai.com"]`). The retained `users` app must not be
  modified. `actor_is_agent` lives on `OperationCall` only, decided once at
  write time from that setting; a `Revision` has no such column and reaches
  it through its `call`, so history does not change if the setting does.
- `channel` is `supplier` → "Supplier · {organisation}" (the user's name when
  no organisation is recorded). Not marked as AI.
- `source_excerpt` opens from the badge.
- `Quote.entered_by` (program/supplier) is left as it is. It answers a
  different question: whose figure it is, not who typed it.

### 3.5 As-of reads: rewind inside a rolled-back transaction

Supply pages read through deep operation and queryset chains, including SQL
aggregates in the stock ledger. Rewinding records in Python would mean
rewriting every read path. Instead, an as-of request is rendered **inside a
database transaction that is always rolled back**:

1. Open `transaction.atomic()` and turn off revision capture.
2. Take this program's revisions with `recorded_at` after the as-of instant,
   and undo them newest first:
   - `update` puts back the old field values;
   - `create` deletes the row. Rows created later go first, so children
     leave before their parents;
   - `delete` re-inserts the row from its snapshot, with the same primary key.
3. Run the view **and render the response** inside the transaction, so no
   lazy queryset escapes it.
4. Call `set_rollback(True)`. Nothing is kept.

Every existing view, operation and aggregate then works unmodified, stock
included. The cost is one write per change since the as-of date, all rolled
back. That is fine at program scale.

"As of 20 Aug" means the **end** of that day in the server timezone.

Nothing rewinds for a caller who may not see the page. An anonymous request
runs live, with no past date, and the page redirects to login. For a
signed-in caller the pages' own data access is built first, so a non-member
gets the page's 403 before any rewind. On Postgres the rolled-back block runs
under `lock_timeout = 2s` and `statement_timeout = 15s`; hitting either
answers 503 "This past view is busy — try again in a moment", so a past view
can never hold row locks against live writes for long.

**Known gap: alert state reads live.** The alert checker updates
`AlertCheckState` (`cleared_at`, `last_seen_at`), `AlertSubscription`
(`movements_seen_through`, `last_digest_sent_at`) and `AlertNotice` with
queryset `.update()`s, which bypass capture. Those fields are therefore not
rewound, and an as-of page shows them as they are today. They are the alert
machinery's own bookkeeping, not a program's supply record, so this is
accepted rather than changed.

Records that predate this feature have no `create` revision. A migration
writes a `create` revision for every existing row, stamped with the row's
`created_at` and marked `channel="command"`. Without it, rewinding before
the feature existed would leave pre-existing rows in place. That is the
right answer: they did exist.

**Scope:** every program-scoped supply page. The market is cross-program and
live, so it ignores `as_of`; portfolios stay live for the same reason — a
named set of program ids is not a program's own record to rewind.

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
- Round 2 is **published**, so it is on the market exactly as a supplier
  browsing it would see it. The demo seeds no other buyers' tenders beside
  it: this repository is public, and an invented buyer organisation would be
  a second, fictitious row in the same directory that holds our real
  partners. The seeder's `market_buyers` support (other synthetic programs'
  public tenders, for a demo that wants a busier board) still exists and
  still works — this demo's document simply does not use it.
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
5. **The market.** Round 2, published, shown exactly as a supplier browsing
   the market sees it — only public facts, no other buyers on the board.
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
as-of for the cross-program market, a `known_on` date, and canopy
plugin changes (follow-up).
