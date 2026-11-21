# Labs Messaging for Connect Workers, and canopy support for systems of record

Status: design proposal, revision 4, 2026-11-21. Not reviewed or built.

How this revision came about:

- Rev 2 made this a first-class labs app for direct messaging, with many approaches and agents chosen by the worker's state and by direction (outbound or inbound).
- Rev 3 gave the agent a complete picture of the worker and their history, using canopy's contact model and attached to labs.
- **Rev 4** follows Jonathan's direction:
  - make canopy strong enough to support cases like this in general, while **each system (labs among them) stays the system of record for its own people**;
  - have labs **pull task and activity history from Connect prod** to build a complete profile;
  - use Connect's own name for its mobile users.

Sources: current `main` of open-chat-studio `cb8559d`, connect-id `06f4c42`, connect-labs `451f13d`, commcare-connect `837fc91`, canopy-web `7cf4035`, canopy `582569a`, commcare-android `381bbdc`. Anything marked [I] is inference, not something read in the code.

**Terminology.** Connect's web interface calls its mobile users **Connect Workers**. Examples:
- `templates/opportunity/workers.html:6` "Displaying … Connect Workers"
- `opportunity/forms/assigned_tasks.py:62` `label=_("Connect Worker")`
- the worker tabs "Connect Workers / Learn / Deliver / Payments / Tasks" in `opportunity/views/worker.py:465-469`

Connect's code says `worker`. "FLW" survives in older code and in microplanning, and the export API just says `username`. This document says **Connect Worker** (or "worker" for short) in prose, and `ConnectWorker` / `worker_*` in code. Labs' existing `FLW` names (`ComputedFLWCache`, …) should move to the new term as they are touched.

---

## Part A — Canopy: systems of record for the people they bring

### A.1 Principle

> A host site is the **system of record** for the people it brings to canopy. Canopy keeps an **anchor** (who this is, keyed by the host's own id), a **pointer** (where the host's record lives), a **host-owned cache** (a few fields the host keeps current), and the **working sessions** of conversations. Canopy never merges a host's person with another host's, never becomes the authority for anything the host owns, and reaches the host's data only through the host, under the host's own authorization.

Canopy already states this, but does not yet act on it:

- `Contact.attributes` is *"A CACHE of what other systems say, never the authority"* (`apps/contacts/models.py:351-357`).
- `Person` is keyed on *"the SYSTEM that vouches for this person"*: `(issuer, signer, external_id)` (`:81-93`).
- Its docstring says *"Cross-TENANT knowledge is deliberately not a thing"* (`:32-38`).

What is missing is the machinery that lets a host stay the system of record: write its cache, point to its record, give a turn context, answer a turn's questions, receive replies and erase. Every gap is listed below, with where the fix goes.

### A.2 What a contact turn can and cannot do today [read]

| Need | Today |
|---|---|
| Host runs conversations server-to-server | **Works.** `mint_contact_token` is a server POST (SDK `host/signing.py:174-216`), `origins` may be empty (`embed_apps.py:173-199`), and every `/api/contact/*` route takes the bearer token. But contact tokens last 30 minutes, minting is capped at 120 per minute per issuer, and replies are pulled, not pushed. |
| Host keeps the person's details current | **No.** Only `sub`, `name`, `email` and `email_verified` are read from the assertion, and extra claims are dropped (`contact_api.py:216-279`). Name and email only ever fill in blanks (`services.py:325-331`). `attributes` can be written only by a workspace editor (`api.py:103-147`). |
| Canopy knows where the host's record lives | **No.** No field on `Contact` or `Person` holds a host resource URI. |
| Agent sees which person this is | **Partly.** The envelope's `contact` block has no `external_id`, no site and no person id (`caller_context.py:202-219`). The plugin hook does not print `contact` at all (`hooks/caller_context.py:336-416`). |
| Host gives the turn context | **No.** A send carries only `text`. Session `x_*` metadata never reaches the envelope. `page-state` is unsigned and meant for pages. |
| Agent asks the host for more, about this person only | **No.** `_for_contacts` hides any agent that serves the site (`contact_api.py:495-501`). Host calls need a passkey-confirmed `HostGrant`, which a contact can never hold (`host_grants.py:205-207`). `Canopy-Execution` names no subject (`execution.py:54-65`). |
| Replies reach the host | **Pull only.** There is no host callback. Slack's relay is the only server-side push (`slack/relay.py`). |
| Host erases a person | **No.** There is no erasure route, and `Contact.app` is `PROTECT`. |
| Limits per person and per agent | **No.** There is no per-contact send limit and no cost cap. |

