# Messaging

Labs' own conversations with Connect Workers over **PersonalID messaging**: labs is a
connect-id **message server**, so it sends with its own credentials, holds each channel's key,
and hears replies, consent changes and delivery receipts on its own endpoints. Every message,
from any author (a Connect Worker, an agent, a flow, a member of staff, labs itself), is stored
here and written to the worker's timeline (`workers.WorkerEvent`, `message_in` / `message_out`).

Design: `docs/superpowers/specs/2026-11-21-connect-worker-messaging-design.md`, Part C.

**Everything is off by default.** With `MESSAGING_ENABLED` unset, the four PersonalID endpoints
answer 404 and nothing is sent. With it on, a program still sends nothing until its own switch
is on (labs Settings › Messaging › `enabled`, namespace `messaging.settings`).

## How it fits together

| Piece                                                                                                                | File                                        |
| -------------------------------------------------------------------------------------------------------------------- | ------------------------------------------- |
| `Address` (a channel: worker, program, consent history, key), `Thread`, `Message`, `ThreadSummary`, `TransportEvent` | `models.py`                                 |
| The one door for sending and hearing: rails (on/off, consent, STOP, disclosure, links), timeline                     | `service.py`                                |
| PersonalID client (`create_channel`, `create_message`, `userinfo`) and the `Transport` interface                     | `transport/`                                |
| The channel encryption (AES-256-GCM, connect-id's and OCS's formats)                                                 | `crypto.py`                                 |
| The endpoints connect-id and the phone call                                                                          | `personalid_views.py`, `personalid_urls.py` |
| Who answers a thread (agent, flow, person, none)                                                                     | `handlers/`                                 |
| Background work: delivery with retries, processing what connect-id posted, the sweep                                 | `tasks.py`                                  |

## The PersonalID contract (verified against connect-id and open-chat-studio)

