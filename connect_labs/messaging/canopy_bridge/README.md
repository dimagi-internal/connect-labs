# Canopy agents in Labs Messaging (the canopy bridge)

A Connect Worker's PersonalID conversation can be answered by a canopy agent that knows the
worker. **Labs stays the system of record**: canopy holds an anchor (the contact, keyed
`personalid:<username>`), a pointer (`labs-worker://<username>`) and a small profile cache labs
keeps current; everything else stays in labs, and the agent reads more through tools bound to
that one worker.

Contracts: `CONTRACTS.md` S1-S6 (canopy SDK `CONTRACT_VERSION "1"`). Design:
`docs/superpowers/specs/2026-11-21-connect-worker-messaging-design.md`.

## The `workers` surface of labs' canopy site

Labs is ONE canopy host site, `connect-labs` (`CANOPY_APP_NAME`), with ONE key
(`CANOPY_SIGNING_KEY`) and ONE `site.json` (`/labs/canopy/site.json`). Its pages are the site itself
(staff, act-as-you grants, sensitive). Connect Worker messaging is its `workers` surface, which that
`site.json` declares (`labs.canopy.surfaces`):

| Fact            | Value                                                       |
| --------------- | ----------------------------------------------------------- |
| `sensitive`     | `false`: a Connect Worker's conversation is not sealed      |
| `mcp_resource`  | `{LABS_PUBLIC_URL}/messaging/canopy/mcp/`, the worker tools |
| `person_scheme` | `labs-worker`                                               |

canopy lets a Connect Worker (a contact of the site) hold a conversation only on that surface,
through an agent whose host-site entry names it (`surface: workers`, `contact_scoped: true`), and
sends that agent's tool calls for the worker only to its resource, never to labs' `/mcp/`.

| Route                             | What                                                      |
| --------------------------------- | --------------------------------------------------------- |
| `/messaging/canopy/mcp/`          | the worker tools, bound to one subject (S4)               |
| `/messaging/canopy/replies/`      | the reply webhook canopy pushes an agent's answer to (S5) |
| `/messaging/canopy/brief/<hash>/` | staff: "what the agent saw", one Worker Brief             |

## Settings

Everything is OFF unless `MESSAGING_ENABLED` and the site is configured (`conf.problems()` says
what is missing):

