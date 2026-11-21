# Canopy agents in Labs Messaging (the canopy bridge)

A Connect Worker's PersonalID conversation can be answered by a canopy agent that knows the
worker. **Labs stays the system of record**: canopy holds an anchor (the contact, keyed
`personalid:<username>`), a pointer (`labs-worker://<username>`) and a small profile cache labs
keeps current; everything else stays in labs, and the agent reads more through tools bound to
that one worker.

Contracts: `CONTRACTS.md` S1-S6 (canopy SDK `CONTRACT_VERSION "1"`). Design:
`docs/superpowers/specs/2026-11-21-connect-worker-messaging-design.md`.

## The `connect-messaging` host site

A second canopy host site, separate from `connect-labs` (the agent panel's): its own name, its
own Ed25519 key, its own documents and routes, all under `/messaging/canopy/`:

| Route                         | What                                                            |
| ----------------------------- | --------------------------------------------------------------- |
| `/messaging/canopy/jwks.json` | the site's public key                                           |
| `/messaging/canopy/site.json` | the site's signed facts, with `site.person_scheme: labs-worker` |

Each answers 404 until the site is configured.

## Settings

Everything is OFF unless `MESSAGING_ENABLED` and all of these are set (`conf.problems()` says
what is missing):

| Setting                                 | Default                      |                                                      |
| --------------------------------------- | ---------------------------- | ---------------------------------------------------- |
| `CANOPY_MESSAGING_SIGNING_KEY`          | empty (off)                  | PEM, Ed25519, Secrets Manager; never the panel's key |
| `CANOPY_MESSAGING_SITE_NAME`            | `connect-messaging`          | the host-site name in canopy                         |
| `CANOPY_MESSAGING_WORKSPACE`            | empty (off)                  | the canopy workspace the site is registered in       |
| `CANOPY_MESSAGING_REPLY_WEBHOOK_PATH`   | `/messaging/canopy/replies/` | where canopy pushes replies (S5)                     |
| `CANOPY_MESSAGING_REPLY_WEBHOOK`        | `False`                      | on: canopy pushes replies; off: labs polls           |
| `CANOPY_MESSAGING_TURN_TIMEOUT_SECONDS` | `600`                        | an agent turn longer than this goes to a person      |
| `CANOPY_BASE_URL`, `LABS_PUBLIC_URL`    | (shared with the panel)      |                                                      |

```bash
openssl genpkey -algorithm ed25519 -out canopy-messaging-signing.pem
```

## The profile canopy caches (S2)

For every Connect Worker labs messages (one with a channel), labs pushes
`{sub, display_name, record_uri, profile: {stage, program, opportunity, language, consent,
timezone}, version}` to canopy's `POST /api/sites/contacts`: when their first channel is made,
when a `state_changed` / `profile_changed` / `consent_changed` event lands on their timeline (the
sweep reads events past a cursor), and before their first canopy session. Only a changed profile
is sent, at most 100 per request; `ConnectWorker.canopy_profile_version` is raised with each.
`ProfilePush` is labs' record of the last push.

## The canopy SDK compat module

`sdk.py` is the one place the bridge reaches canopy's new SDK functions (`RelayClient`,
`sign_context`, `update_contacts`, `erase_contacts`, `verify_subject`, `verify_reply`, and
`site.json`'s `person_scheme`). Labs pins `dimagi-canopy` 0.29.0, which has none of them, so each
is a LOCAL FALLBACK of exactly the CONTRACTS.md wire format until the SDK ships it;
`sdk.implementations()` says which half each name is. `tests/test_sdk_wire.py` pins the format.

**To remove the fallbacks**: bump `dimagi-canopy` in `requirements/base.txt` to the release with
S1-S6, check `implementations()` reports `"sdk"` for every name, delete each block marked
`LOCAL FALLBACK`, and keep the wire tests.
