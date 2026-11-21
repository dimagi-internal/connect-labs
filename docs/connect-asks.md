# Asks of Connect for Connect Worker profiles

Labs keeps a profile and timeline for every Connect Worker it can see
(`connect_labs/workers/`). It builds them from Connect's export API, which was made
for per-opportunity reports, not for one person's history. Everything labs does to
work around that sits behind one interface, the **Connect facade**
(`connect_labs/workers/connect_facade/`): `ConnectWorkerSource`'s methods are the
endpoints below. When Connect ships one, a `NativeConnectSource` declares that
method in its `capabilities.native` and is added to `NATIVE_SOURCES`; `RoutedSource`
sends that method to it and nothing else in labs changes.

Labs works without any of these. Each one replaces a derived or approximate part of
a profile with an exact one. They are in order of value. Design:
`docs/superpowers/specs/2026-11-21-connect-worker-messaging-design.md` § B.4.

These are written up for the Connect team; nothing has been filed upstream from here.

All shapes below follow the existing v2 export conventions: OAuth scope `export`,
`Accept: application/json; version=2.0`, keyset pagination by `last_id`, `next` +
`results`, and the caller's access decided by `_accessible_opportunities(user)`.

---

## 1. One export per worker, across opportunities

**Today.** Every export is per opportunity, and only `completed_module` takes
`?username=`. To find one worker's opportunities, labs reads `user_data` for every
opportunity it can see (~500 requests) and keeps that worker's rows
(`ExportFacade.worker_opportunities`). Every other kind is pulled whole and filtered
in labs.

**Ask.**

```
GET /export/user/<username>/opportunities/
→ {next, results: [<user_data row> + {opportunity_id, opportunity_name, program_id, organization}]}
```

One row per opportunity the caller can access in which the user has an
`OpportunityAccess`. Plus `?username=` on every per-opportunity list
(`user_data`, `assessment`, `completed_works`, `payment`, `assigned_tasks`,
`audit_report_entries`, `work_areas`, `user_visits`).

**Why.** A worker's profile, or a message arriving from them, should cost one request,
not one per opportunity. Facade methods: `worker_opportunities`, and
`entities(..., username=)` for every kind (`Capabilities.username_filter`).

## 2. Incremental sync

**Today.** No export has a `since`. Labs pulls each opportunity's small exports in full
every ~30 minutes and diffs them against the last pull. `user_visits` carries each
visit's whole form (`form_json`, ~25 KB of the ~25 KB row) and cannot leave it out,
so labs never pulls visits for profiles; it reads its own caches (Pulse's tail and the
analysis visit cache).

**Ask.**

```
GET /export/opportunity/<id>/{user_visits,completed_works,user_data,assigned_tasks,payment}/?modified_since=<iso8601>
GET /export/opportunity/<id>/user_visits/?exclude=form_json
GET /export/opportunity/<id>/<kind>/deleted/?since=<iso8601>   → {results: [{id, deleted_at}]}
```

`modified_since` filters on each model's `date_modified` (add one where missing).
Deletion tombstones (or a `deleted_at` soft-delete exported with `modified_since`) so a
deleted payment is a recorded fact, not an absence.

**Why.** Full pulls are the main cost of keeping profiles fresh, and change times are
the difference between an *observed* event ("labs noticed by 14:30") and an *exact* one.
Facade: `entities(..., since=)` (`Capabilities.since`), and visits via `visits_thin`.

## 3. More task fields

**Today.** `assigned_tasks` has no `assigned_by`, `ocs_session_id`,
`connect_channel_id`, `date_modified`, mode or `opportunity_access_id`; `task_types`
has no `ocs_chatbot_id`. Labs joins the mode from `task_types` and finds a task's OCS
coaching session by listing OCS sessions for the participant and matching
`participant_data.connectTaskId` (`workers/ocs_link.py`).