| Setting                                 | Default                      |                                                                    |
| --------------------------------------- | ---------------------------- | ------------------------------------------------------------------ |
| `CANOPY_SIGNING_KEY`, `CANOPY_APP_NAME` | (the site's own)             | the one key and name of labs' host site                            |
| `CANOPY_BASE_URL`, `LABS_PUBLIC_URL`    | (the site's own)             |                                                                    |
| the canopy workspace                    | learnt from canopy           | Labs Admin › Canopy agents (`canopy_settings.current().workspace`) |
| `CANOPY_MESSAGING_REPLY_WEBHOOK_PATH`   | `/messaging/canopy/replies/` | where canopy pushes replies (S5): `CANOPY_HOST["REPLY_URL"]`, declared as `site.reply_url` |
| `CANOPY_MESSAGING_REPLY_WEBHOOK`        | `False` (`True` on prod)     | on: labs waits for the push, polling only as a safety net; off: labs polls first |
| `CANOPY_MESSAGING_TURN_TIMEOUT_SECONDS` | `600`                        | an agent turn longer than this goes to a person                    |
| `CANOPY_CONTACT_RETENTION_DAYS`         | unset                        | `site.retention_days` in labs' site.json (1-3650)                  |

The surface's `HostConfig` (`conf.host_config()`) is the site's name, key and workspace with the
worker tools as its `resource` (the `aud` of every subject statement), the reply webhook as its
`reply_url` (`{LABS_PUBLIC_URL}{CANOPY_MESSAGING_REPLY_WEBHOOK_PATH}`, the `aud` every reply must
carry, and `CANOPY_HOST["REPLY_URL"]`, which labs' `site.json` declares as `site.reply_url` with
`site.reply_mode` `final`, so canopy adopts it as the site's reply webhook on its own) and no runner
requirement: canopy refuses a relay arrival
that requires sensitive-data runners on a surface that holds no sensitive data.

## The profile canopy caches (S2)

For every Connect Worker labs messages (one with a channel), labs pushes
`{sub, display_name, record_uri, profile: {stage, program, opportunity, language, consent,
timezone}, version}` to canopy's `POST /api/sites/contacts`: when their first channel is made,
when a `state_changed` / `profile_changed` / `consent_changed` event lands on their timeline (the
sweep reads events past a cursor), and before their first canopy session. Only a changed profile
is sent, at most 100 per request; `ConnectWorker.canopy_profile_version` is raised with each.
`ProfilePush` is labs' record of the last push.

Every item names `display_name`, `record_uri` and `profile`: canopy leaves an omitted field as it
holds it, and clears one sent as `""` / `{}`, so a name or labels labs no longer has are cleared
in canopy too. canopy refuses an update labs signed before canopy erased that person (`refused`,
reason `erased: ...`): that is final (never retried), and the worker's `canopy_contact_id` is
cleared.

## The agent handler (S1, S3, S5)

`handler.CanopyAgentHandler` is installed over labs' `agent` stub. A thread whose handler is
`agent` (`handler_ref` = `<agent>:<skill>`, from the playbook) is answered like this:

1. The worker's debounced messages are joined into one text.
2. The thread's canopy contact session is opened if it has none (`CanopySession`; one per
   thread and agent). The worker's profile is pushed first. The skill goes in the session
   metadata: `x_skill`, `x_thread`, `x_program`, `x_playbook`.
3. The Worker Brief is built and stored (`WorkerBrief`, by hash). The text is sent as the
   worker, with the brief as signed context (`canopy-context+jwt`), on channel `personalid`.
4. The answer arrives at the reply webhook (canopy-signed `canopy-reply+jwt`), or from the poll
   (`tasks.poll_turn`, backing off 3 s → 60 s). It is sent once per (turn, seq) as
   `thread.send(..., "agent", "<agent>:<skill>", canopy_turn_id=, canopy_session_id=, brief_hash=)`.
   - `(turn, seq)` is the idempotency key (`ReplyDelivery`): canopy signs every delivery attempt
     afresh, so a retry of a piece already sent is answered 200 `duplicate`. The same token twice
     is a replay (its `jti` is spent in `DjangoJtiStore`, shared by every worker) and is a 401.
   - In `final` mode a turn is one post with all its prose; in `stream` mode each piece is a post
     and the last (`ended`) has no text. A failed, cancelled, lost or missed turn carries `error`
     on its `ended` post: the turn is `failed` and a person takes the thread.
   - Sensitive conversations are never pushed; the poll answers those.
   - With `CANOPY_MESSAGING_REPLY_WEBHOOK` on, the poll still runs, from 120 s, as a safety net. A
     turn the poll delivered was delivered whole, so a later push for it is a `duplicate`.

Each send passes labs' own message id as `client_id` (`labs-message-<pk>`), so a retried send
collapses onto the first turn. One `RelayClient` per process (`handler.relay_client`).

canopy limits each contact's sends (6 a minute, 200 a day: `429 rate_limited`). The words wait in
labs and are sent again 65 s later (`tasks.kickoff`, `attempt=`), at most twice; still refused
(the daily limit), a person takes the thread, told why. A message the worker writes during the
wait carries the refused words with it. Any other refusal, a failed turn, or a
turn longer than `CANOPY_MESSAGING_TURN_TIMEOUT_SECONDS` hands the thread to a person, with the
reason.
`MessagingError` is respected:

- `turn_limit` → a person takes the thread, and the agent is not even asked once the limit is reached;
- `thread_closed` → the answer is dropped.

Handing the thread to another agent ends the session. The next session is seeded with the
handoff note, and if the worker is still waiting it is picked up after 45 s (`tasks.kickoff`).
Continuity comes from labs' record (the brief), never from resuming a canopy session.

## The Worker Brief

`brief.build(thread, inbound_text=...)` is deterministic, sectioned and capped at 6,000
characters. Its sections:

1. who
2. this thread
3. cautions and corrections (plus staff notes that are not private)
4. open commitments
5. recent history (the last 3 thread summaries in this program + the last 6 messages)
6. at most 8 facts, ranked by overlap with the inbound text
7. state highlights
8. pointers (`labs-worker://` URIs)

It opens with plain lines an agent's skill parses, because canopy does not pass a session's
`x_*` metadata to the agent:

- `Thread: <id>`
- `Program: <id> <name>`
- `Playbook: <key>@<version>`
- `Skill: <skill>`
- `Direction: inbound|outbound`

After "This thread" comes the program's own context: the playbook's `context.program`, which is
Markdown (the script, an app summary, persona notes) of at most 4 KB. It sits on top of the cap
and is never cut.

State highlights include Messaging's live facets (`messaging/facets.py`): `recently_coached`,
`last_coached_at` and `open_threads`. A playbook's `eligible.facets` can match them too, e.g.
connect-coach's inbound `coach-followup` with `{"messaging.recently_coached": {"eq": true}}`.
The window is the program's `messaging.settings.recently_coached_days`, default 14.

It is program-scoped. Another program's summaries appear only if the playbook sets
`context.other_programs`. Global facts cross programs.

None of these ever appears: restricted content, private notes, suspension reasons, staff notes
on a thread, or the worker's phone (also scrubbed from quoted text). "What the agent saw" is the
`WorkerBrief` whose hash is on the agent's message.

## The worker tools (S4)

`/messaging/canopy/mcp/` is the site's MCP resource. It is a stateless JSON-RPC endpoint that
canopy's gateway calls in subject mode, and it accepts **only**
`Authorization: CanopySubject <jws>` + `DPoP`. For every request:

1. `sdk.verify_subject` (the SDK's `canopy_sdk.host.subject.verify_subject`) checks the statement:
   - its signature against canopy's client metadata, `iss`, and `aud` = this resource;
   - a lifetime of at most 60 s;
   - the DPoP binding;
   - single use (`DjangoJtiStore`, shared by every worker: `conf.replay_store()`);
   - `rpc` = this JSON-RPC request.
2. `sub` resolves to the ConnectWorker, and `session` to the thread labs relays to that session. The
   session must be that worker's and that agent's.
3. Only the tools the thread's playbook lists in `tools` are listed or run.

These calls are not a labs user: they never reach `/mcp/` or any user-scoped tool, and no tool
takes a worker, thread or program argument.

The tools are in `tools.py`:

- reading: `worker_profile`, `worker_timeline`, `worker_threads`, `worker_thread`,
  `worker_history_search`, `worker_tasks`, `worker_learn`, `worker_visits`, `worker_payments`,
  `worker_facts`;
- memory: `worker_fact_record`, `worker_fact_retract`;
- the thread: `thread_record_outcome`, `thread_handoff`, `thread_close`.

How they behave:

- They follow the brief's visibility rules.
- `worker_history_search` searches in-process: messages are encrypted at rest, so Postgres
  full-text and pgvector cannot index them.
- A fact needs no approval (`asserted_by_kind=agent`). An obvious judgement of performance or
  character is refused. An agent retracts only agents' facts.
- `thread_handoff` and `thread_close` wait until the agent's answer has gone out (`PendingTurn.after`).
- Every call is in `MCPAuditLog` (`user` null; `execution` = the subject, the thread and canopy's turn).

## Summaries, OCS history, erasure

- **Fallback summaries** (`summaries.py`, beat `messaging-fallback-summaries`, every 10 minutes).
  When a thread closes or times out with no summary covering its last message, labs writes one:
  - extractive and deterministic (labs' `connect_labs/ai` helpers are workflow agents, and a
    worker's words should not go to a model just to be shortened);
  - author `system` / `labs-job`.
- **OCS history** (`ocs_import.py`; `manage.py import_ocs_history <usernames>|--all [--queue]`; task
  `import_ocs_history`). It imports a worker's past Open Chat Studio sessions
  (`workers.api.ocs_sessions_for`, team key) as closed threads:
  - each bot message has `author_ref ocs:<bot>` and `transport ocs`;
  - it is idempotent on `ocs:<session>:<n>`;
  - each thread is linked from the worker's `ocs_session` timeline event (`ref.thread_id`) and gets
    the fallback summary.
- **Erasure** (`erasure.py`). It is staff-only (Dimagi staff) and done from the Connect Worker page
  (type the username to confirm) or the admin action:
  - it deletes everything labs holds about the worker: messages, threads, summaries, channels,
    connect-id's stored posts, facts, notes, timeline, memberships, snapshots, briefs, commitments
    and sessions;
  - it blanks the record and keeps only the username with `erased_at`, so the sync skips them from
    then on;
  - it then calls canopy's erase (S6), retried by the sweep. `Erasure` records the counts and
    canopy's answer. canopy's `503 files_remain` (a stored file not deleted yet; the rest is done)
    is sent again by the sweep until it finishes (never failed after `MAX_ATTEMPTS`); the sweep
    takes the least-tried erasures first, so ones stuck on files never starve newer ones.

## The canopy SDK

`sdk.py` is the one place the bridge reaches the canopy SDK's host halves. Every name is the SDK's
own, from `dimagi-canopy` 0.37.0 (`sdk.implementations()` reports `"sdk"` for each):
`RelayClient` (0.32), `update_contacts` / `erase_contacts` and `site.person_scheme` /
`site.retention_days` (0.30), `verify_subject` (0.31), `sign_context` (0.33) and `verify_reply`
(0.34). `tests/test_sdk_wire.py` pins the wire, so an SDK bump that changes it fails there first.

The SDK's Django wrapper `canopy_sdk.django.execution.verify_subject` is not used: it verifies
against `CANOPY_HOST`, whose resource is labs' own `/mcp/`. The bridge calls the host function with
the workers surface's `HostConfig` (its resource the worker tools) and the same pieces the wrapper wires: `DjangoJtiStore` (shared by
every worker) and a `ClientKeyResolver` cached in Django's cache (`conf.replay_store()`,
`conf.client_keys()`). Reply verification uses the same two. The messaging MCP accepts only
`Authorization: CanopySubject`; it does not verify `Canopy-Execution` (it uses nothing in it), so
there is no `verify_execution(jkt=subject.jkt)` to pass.
