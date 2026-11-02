# Public links to a saved workflow run

**Status:** design, not built. Date: 2026-11-02.

## Why

Someone wants to send a saved workflow run to a person with no labs account, and have
it open as the same report. Today nothing does this:

- A run page needs a login, and reads its data with the viewer's own Connect token.
- The workflow "Share" button makes a *definition* public to other signed-in users.
- Embedding needs a registered partner system naming each viewer, and shows the newest run.

The first use is a survey report: workflow 28647, run 28649, opportunity 2377, a
completed run on a stored render whose snapshot captures one pipeline and the worker list.

## Goals

- From a completed run's page, a person with the right to do so creates a link.
  **They choose how long it lasts, on that page.**
- Anyone with the link sees that run as it was saved, with no login and no Connect token.
- When the link expires or is ended, the copied data is deleted from the labs database.
- The person sharing can see the run's links, copy one again, and end one early.

## Non-goals (this version)

- Stripping fields, pseudonymising workers or coarsening locations. The person asking
  accepted this for now: the link exposes everything saved in the run. A later version
  would add a redaction step at copy time; nothing here should make that hard.
- Sharing an in-progress run, a live view, or a whole workflow.
- Passwords, per-viewer identity, or allow-lists on a link.
- Changing an existing link's expiry. End it and make another.
- Creating links from MCP or by agent. A public write of real data stays a person's click.
- A list of every public link across labs.

## Anti-goals (a failure even if it works)

- A visitor's browser, signed in to labs or not, can use the link to reach anything
  beyond the copied run. Render code is author-written JavaScript.
- An expired or ended link still serves data, because cleanup has not run yet.
- The token ends up in the audit trail, which is append-only and archived under object lock.
- The page shows live data, or changes when the workflow's render is later edited.

## Decisions

Marked **(asked)** where the person asking decided; everything else is the designer's call.

1. **The run is copied into the labs database when the link is created (asked)**, and no
   later read touches a LabsRecord or Connect. The visitor has no token to read with.
2. **The copy is deleted when the share ends (asked).**
3. **Only completed runs can be shared.** Their snapshot is what the render draws from, so
   the copy needs no pipeline. The share control does not appear on an in-progress run.
4. **Duration is chosen in the share dialog (asked).** Choices: 1, 7, 30 or 90 days, or a
   date of the person's choosing. Default 7 days, maximum 365. There is no "never".
5. **A link can be ended early** by whoever can create links for that run. The question
   was asked and not answered; this is the designer's call because a public link with no
   off switch is a one-way door (the reasoning on `PulsePublicView`).
6. **A run may have several links**, each with its own expiry, so one recipient's can be
   ended without breaking another's.
7. **Who may share: Dimagi staff who also hold the run's ACL (asked).** Both tests, on the
   server. `utils/dimagi_user.is_dimagi_staff`, and the LabsRecord ACL on the run's scope:
   a program's managing organization, or an opportunity's organization, supervising
   organization or program organizations. In practice the ACL test is "this person's own
   token reads the record in that scope" (`template_workflows.can_edit` is the same test),
   so a person reading only a public copy fails it. On a labs-only synthetic scope, which
   has no membership check, the staff test is the one that bites.
8. **The token is stored in the clear**, as `PulsePublicToken` does. The row sits beside
   the data it unlocks, so hashing protects nothing, and storing it lets the person copy
   the link again. `secrets.token_urlsafe(32)`.
9. **A person deleting the run or its workflow ends its links**, in the user-facing delete
   paths, not in `WorkflowDataAccess.delete_run`. That method is also called by
   `hand_down.py`, `history_rebuild.py` and backfill commands, and routine replacement must
   not turn a link already sent to someone into a 404 with nobody told. A link whose run was
   replaced automatically stays as its frozen copy until it expires.
10. **A sharer must have access to every opportunity the snapshot holds**, read from the
    snapshot's `opportunity_ids` and the row tags at copy time. A multi-opportunity run can
    hold other organizations' rows; viewing them in labs must not become the right to publish
    them. A run the sharer cannot clear this way cannot be shared, and the refusal names why.
11. **Every view of a link is an audit-trail event**: route redacted, IP and user agent, no
    token. A signed-in visitor is recorded as anonymous, since the page ignores their session.
    The counters on the row are a convenience, not the record.
12. **Boundaries and images are not copied.** Their routes need a login and stay refused, so
    a report that draws them shows a bare basemap or missing pictures. The first use draws
    its own points on `ConnectMap` and loads neither.
13. **The token in ALB access logs is accepted**, as it is for Pulse and update links.
    `request_telemetry` must log this route redacted.

## What gets built

### Data: a real table in the labs database

A `workflow` app model, say `PublicRunShare`, next to `CoachChart`. It is not a LabsRecord,
because nothing about it should go through Connect.

- **Identity:** `token` (unique), `definition_id`, `run_id`, one of `opportunity_id` /
  `program_id`, and the workflow name at share time.
