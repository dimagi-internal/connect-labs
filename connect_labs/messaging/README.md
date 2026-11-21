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

## Settings

| Setting                              | Default                        | Where it comes from                           |
| ------------------------------------ | ------------------------------ | --------------------------------------------- |
| `MESSAGING_ENABLED`                  | `False`                        | env (`environment[]` in the task definitions) |
| `PERSONALID_SERVER_URL`              | `https://connectid.dimagi.com` | env                                           |
| `PERSONALID_MESSAGING_CLIENT_ID`     | empty                          | Secrets Manager                               |
| `PERSONALID_MESSAGING_CLIENT_SECRET` | empty                          | Secrets Manager                               |
| `MESSAGING_DEBOUNCE_SECONDS`         | `8`                            | env (optional)                                |

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

## Tests

`tests/fake_connectid.py` is a fake connect-id (channels, `create_message` with its error codes,
`userinfo`, and a refusal if anything ever calls `send_fcm`), plus helpers that post to labs as
connect-id does, signed with the shared secret. Use the `fake_connectid`, `messaging_on` and
`run_now` fixtures in `tests/conftest.py`.