- Labs calls connect-id with HTTP Basic `client_id:secret` (connect-id's `MessagingServerAuth`).
- `POST /messaging/create_channel/ {connectid, channel_source, channel_name}` makes or finds a
  channel (201 / 200, `{channel_id, consent}`). Labs keys channels as `labs-program-<id>`: one per
  (Connect Worker, program).
- `POST /messaging/create_message/` (multipart; needs `RICH_MESSAGING_ENABLED` on connect-id). Labs
  **never** uses `send_fcm`, which does not check that the channel is the caller's.
- A retry reuses the message's id; `MESSAGE_ID_ALREADY_EXISTS` means it already arrived.
- connect-id signs what it posts to labs: `X-MAC-DIGEST` = base64(HMAC-SHA256(secret, body)).
- The delivery endpoint must answer **exactly 200**, quickly: connect-id never retries. Labs
  stores the post (`TransportEvent`, ciphertext only) and does the work in Celery; the
  `messaging-sweep` beat task picks up anything a lost task left behind.
- The key endpoint gets form `channel_id` and `Authorization: Bearer <PersonalID token>` from the
  worker's phone. Labs asks `GET {PERSONALID_SERVER_URL}/o/userinfo/` whose token it is and serves
  the key only to that channel's worker (`sub` compared case-insensitively).
- Text is Markdown on the phone; bare URLs are not tappable, so labs turns them into links.

## Playbooks and the router

A program's playbooks are labs Settings (`messaging.playbooks`, program layer), changed by its members with
history and undo (`playbooks.set_playbook`, MCP `messaging_playbook_set`). See the docstring of
`playbooks.py` for the full shape; in short a playbook has a `direction`, a `trigger` (outbound), an
`eligible` predicate over `WorkerState`, a `handler`, a verbatim `opening`, an `approval` mode, `caps`
(max_turns, threads_per_worker_per_week, quiet_hours), `exit`, `handoffs`, `context`, `tools` and
`variants`.

- **Outbound**: `router.preview` (who would get what, and who is left out and why) then `router.commit`
  with the single-use `confirm` token. A schedule, a state event or an API caller uses
  `router.start_automatic`, which only an `auto_within_caps` playbook (with `approved_by`) allows. Quiet
  hours hold the opening back until they end.
- **Inbound**: the open thread's handler, else the program's first matching inbound playbook (lowest
  `priority`), else the Inbox. Messages are debounced per thread (`MESSAGING_DEBOUNCE_SECONDS`).
- **State events**: the sweep reads new `WorkerEvent`s past a cursor (the sync writes them in bulk, with no
  signals) and starts any `state_event` playbook whose kinds (and `stage_to`) match.

## Plugging in a handler (the canopy bridge)

```python
from connect_labs.messaging import handlers
from connect_labs.messaging.handlers import Handler

class CanopyAgentHandler(Handler):
    def start(self, thread, ctx): ...            # thread just opened; ctx has "playbook", "opening", "context"
    def on_message(self, thread, msgs): ...      # debounced batch, oldest first; handlers.joined(msgs) for one text
    def on_handoff_in(self, thread, from_, note): ...
    def stop(self, thread, reason): ...

handlers.register("agent", CanopyAgentHandler())   # in your AppConfig.ready(); replaces labs' stub
```

A handler replies with `thread.send(text, "agent", "<agent>:<skill>", canopy_turn_id=..., canopy_session_id=...,
brief_hash=...)`, hands off with `handlers.to_human(thread, reason)` or `router.handoff_to(thread, "<playbook
key>" | "human", note=...)`, records what the thread came to with `service.record_summary(thread, text, author_kind="agent", outcome=...)`, and ends with `thread.close(reason, by=...)`. `service.send` raises
`MessagingError` (`code`: `messaging_off`, `program_off`, `thread_closed`, `not_reachable`, `turn_limit`,
`invalid`) instead of sending; on `turn_limit` hand the thread to a person. A handler that raises is
caught and the thread goes to a person.

## Settings

| Setting                              | Default                        | Where it comes from                           |
| ------------------------------------ | ------------------------------ | --------------------------------------------- |
| `MESSAGING_ENABLED`                  | `False`                        | env (`environment[]` in the task definitions) |
| `PERSONALID_SERVER_URL`              | `https://connectid.dimagi.com` | env                                           |
| `PERSONALID_MESSAGING_CLIENT_ID`     | empty                          | Secrets Manager                               |
| `PERSONALID_MESSAGING_CLIENT_SECRET` | empty                          | Secrets Manager                               |
| `MESSAGING_DEBOUNCE_SECONDS`         | `8`                            | env (optional)                                |
| `MESSAGING_SIMULATION_WORKERS`       | empty                          | env: test users `messaging_simulate` may use  |

## Turning it on in an environment (not done yet)

These are **not** in `deploy/task-definitions/` yet, on purpose: a `secrets[]` entry whose ARN
does not exist fails the deploy. Do it in this order:

1. **connect-id registers labs as a message server** (a `MessageServer` row with its own
   `ServerKeys`), with these URLs (staging host shown; production is `https://labs.connect.dimagi.com`):

   | connect-id field | URL                                                  |
   | ---------------- | ---------------------------------------------------- |
   | `key_url`        | `https://<labs host>/messaging/personalid/key/`      |
   | `delivery_url`   | `https://<labs host>/messaging/personalid/delivery/` |
   | `consent_url`    | `https://<labs host>/messaging/personalid/consent/`  |
   | `callback_url`   | `https://<labs host>/messaging/personalid/callback/` |

   and confirms `RICH_MESSAGING_ENABLED` is on (labs sends with `create_message` only).

2. **Create two secrets** in Secrets Manager (us-east-1) from the `ServerKeys` connect-id issued:
   `labs-jj-personalid-messaging-client-id` and `labs-jj-personalid-messaging-client-secret`.
3. **Add to BOTH `web.json` and `worker.json`** (the web tier verifies signatures and serves keys;
   the worker sends):

   ```json
   "secrets": [
     {"name": "PERSONALID_MESSAGING_CLIENT_ID", "valueFrom": "arn:aws:secretsmanager:us-east-1:858923557655:secret:labs-jj-personalid-messaging-client-id-XXXXXX"},
     {"name": "PERSONALID_MESSAGING_CLIENT_SECRET", "valueFrom": "arn:aws:secretsmanager:us-east-1:858923557655:secret:labs-jj-personalid-messaging-client-secret-XXXXXX"}
   ],
   "environment": [
     {"name": "PERSONALID_SERVER_URL", "value": "https://connectid.dimagi.com"},
     {"name": "MESSAGING_ENABLED", "value": "False"}
   ]
   ```

   (the `-XXXXXX` suffix is the one Secrets Manager adds; copy the full ARN). Deploy with
   `MESSAGING_ENABLED` still `False`, then flip it to `True` in a second PR once connect-id is
   pointing at labs.

4. Turn a program on: labs Settings › the program › Messaging › `enabled` (or MCP `labs_config_set`
   on `messaging.settings`), and give its channel a name.

### The canopy bridge (the `workers` surface of labs' canopy site)

Labs is ONE canopy host site, `connect-labs`, signed with its one key (`CANOPY_SIGNING_KEY`). Connect
Worker messaging is its `workers` surface, declared in the same `site.json`
(`/labs/canopy/site.json`, `labs.canopy.surfaces`): not sensitive, its MCP resource the worker tools
(`/messaging/canopy/mcp/`), its people's records `labs-worker://<username>`. The bridge needs
`dimagi-canopy` >= 0.37.0 (pinned in `requirements/base.in`). There is no second site, key or
registration.

What is already in place:

- the site key, `CANOPY_BASE_URL` and `LABS_PUBLIC_URL` (the panel's), and the canopy workspace labs
  learns from canopy at Labs Admin › Canopy agents (`conf.workspace`);
- `CANOPY_HOST["REPLY_URL"]` = `{LABS_PUBLIC_URL}{CANOPY_MESSAGING_REPLY_WEBHOOK_PATH}`
  (`https://labs.connect.dimagi.com/messaging/canopy/replies/`), the `aud` every reply is verified
  against;
- in canopy: `connect-labs` may offer `connect-helper` and `connect-coach` (canopy-web
  `tokens/0048`), and each agent's host-site entry for `connect-labs` serves the `workers` surface
  (`contact_scoped: true`, `surface: workers`), set by the agents' admin with
  `put_agent_host_site`; each agent's contact access line names that entry.

To go live, in this order:

1. **Deploy labs** with this code, then check `https://labs.connect.dimagi.com/labs/canopy/site.json`
   declares `surfaces.workers` (canopy's Host sites page shows it under _What this site asks of
   agents_, with the two agents serving it).
2. **Mint budget.** Labs mints one contact token per Connect Worker and agent (cached until 5 minutes
   before it expires). canopy allows each site 120 mints a minute by default
   (`CONTACT_TOKEN_ISSUER_LIMIT`); over it, a send is refused `rate_limited` and labs retries it a
   minute later, then hands the thread to a person. If a program messages many workers at once, ask a
   canopy superuser to raise the site's `contact_token_issuer_limit` (Host sites, superuser only; `0`
   puts it back to the default). Separately, each worker may send 6 messages a minute and 200 a day to
   the agent (per contact, not per site).
3. **Set the reply webhook in canopy**: a `connect` workspace owner opens canopy at `https://canopy.dimagi.com/w/connect/settings/host-sites` ›
   `connect-labs`, enters `https://labs.connect.dimagi.com/messaging/canopy/replies/` as
   _Reply webhook URL_ with _Reply mode_ `final`, and saves (canopy asks for their passkey: the URL
   receives the workers' conversations). Then **turn on the reply push in labs**:
   `CANOPY_MESSAGING_REPLY_WEBHOOK=True` (the poll still runs as a safety net, and answers sensitive
   conversations, which canopy never pushes). Optionally `CANOPY_CONTACT_RETENTION_DAYS` (1-3650;
   published as `site.retention_days`).
4. Flip `MESSAGING_ENABLED` (above), then author the program's playbooks (ACE's
   `messaging-playbook-setup`) and turn the program on.

## Simulating a conversation (staff, test workers only)

`manage.py messaging_simulate --username <test worker> --opportunity-id <labs-only opp>` runs one
conversation through the real path (router → inbound playbook → handler → canopy relay → agent →
reply) on the **recorder transport** (`transport/simulated.py`, `TransportKind.SIMULATED`): the
reply is stored and marked sent, and no call reaches PersonalID. It needs no connect-id
registration, so it tests the canopy half before PersonalID is wired. On prod, run it through
`run-labs-command.yml` (with a `recipient`, so the transcript is sealed).

- It refuses any username not in `MESSAGING_SIMULATION_WORKERS` (`+7426` demo-number test users)
  and any opportunity that is not a labs-only synthetic one, so the program is a test program.
- The program needs `messaging.settings.enabled` and an inbound playbook (e.g. `general-help`,
  handler `{kind: agent, agent: connect-helper, skill: general-help}`).
- A test worker on a simulated channel stays on it (`service.address_for` prefers it), and the
  recorder refuses to carry for anyone not in the setting, whichever process delivers.
- It prints the transcript and checks: an agent reply in the thread, the brief stored
  (`brief_hash`), the worker's canopy contact, and a worker tool called with subject auth
  (`MCPAuditLog`, `execution.mode = subject`). `--report <thread>` re-reads a thread.

## Tests

`tests/fake_connectid.py` is a fake connect-id (channels, `create_message` with its error codes,
`userinfo`, and a refusal if anything ever calls `send_fcm`), plus helpers that post to labs as
connect-id does, signed with the shared secret. Use the `fake_connectid`, `messaging_on` and
`run_now` fixtures in `tests/conftest.py`.