- **Lifetime:** `created_by`, `created_at`, `expires_at`, `ended_at`, `ended_by`,
  `ended_reason` (`expired`, `ended`, `source_deleted`).
- **Use:** `last_viewed_at`, `view_count`.
- **`payload`**, a nullable JSON field holding everything the page draws from:
  - the effective render code at share time, resolved through `render_source`, so a run
    that follows a template freezes that template's code;
  - the definition fields a render reads, minus `config.actions` and `config.agent`;
  - the instance fields a render reads, with `status: completed`;
  - the **decoded** snapshot.

**Read the snapshot through `LabsRecordAPIClient`, not the raw record.** A stored run is
columnar, and a large one lives in a child `workflow_run_snapshot` record behind
`snapshot_ref`; the client decodes and fetches it.

"Cleaned up" means `payload` becomes NULL. The row stays, holding no report data, as the
record of who shared what and when.

### Creating and ending a link

Plain session-authenticated endpoints under `/labs/workflow/api/run/<run_id>/`: create
(with a duration), list, and end. Each checks decisions 7 and 10 itself, as the signed-in
person. Creating refuses a run that is not completed. Both creating and ending write an
audit-trail event naming the person, the run and the expiry (never the token).

### The public page

`GET /labs/workflow/shared/<token>/`, modelled on `PulsePublicView` and
`supply_chain/update_links/views.py`:

- No login, and it **ignores any session the visitor has**.
- **Expiry is checked on every read**: `expires_at > now`, `ended_at` null and `payload`
  present. Cleanup is for removing the data, never for enforcing the expiry.
- Unknown, expired and ended tokens get the same response: a plain "This link is no
  longer available" with a 404.
- `audit.path` is set to the redacted route, as update links do, and each view writes an
  explicit event (decision 11), because the audit middleware skips anonymous page views.
- Headers: `X-Robots-Tag: noindex, nofollow`, `never_cache`, and `Referrer-Policy:
  no-referrer`, because the frame loads third-party scripts and the token is in the URL.
- No site navigation, canopy panel, editing controls, workflow actions or auth-status gate.
  Modelled on `embed/workflow.html` and `EmbedWorkflowView`'s present mode. A small footer
  says it is a saved copy as of the run's date.

**It renders in the existing sandbox** (`workflow/sandbox.py`): the opaque-origin frame
with its enforced CSP. The payload is handed in as `workflow_data`, exactly as a completed
run gets it. This is what stops author-written code acting as a signed-in visitor.

**Nothing extra is built to refuse requests.** The frame is mounted as `artifact.tsx` mounts
it, without the workflow handlers, so the existing host already answers none of its labs
requests. An anonymous request fails on login and the ACL anyway. The frame's CSP already
allows Mapbox and tile hosts directly. A render that needs more than its snapshot shows its
own empty state, which is accepted.

### The share dialog

A **Share** control in the runner's own chrome, not in render code, so every saved-run
workflow gets it. It shows only on a completed run and only to someone who passes decision 7.

The dialog holds the duration choice and the list of this run's live links, each with copy,
its expiry, its view count and an end button. Before the first link is created it says
plainly, in a required confirmation:

> Anyone with this link can see everything saved in this run without signing in, including
> worker names and locations, and including data the report does not display. It stays
> available until the date you chose or until you end it.

### Cleanup

A Celery beat task, hourly (`config/settings/base.py`), in the style of
`sweep_stale_workflow_runs`: rows past `expires_at` with a payload get `ended_at`,
`ended_reason=expired` and a NULL payload. Ending a link early and deleting the source
clear the payload immediately. The task is safe to rerun.

## Constraints

- A snapshot is at most 5 MB (`_SNAPSHOT_SIZE_HARD_BYTES`); the payload adds render code.
- This repo is public: no tokens, real worker data or recipient details in fixtures or docs.
- Migration numbers race on `main`. Check `workflow/migrations/` right before the PR.
- Document it in `WORKFLOW_REFERENCE.md` §9 and `user_docs/`, and give the PR a
  `## Product Description`.

## How we know it works

- In a private window with no labs session, run 28649's link draws the report, all four
  tabs and the map, using only the copied data.
- Signed in as a labs user, the same link makes no labs API call as that user.
- After expiry the link 404s even with the sweep stopped; after the sweep `payload` is NULL.
- Ending a link, and deleting the run, each 404 it and NULL the payload at once.
- Editing the workflow's render afterwards does not change the shared page.
- A non-staff person with the ACL, and a staff person without it, each see no Share control,
  and the endpoints refuse them.
- The token does not appear in any audit-trail row.

## Left to the builder

- Exact names, the dialog's layout, and the code structure of the host's public mode.
- Which definition and instance fields a render needs, found by reading what the runner
  and `buildRunView` consume. Keep it to those.
- How the ACL test is applied to a run's scope, or whether another check already exists to reuse.
- Whether the payload is served inline in the page or fetched by token.