### A.3 Changes to canopy

Each change is general: it serves labs, and equally HQ, the hub, ace-web or any future host. Each one is additive within the SDK's `CONTRACT_VERSION = "1"` ("additive, optional fields do not bump it", `contract.py:28-32`), and is declared in `site.json`, so a host opts in.

**S1. Relay channels (server-side conversations).**
- **Problem.** A host that carries a conversation for a person who never opens canopy (PersonalID, WhatsApp, SMS) has to look like a browser widget.
- **Changes:**
  - An assertion claim `canopy_channel` (e.g. `personalid`), parsed next to `parse_agent_claim` in `contact_api.contact_token`. `initiator.channel` (`initiator.py:151-162`) then records `relay:<site>:<channel>`.
  - A new turn origin `site_message` in `Turn.ORIGIN_CHOICES` / `CHAT_SEND_ORIGINS` (`turn.py:42-64`). The plugin treats it as **present**: `PRESENT_ORIGINS` in `hooks/caller_context.py:75`.
  - **Site-signed requests instead of tokens.** A relay site may call `/api/contact/sessions/*` with a per-request JWS signed by its own key (`sub`, `jti`, ≤60 s), like `resources-updated`. That removes the 30-minute re-mint and the 120/min ceiling. Alternatively, raise the per-issuer budget per site (`rate_limit.check_contact_token_issuer`).
  - An SDK helper, `canopy_sdk.host.relay`: `open_session(sub, agent, metadata)`, `send(session, text, context=…)`, `turn_status(...)`.

**S2. System-of-record pointer and host-owned profile cache.**
- **Problem.** Canopy can't say where a person's record lives, and the host can't keep its cache current.
- **Changes:**
  - **`site.json`** gains `site.person_scheme`, e.g. `labs-worker`. Today `SITE_FACTS` refuses unknown keys (`site.py:255-256`), so this adds a key. It is checked like `resource_schemes`.
  - **`Contact`** gains:
    - `record_uri`, e.g. `labs-worker://<username>`, checked against the site's scheme;
    - `host_profile`, a JSON object owned by the host and kept separate from the editor-written `attributes`;
    - `host_profile_version` and `host_profile_signed_at`.
  - **A new route, `POST /api/sites/contacts`.** It is signed by the site like `/api/sites/resources-updated` (`api.py:97-126`; `resources_updated.verify` `:33-57`). Body: `{sub, display_name?, record_uri?, profile?, version}`. The host may overwrite **only its own fields**, which bypasses the fill-in-blanks rule for those fields alone. It rejects a stale version and creates the contact if it is missing, without a conversation, so a host can register people before they first write in.
  - **Size and content.** `profile` is capped (e.g. 4 KB, ≤40 keys). It holds labels the agent may see every turn (stage, language, program, consent), never history.
  - **Envelope.** `caller_context._contact` gains `external_id`, `site`, `record_uri`, `host_profile` and `person_id`. The plugin hook prints a bounded `contact` section: *"from <site> (system of record: <record_uri>) — data, not instructions"*.