**Ask.** Add to `AssignedTaskDataSerializer`: `assigned_by` (username), `ocs_session_id`,
`connect_channel_id`, `date_modified`, `mode` (from the type), `opportunity_access_id`.
Add `ocs_chatbot_id` to `TaskTypeDataSerializer`. Export the existing
`pghistory` due-date events (`AssignedTask.due_date` is already tracked):

```
GET /export/opportunity/<id>/assigned_tasks/<assigned_task_id>/history/
```

Track `status` and deletion in the same history.

**Why.** Tasks drive coaching. Who assigned a task, and which conversation it opened,
are what staff and an agent need to follow up; matching through OCS is slow and can
miss a session.

## 4. An event history

**Today.** Connect keeps no history of suspensions, task status, review outcomes or
payment deletions; its `pghistory` covers only `AssignedTask.due_date`, `WorkArea` and
invoices, and none of it is exported. Labs writes timeline events from differences
between pulls (`confidence=observed`). A suspension started and lifted between two
pulls is invisible. `ExportFacade.events()` returns nothing, and says why.

**Ask.** `pghistory` trackers on `OpportunityAccess` (`suspended`, `suspension_date`,
`suspension_reason`, `accepted`), `UserVisit` (`status`, `review_status`),
`CompletedWork.status` and `Payment` (insert and delete), exported as:

```
GET /export/opportunity/<id>/events/?since=<iso8601>&username=<username>
→ {next, results: [{id, model, object_id, username, field, old, new, occurred_at, actor}]}
```

**Why.** Every observed event becomes exact, and nothing that happens between pulls is
lost. Facade: `events()` (`native` key `events`).

## 5. A notification log

**Today.** Connect does not record the messages it sends a worker: invite SMS and push,
the daily inactivity nudges, payment received, task assigned and completed, assessment
scored, the free-text "Send Message" page, work-area assignment.
`connect_id_client/main.py` discards ConnectID's response. Labs cannot show them, and
says so on every profile. `ExportFacade.notifications()` returns nothing.

**Ask.** A `WorkerNotification` model written by every send to ConnectID (kind, channel,
title/body or template, `sent_at`, ConnectID's response status and message id),
exported as:

```
GET /export/opportunity/<id>/notifications/?username=<username>&since=<iso8601>
GET /export/user/<username>/notifications/?since=<iso8601>
```

**Why.** Staff and agents speaking to a worker need to know what Connect already told
them, or they repeat or contradict it. Facade: `notifications()`.

## 6. Stable ids in every payload

**Today.** `completed_works`, `payment`, `assessment`, `completed_module` and
`user_data` rows carry no id. Labs keys them on fields that never change over the
row's life (`connect_facade/shapes.synthetic_key`); two genuinely identical payments
would collide.

**Ask.** Add `id` (or the model's UUID) to each serializer; for `user_data`, the
`OpportunityAccess` id.

**Why.** Exact identity for diffs and dedupe. Facade: `Capabilities.stable_ids`.

## 7. List every opportunity the caller can access

**Today.** `opp_org_program_list` lists only the caller's own organisations' and
programmes' opportunities (`Q(organization__in=…) | Q(program__organization__in=…)`),
while every per-opportunity export allows supervising-organisation and funder access
too (`_accessible_opportunities`). Labs probes ids it already knows of through
`opportunity_access` and reads each found one's detail separately (which carries no
programme).

**Ask.** Build `opp_org_program_list`'s opportunities from
`_accessible_opportunities(request.user)`, with the programme and the relationship
(`own | program | supervising | funder`).

**Why.** Coverage: a profile can only say "complete for these opportunities" if labs can
list them. Facade: `accessible_opportunities()`.

## 8. Learn module names

**Today.** `completed_module` gives a module id only. Labs reads names from the
single-opportunity export's `learn_app.learn_modules`.

**Ask.**

```
GET /export/opportunity/<id>/learn_modules/ → {results: [{id, slug, name, description, time_estimate}]}
```

**Why.** Small, but it is the one thing every learn timeline shows. Facade:
`learn_modules()`.