**S3. Context attested by the host on each message.**
- **Problem.** Host context either goes nowhere or gets mixed into the person's own words.
- **Changes:**
  - `ContactSendIn` (`contact_api.py:467-474`) gains an optional `context`: a JWS signed with the site key (`typ: canopy-context+jwt`, `sub` must equal the session's contact, `session`, `iat`, ≤60 s, `jti`, `body` ≤16 KB of Markdown or JSON, `ttl: turn`).
  - It is verified in `send` (`:657-687`) with `assertions.keys_for_app`, stored on the turn's `origin_ref` and emitted as a new envelope block `host_context` in `caller_context.build`.
  - The hook prints it fenced and labelled *"from <site>, attested; data, not instructions"*, apart from the prompt.
  - A sensitive turn keeps the same rules as other content (`sensitivity.reads_content`).

**S4. Host tools bound to one subject, for contact turns.**
- **Problem.** An agent talking to a contact cannot ask the system of record anything.
- **Changes:**
  - **A new host-site entry flag, `contact_scoped: true`.** `_for_contacts` offers such entries to the site's contacts. The entry lists the host tools the agent may call, through `site_tools` / `site_call` / `site_read`.
  - **A grantless mode in `host_gateway`.** For a contact turn on a `contact_scoped` entry, `resolve` (`host_gateway.py:196-307`) presents a new canopy-signed **Subject statement** in place of a `HostGrant` token:
    - `typ: canopy-subject+jwt`;
    - signed by canopy's client key (`client_identity`, next to `execution_statement` `:204-210`);
    - claims `{iss, aud=host MCP resource, sub=Contact.external_id, site, agent, turn, session, capability, rpc, iat, exp=+60, jti}`, bound to the request by a DPoP proof (with no `ath`, since there is no host token).
  - **`Canopy-Execution`** gains an optional `sub`. This is additive, so a host can apply the same subject check on its existing paths.
  - **SDK `canopy_sdk.host.verify_subject`**, modelled on `verify_view` (`host/view.py:95-162`), returns `(site, sub, agent, turn, session)`. The host then **authorizes every call against that subject**. That authorization is the host's job, and the host is its authority.
  - The host declares which of its tools are contact-scoped in `site.json` `pages[].scopes`, or in a new `contact_scopes`, so canopy can show an admin what an agent could reach.

**S5. Reply relay (push).**
- **Problem.** The host has to poll.
- **Changes:**
  - `AppCredential.reply_webhook_url`, validated like `jwks_url` through `host_grants/outbound.py` (https only, no private IPs, no redirects).
  - A receiver on `session_turn_finished` / `turn_events_appended`, for sessions with `embed_app` plus a contact. It POSTs a JWS signed by canopy's client key: `{sub, session, turn, seq, text, ended, error?}`. The host verifies with `ClientKeyResolver`.
  - Delivery happens exactly once per (turn, seq) and is retried with backoff, copying `SlackRelayPost` and `hcp_webhooks.deliver_due`. Mid-turn prose is optional per site (`reply_mode: final | stream`).

**S6. Erasure and retention driven by the host.**
- **Problem.** The system of record can't make canopy forget.
- **Changes:**
  - `POST /api/sites/contacts/erase`, signed by the site, body `{sub}`.
  - A new `contacts.services.erase(contact)` deletes contact tokens, host grants, contact sessions and turn content (building on the retention purge in `apps/retention/services.py`), plus any `PersonFact` / HCP rows. It deletes the `Person` when nothing else references it, and tombstones `(app, external_id)` with a reason so a later arrival is visible as such.
  - It confirms with a signed callback, like the HCP revocation webhook.
  - **Site retention.** A site may declare `site.retention_days` for its contact sessions. Canopy enforces the shorter of that and the workspace's own `RetentionRule`.

**S7. Limits.**
- **Problem.** There is no guard against cost or abuse.
- **Changes:**
  - per-contact send rate (per minute and per day), in the contact `send` path;
  - a per-agent daily cost or turn cap, checked by an admission rule (`apps/admission`);
  - a per-site concurrency cap on open relay turns.

  All three are visible on the agent's canopy page.

**S8. Identity across hosts: link, never merge.**
- **Problem.** The same human can be a contact of several hosts.
- **Changes:**
  - Keep one `Person` per `(issuer, signer, external_id)`, as now.
  - A host **may** assert a link, e.g. labs: *this Connect Worker is HQ mobile user X*. It goes through S2 as `profile.links: [{site, sub}]`, stored as a reference and shown to an agent only if both sites are on that agent's entry.
  - Canopy never merges two persons or copies data between them.

**Fix while there: doc and code drift** found in this review.
- The hook and `turn.md` mention a person "digest" that does not exist (`models.py:468`).
- The hook tells agents to run `canopy people show`, but agents get 403 there (`people_api.py:522-527`).

**What canopy keeps per person after S1–S8:** the anchor (`Contact`/`Person`), `record_uri`, `host_profile` (cache), `attributes`/`notes` (workspace's own), sessions and turns (working memory, subject to retention), and its own access logs (`PersonAccess`). Everything else stays with the host.

### A.4 Where each change goes (summary)

| Change | canopy-web | canopy SDK (`sdk/python/src/canopy_sdk`) | canopy plugin |
|---|---|---|---|
| S1 | `contact_api.contact_token`, `turn.py`, `rate_limit.py`, site-signed auth on `/api/contact/*` | `host/relay.py` | `PRESENT_ORIGINS` |
| S2 | `contacts/models.py`, new `/api/sites/contacts`, `caller_context._contact`, `site.py SITE_FACTS` | `host/contacts.py` (`update_contact`) | print `contact` block |
| S3 | `ContactSendIn`, `send`, `caller_context.build` | `host/relay.send(context=)` | print `host_context` |
| S4 | `_for_contacts`, `host_gateway.resolve/_auth`, `client_identity.subject_statement`, `execution.facts_for` | `host/subject.py` (`verify_subject`) | — |
| S5 | `AppCredential.reply_webhook_url`, relay receiver | `host/replies.py` (`verify_reply`) | — |
| S6 | erase route, `contacts.services.erase`, retention | `host/contacts.py` (`erase_contact`) | — |
| S7 | contact send limits, admission rule | — | — |
| S8 | `host_profile.links` | — | — |

---

## Part B — Labs as the system of record for Connect Workers

### B.1 The ConnectWorker record

**`ConnectWorker`** is one per human.

- **Key.** The **PersonalID username**. Connect's `User.username` *is* the PersonalID username: invites `update_or_create(username=…)` from ConnectID (`opportunity/tasks.py:138-141`), and mobile tokens are introspected at ConnectID. Every export field is `username`.
- **Also stored:** Connect's `user_id` UUID. It is Connect-local, and appears only in `user_visits`.
- **Fields.** `name` and `phone` change on re-invite, so they are kept as mutable fields with history.
- **Canopy fields:** `canopy_sub = "personalid:<username>"` and `record_uri = "labs-worker://<username>"`.

Earlier ids in labs (`connect_username` in supply-chain, alerts, update-links and partners, and roster keys `"<opp>::<username>"`) become foreign keys to `ConnectWorker` through a backfill.

**Other tables:**

| Table | Holds |
|---|---|
| `WorkerMembership` | One row per (worker, opportunity), with program and org |
| `WorkerState` | Lifecycle stage plus facets, derived (§B.3) |
| `WorkerEvent` | The timeline (§B.2) |
| `Address` | Per (worker, transport, channel), with consent history |
| `Thread`, `Message`, `ThreadSummary` | Messaging history |
| `WorkerFact` | Durable learned facts, with provenance |
| `StaffNote` | Notes by staff |

These are as described in rev 3, with `ConnectWorker` in place of `Worker`.

### B.2 Pulling a complete profile from Connect prod

**What Connect exports per worker today** (`data_export/urls.py`, OAuth scope `export`, v2 JSON with keyset pagination by `last_id`). Every endpoint is **per opportunity**. Only `completed_module` filters by `username`, and **none has a `since` filter**.

| Export | Gives labs | Notes |
|---|---|---|
| `opp_org_program_list/` | The opportunities, programs and orgs the caller can see | Lists only the caller's own org and program opportunities, not supervising-org or funder opportunities (`views.py:344-350`). Probe those with `opportunity_access/`. |
| `opportunity/<id>/user_data/` | Invite date and status, learn started and completed, claim date and limits, suspended + date + reason, `last_active`, `payment_accrued` | No `user_id`, no access id, no `accepted` |
| `…/completed_module/?username=` | Learn module completions with date and duration | Module id only. Names come from `app_structure/` (proxied from HQ). |
| `…/assessment/` | Assessment attempts: score, passing score, passed | |
| `…/user_visits/` | Every deliver visit: status, flagged + reason, review status, justification, GPS, `form_json`, attachments | ~25 KB a row, about all of it `form_json`, which can't be excluded |
| `…/completed_works/` | Work units: status, payment date, saved amounts | No `id` in the payload |
| `…/payment/` | Payments: amount, date paid, confirmed, method, operator | No `id`. Deletions just disappear. |
| `…/task_types/` + `…/assigned_tasks/` (v2) | **Tasks**: type (relearn or OCS mode), status `assigned`/`completed`, due date, created, completed | No `assigned_by`, `ocs_session_id`, `connect_channel_id` or `date_modified` |
| `…/audit_report_entries/` (v2) | Audit results: flagged, reviewed, review action | |
| `…/work_areas/` (v2) | Current work-area assignments and status | |

**How labs syncs (a `connect_worker_sync` Celery job):**

1. **Coverage.** Run as labs' service poller identity, the one Pulse already uses (`pulse/client.py:95`). List opportunities with `opp_org_program_list/` and probe the others with `opportunity_access/`. Record on each `ConnectWorker` which opportunities were *visible* at sync time, so the profile can say *"complete for these opportunities only"*.
2. **Cheap tier, every 15–60 minutes for each active opportunity:** `user_data`, `assigned_tasks`, `task_types`, `completed_module`, `assessment`, `payment`, `completed_works`, `audit_report_entries` and `work_areas`. These are small rows. Pull them in full, compare each with the previous snapshot, and write the differences.
3. **Visits.** **Reuse** Pulse and the analysis cache rather than pull them a second time. Pulse already tails `user_visits` by `last_id`, with hot, warm, cold and dormant tiers. The profile keeps a **thin projection** (visit id, date, status, flagged, review status, entity, deliver unit), never `form_json`. Status changes on older visits arrive with the analysis cache's full rebuilds.
4. **Timeline from differences.**
   - Connect keeps no history for suspensions, task status, review outcomes or payment deletions; its `pghistory` covers only `AssignedTask.due_date`, `WorkArea` and invoices, and none of that is exported. So labs writes `WorkerEvent`s from snapshot differences.
   - Each event: `kind` (invited, learn_started, module_completed, assessment, claimed, visit, visit_reviewed, task_assigned, task_completed, task_due_changed, suspended, unsuspended, payment, work_area_assigned, message_in, message_out, state_changed…), `opportunity`, `occurred_at` (when Connect gives a timestamp), `observed_at` (when labs noticed), `source` (connect | labs | ocs | personalid), `ref` (the row in labs or Connect), and `confidence` (exact | observed).
   - Example: a suspension that starts and is then lifted between two syncs is invisible. One that is still in place shows up as `suspended`, with Connect's `suspension_date`.
5. **OCS-mode tasks.** `AssignedTask` with `mode=ocs` carries `ocs_session_id`, which the export omits. Until it is exported, match through OCS: `list_sessions` filtered by participant = username, and `participant_data.connectTaskId`. Import the transcript as a `Thread` (source `ocs`), linked to the task event.
6. **Messages Connect sends** (invite SMS and push, the daily inactive nudges, payment received, task assigned and completed, assessment scored, the free-text "Send Message" page, work-area assignment) are **not recorded anywhere in Connect** (`connect_id_client/main.py:29-45` discards the response). The profile cannot include them until Connect logs them (§B.4). Labs' own messages are complete, because Messaging stores every one.

**What a profile holds once assembled.** For every opportunity labs can see:

- identity, invite, learn, claim and suspension, from `user_data`;
- every learn module, with date and duration, and every assessment, with score;
- every visit (thin), with status and review outcome;
- every work unit and payment;
- every task assigned, with type, mode, due date and completion, plus OCS coaching transcripts;
- audit results and work areas;
- every message labs exchanged, by any handler;
- facts and staff notes;
- a derived lifecycle stage and facets.

Each piece records its source and when it was last refreshed.

### B.3 Worker state (derived from the profile)

The stage per membership, first match wins:

1. `suspended`
2. `invited` (not accepted)
3. `not_started`
4. `learning`
5. `ready_to_claim`
6. `at_cap`
7. `delivering` (active within N days)
8. `inactive`

Facets alongside the stage:

| Facet | Contents |
|---|---|
| learn | modules done of total, assessment passed |
| performance | red and yellow indicators from workflow grading, peer-watch |
| verification | flagged, rejected and pending share |
| payment | accrued, last paid |
| tasks | open, overdue |
| supply | stock, days to stock-out |
| messaging | consent, open threads, last contact |

A change of stage or a facet crossing a threshold writes a `WorkerEvent(kind=state_changed)`, which can trigger a playbook.

### B.4 Asks of Connect (upstream PRs, in order of value)

1. **A cross-opportunity export per worker.** `GET /export/user/<username>/opportunities/` returns user_data rows for every opportunity the caller can access. Also add `?username=` on every per-opportunity list (`completed_module` already has it).
2. **Incremental sync.** `?modified_since=` on `user_visits`, `completed_works`, `user_data`, `assigned_tasks` and `payment`; `exclude=form_json` on `user_visits`; deletion tombstones.
3. **More task fields.** Add `assigned_by`, `ocs_session_id`, `connect_channel_id`, `date_modified`, `mode` and `opportunity_access_id` to `assigned_tasks`; `ocs_chatbot_id` to `task_types`; and export the existing due-date pghistory events. Track task status and deletion too.
4. **An event history.** Add pghistory trackers on `OpportunityAccess` (suspended, suspension, accepted), `UserVisit` (status, review status), `CompletedWork.status` and `Payment` (insert and delete), with `GET /export/opportunity/<id>/events/?since=&username=`.
5. **A notification log.** A `WorkerNotification` model written by every send to ConnectID, exported as `…/notifications/?username=&since=`.
6. **Stable ids in every payload** (`completed_works`, `payment`, `assessment`, `completed_module`, `user_data`).
7. **A scoping fix.** `opp_org_program_list` should use `_accessible_opportunities`, so supervising-org and funder opportunities are listed.
8. **Learn module names.** `GET …/learn_modules/`.

Labs works without any of these. Each one replaces a difference-based or approximate part of the profile with an exact one.

### B.5 What labs exposes about a worker

- **To staff:** the **Connect Worker page**: profile, a timeline across Connect, labs, OCS and PersonalID, threads, facts with provenance, notes, consent, coverage ("complete for opps …"), and "what the agent saw" for every agent message.
- **To canopy:**
  - S2 `POST /api/sites/contacts` keeps each worker's `record_uri` and `host_profile` current: `{stage, program, opportunity, language, consent, timezone}`. It runs when those change, and at enrolment, so the contact exists before the worker's first message.
  - S3 sends the per-turn **Worker Brief** (rev 3 §4.2) with each message.
  - S4 serves the **worker tools**, bound to the subject in canopy's Subject statement:
    - `worker_profile`
    - `worker_timeline(since, kinds)`
    - `worker_threads`, `worker_thread`, `worker_history_search`
    - `worker_tasks`, `worker_learn`, `worker_visits` (thin), `worker_payments`
    - `worker_facts` / `_record` / `_retract`
    - `thread_record_outcome` / `_handoff` / `_close`
  - Each tool is limited to the subject, the thread's program (the visibility rules below) and the playbook's allowed list.
  - S6: labs erasure cascades to canopy.
- **Visibility rules:**
  - History is limited to the program by default. A worker may be in programs of different orgs, and an approach sees summaries from other programs only if its playbook opts in.
  - Restricted content (safety disclosures, suspension reasons, payment disputes, private notes) is for staff only.
  - Phone numbers are never shown to an agent.
  - Every read is audited in `MCPAuditLog` with the thread and the canopy turn.

---

## Part C — Labs Messaging (unchanged in substance from rev 3)

- **Transport.** Labs is its own PersonalID message server: `create_channel` / `create_message`, the key, delivery (returns exactly 200, then queues the work), consent and callback endpoints. By default there is **one channel per (Connect Worker, program)**, plus dedicated channels where one is needed (security codes).
- **Router and playbooks.**
  - Playbooks are versioned per program in `ScopeConfig`. Each defines:
    - direction and trigger: a person, a schedule, a `WorkerEvent`, or the API;
    - eligibility over `WorkerState`;
    - the handler: an agent with a skill, a flow, a person, or none;
    - the opening, sent verbatim;
    - the approval mode;
    - caps and quiet hours;
    - exit and handoff rules;
    - context depth and allowed tools;
    - variants.
  - Outbound: eligibility, then caps, then approval, then the thread opens and the opening is sent.
  - Inbound: an open thread goes to its handler; otherwise the first inbound playbook that matches the worker's state takes it; otherwise the human Inbox.
- **Handlers.** Agent (one canopy contact session per thread, through S1/S5), flow, human (labs Inbox, with takeover) and none. Every message, from any handler, is stored on the worker's record. Continuity across threads, agents and handlers comes from labs' record, never from resuming a canopy session.
- **Agents.** A small family of worker-facing agents, `connect-helper` and `connect-coach`, with one skill per approach. ACE builds and maintains each program's playbooks, skills and context.
- **Starter approaches:**

| Approach | Direction | Handled by |
|---|---|---|
| `welcome` | outbound | flow |
| `learn-stalled` | outbound | agent |
| `claim-help` | outbound | agent |
| `coach-on-flags` | outbound | agent (a person confirms the batch) |
| `task-due` | outbound | flow (new; from `assigned_tasks`) |
| `stock-low` | outbound | flow |
| `verify-code` | outbound | flow |
| `payment-notice` | outbound | flow |
| `inactive-checkin` | outbound | agent |
| `suspended` | inbound | human |
| `coach-followup` | inbound | agent |
| `learn-support` | inbound | agent |
| `general-help` | inbound | agent |
| `fallback` | inbound | human |

- **Migration.** Coaching, alerts and update-link codes move off OCS; past OCS conversations are imported; `communications` gains PersonalID recipients.
- **Rails.**
  - The first message discloses that an AI may answer.
  - Unsubscribe stops sends.
  - Caps are enforced in labs until S7.
  - The delivery endpoint does no work inline, because connect-id never retries.
  - **Security asks of connect-id:**
    - `send_fcm` doesn't check the channel's owner;
    - user-side `send_message` / `update_consent` are unscoped;
    - all servers probably share one throttle bucket [I].
  - Prompt injection is contained by tools limited to the subject, no tool that sends, and attested context.
  - Retention and erasure cascade from labs.

---

## Part D — Build plan

| Phase | Delivers | Repos | Done when |
|---|---|---|---|
| 0. Prerequisites | connect-id registers labs on staging. Confirm `RICH_MESSAGING_ENABLED`. Send the connect-id security asks. File the Connect asks (§B.4). Decide the canopy S-series with the canopy owner. | connect-id, commcare-connect, canopy-web (issues) | Labs creates a PersonalID channel for ACE's test worker |
| 1. ConnectWorker profile | `ConnectWorker`, memberships, `connect_worker_sync` (cheap tier + Pulse visits projection), `WorkerEvent` differences, OCS task-transcript import, the Connect Worker page | connect-labs | The page shows a complete timeline for a real worker across every opportunity labs can see, with coverage stated |
| 2. Transport + Inbox | PersonalID transport, `Thread` / `Message`, human handler, Inbox | connect-labs | Staff converse with the test worker on the emulator, and it appears on the worker's timeline |
| 3. Canopy S1–S4 | Relay channel, record pointer + host profile, attested context, subject-bound tools, SDK helpers | canopy-web, canopy SDK, canopy plugin | A contact turn on staging receives labs' brief, sees `record_uri`, and calls `worker_timeline` only for its own subject (a test refuses a call about another worker) |
| 4. State, router, flows, agents, memory | `WorkerState`, playbooks, router, flows, BriefBuilder, worker tools, summaries, facts, `connect-helper` / `connect-coach` | connect-labs, agent repos, ace | Coach personas pass on mobile, and a continuity test passes across two threads with different agents |
| 5. Canopy S5–S7 | Reply push, erasure, limits | canopy-web | Latency is measured before and after; erasure removes a worker end to end |
| 6. Connect upstream + migration | Adopt the §B.4 endpoints as they land (replacing difference-based events); move programs off OCS; experiments and metrics | commcare-connect, connect-labs, ace | One program fully on Messaging, with a profile whose events are exact, not observed |

Before any real Connect Worker is involved: S1–S4, the program-scope visibility rule, and the connect-id security asks.

---

## Part E — Decisions for Jonathan

1. **Canopy S-series ownership.** Do S1–S8 go to canopy-web as one design doc for its owners, or straight to PRs, with ACE writing them?
2. **History across programs.** By default a worker's history is limited to the program. Should any approach ever see summaries from a worker's other programs, possibly run by other orgs?
3. **Connect asks.** Which of §B.4 to push first? I'd start with #1 (the cross-opportunity worker export) and #3 (task fields), because they matter most for tasks and coaching.
4. **Memory writes.** Do agents write facts and summaries automatically, with staff corrections (recommended), or only after staff approve?
5. **Labs' poller identity.** The profile only covers opportunities the labs sync identity can reach. Use Pulse's poller, or a dedicated `ConnectWorker` sync identity with explicit program grants?

## Part F — Open questions to verify

- Is the PersonalID username immutable for life? Connect treats it as the stable key [I].
- Can canopy's DPoP-without-`ath` binding (S4) satisfy its security review, or does S4 need a short-lived host-issued token for each turn?
- Does labs' RDS have pgvector (history search)? If not, use Postgres full-text search for v1.
- What is the reply latency of a resumed relay session on a warm cloud runner?
- Does ConnectID keep any log of the push notifications Connect sends, as an interim source for notification history?
