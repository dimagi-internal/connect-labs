# The site agents API: canopy and a connected site describe themselves to each other

Status: built. canopy-web#1902 (+#1906, #1907) and dimagi-canopy 0.14.0; labs as described
in §3, with the two choices under "As built" below.

## The problem

Labs offers canopy agents on its pages and lets some of them read real visit data. Every
fact labs uses to decide that is a copy it keeps by hand of something canopy owns:

| Labs keeps                                 | Canopy owns                                                                        |
| ------------------------------------------ | ---------------------------------------------------------------------------------- |
| agent slugs typed into text fields         | which agents exist and are allowed on the connect-labs site                        |
| a "data agent" role, offered read-only     | what each agent's capability does on this site                                     |
| a list of agents "allowed over real data"  | whether the agent handles sensitive data (`data: sensitive`, set behind a passkey) |
| `KNOWN_RUNNER_FLAGS = ("sensitive_data",)` | the runner flags canopy knows, and which runners an agent can use                  |
| nothing                                    | whether the agent is ready (GitHub token, a ready runner, ...)                     |

Nothing checks the copies against the originals until a visitor's call reaches labs with
canopy's `Canopy-Execution` statement, and a mismatch then shows as an agent that silently
does not appear. On 2026-10-22 the owner set `labsdata` up on every canopy screen, left one
labs text field empty, and the KMC report drew no panel with no word as to why.

## The design

Each side describes itself through a signed API -- canopy its agents for the site, the
site what it needs -- and the other side's
settings are CHOSEN from that, never typed. The per-call `Canopy-Execution` statement
stays: it is still the final check that this conversation is that agent on that runner.
It stops being the first place a mismatch shows.

No new secrets. The two trust anchors already exist:

- canopy verifies the site's signatures through the site's `jwks_url` (`AppCredential`),
  which is how it verifies every visitor assertion today;
- the site verifies canopy's signatures through canopy's client document
  (`https://canopy.dimagi.com/oauth/client.json`), which is how labs verifies
  `Canopy-Execution` statements today.

### 1. The site agents API: canopy → site

`GET {canopy}/api/sites/agents`

**Request.** `Authorization: Bearer <site assertion>`: a JWT signed with the site's key
(the one at its `jwks_url`): `iss` = the site's name (`AppCredential.name`, e.g.
`connect-labs`), `aud` = the endpoint's URL, `iat`/`exp` (lifetime at most 60 s), `jti`.
canopy resolves the site by `iss` (`embed_apps.resolve_site`), verifies against its keys,
and refuses (401) anything else. The SDK builds it: `canopy_sdk.site_agents.request_assertion`.

**Response.** `application/jose`: a compact JWS signed with canopy's client key (the
`Canopy-Execution` key, its `kid` in `client.json`). Payload:

```json
{
  "v": 1,
  "iss": "<canopy client id>",
  "aud": "connect-labs",
  "iat": 1792700000,
  "exp": 1792700900,
  "version": "<sha256 of the canonical agents+flags content>",
  "site": { "name": "connect-labs", "tenant": "connect" },
  "runner_flags": [
    { "flag": "sensitive_data", "meaning": "a sensitive-data runner: ..." }
  ],
  "agents": [
    {
      "slug": "labsdata",
      "name": "labsdata",
      "description": "...",
      "workspace": "connect",
      "owner": "a person's name",
      "url": "https://canopy.dimagi.com/w/connect/agents/labsdata",
      "capabilities": [
        {
          "name": "data",
          "description": "Ask data questions about the workflow you are looking at ...",
          "callers": ["contact", "member"],
          "handles_sensitive": true,
          "sensitive_set_by": "a person's name",
          "sensitive_set_at": "2026-10-22T22:00:09Z"
        }
      ],
      "runners": {
        "flags_available": ["sensitive_data"],
        "ready_with": { "sensitive_data": true }
      },
      "ready": true,
      "problems": []
    }
  ]
}
```

- `agents`: exactly the agents the site's tenant allows on this site
  (`AppCredentialAgent`) AND that have a capability naming this site in `sites:`. An
  agent allowed but with no capability for the site is listed with `ready: false` and a
  problem saying so.
- `handles_sensitive`: the capability has `data: sensitive` (canopy-web#1887's interface setting).
- `runners.flags_available`: flags carried by at least one runner the agent may run on;
  `ready_with[flag]`: one of those runners is ready now.
- `problems`: plain sentences an admin can act on, each naming where to fix it in canopy
  (`"no ready runner with sensitive_data: canopy › Runners"`, `"no GitHub token: …/settings/credentials"`).
- `version`: changes exactly when anything above (bar `iat`/`exp`) changes.

**SDK 0.14** (`canopy_sdk.site_agents`):

- `fetch_site_agents(canopy_url, site_name, site_key, canopy_keys) -> SiteAgents`: builds the
  assertion, GETs through `canopy_sdk.fetch` (pinned, bounded, no redirects), verifies
  signature, `aud`, `exp`, `v`; raises `SiteAgentsError` otherwise.
- `SiteAgents` / `SiteAgent` / `AgentCapability` dataclasses mirroring the payload, with
  `SiteAgents.agent(slug)`.
- `canopy_sdk.django.site_agents.current() -> SiteAgents | None`: cached in the host's cache for 5
  minutes, refreshed on miss; on a failed refresh it serves the last good response marked
  `stale=True` (and its age) for up to 24 h, then None. Hosts treat None as "show
  no agent" (fail closed).

### 2. The needs: site → canopy

The site publishes what it asks of agents, so canopy's Connected sites page can say, per
allowed agent, whether it meets them — before anyone opens a page.

`GET {site}/canopy/site.json` (served by the SDK's host URLs beside `jwks.json`): a JWS
signed with the site's key. Payload:

```json
{
  "v": 1,
  "iss": "connect-labs",
  "pages": [
    {
      "key": "labs:workflow:run",
      "title": "A workflow run",
      "scopes": ["workflow:read", "workflow:act", "data:read"]
    },
    {
      "key": "labs:workflow:run:data",
      "title": "A workflow run, read-only",
      "scopes": ["workflow:read", "data:read"]
    }
  ],
  "real_data": {
    "requires": { "handles_sensitive": true, "runner_flag": "sensitive_data" }
  },
  "uses": {
    "agents": ["ace", "labsdata"],
    "real_data_agents": ["labsdata"],
    "version": "<site agents version chosen against>"
  }
}
```

canopy fetches it like `jwks_url` (https only, bounded, cached, fail-closed) and shows on
the site's Connected sites page: the site's pages and scopes; which allowed agents the
site actually offers and lets read real data; and for each, whether it meets the site's
`real_data.requires` (handles sensitive data, ready on a runner with the flag) and, if not, why.

### 3. Labs: settings chosen from the site agents API

**Labs Admin › Canopy agents** becomes a table built from the site agents API response, one row
per agent canopy lists:

| Agent | What it does here | Handles sensitive data | Sensitive-data runner | Ready | Show on labs | Real data | Default |
| ----- | ----------------- | ---------------------- | --------------------- | ----- | ------------ | --------- | ------- |

- "Show on labs" and "Real data" are ticks; "Default" is one radio. No free text.
- "Real data" can be ticked only when canopy says the agent handles sensitive data AND is
  ready with `sensitive_data`; otherwise it is disabled with canopy's reason and a link to fix it there.
- Ticking "Real data" implies "Show on labs". An agent offered over real data is always
  read-only on sensitive-data runners (the `labs:workflow:run:data` page key) — no separate "data agent" role.
- Runner flags come from the response's `runner_flags`, not a constant.
- Each save records the response's `version` it was made against (history, audit metadata).
- Drift: an agent ticked here that the current response no longer lists, or no longer lists
  as handling sensitive data/ready, is shown in red with canopy's reason, and labs stops offering it at
  once — the page, the run pages and the Data page all read the same answer.

The model (`CanopyAgentSettings`, append-only, history + restore as today) becomes
`agents: [{slug, offered, real_data}]`, `default_agent`, `runner_requirements`,
`site_agents_version`. A data migration maps each existing row: `site_agent` → default and
offered; `data_agent` → offered; `phi_agents` → real_data; `offered_agents` → offered.
The env defaults stay as the cold-start defaults.

**One answer to "may this agent be offered here, and if not, why".**
`canopy_agents.availability(slug, reads) -> Availability(ok, read_only, reasons)`
combines labs' ticks, canopy's site agents response, and what the page reads. Everything uses it:

- the panel decision (`canopy_panel.panel`) — no panel when not ok;
- the run page's **Agent** box: every agent labs offers is listed; one not available on
  this workflow is greyed with its reasons ("not declared to handle sensitive data in canopy", "not allowed over
  real data in Labs Admin"), and an available one is pre-selected;
- a line beside the Agent box for an editor when the chosen agent is held back:
  "labsdata is not shown: <reason> — <where to fix>". Never silent.
- the Labs Admin table's per-row state.

**Workflows** keep `config.agent.agent`, now always a slug (legacy `"site"`/`"data"` read
as the default agent / the first real-data agent).

### 4. What stays

- The per-call `Canopy-Execution` statement and `call_real_data_clearance`: unchanged.
  Labs' side of that check now reads the same ticks as everything else.
- Only Dimagi staff change the settings; every change attributed, audited, restorable.
- canopy's connected-site allow-list (`AppCredentialAgent`) stays canopy's; labs cannot
  offer an agent canopy has not allowed on the site.

## Order of work

1. **canopy-web**: `GET /api/sites/agents`; SDK 0.14 (`canopy_sdk.site_agents`,
   `canopy_sdk.django.site_agents`, `site.json` host route); Connected sites page shows the
   site's needs and per-agent fit. Release `dimagi-canopy` 0.14.0.
2. **labs**: bump to 0.14; `canopy_agents.availability`; the model + data migration; the
   Labs Admin table; the Agent box and the held-back line; publish `site.json` (the SDK
   route, fed by `PAGE_SCOPES` and the settings).

The "never silent" line and greyed choices (step 2's run-page half) need only labs' own
ticks and can land before the site agents API does.

## As built (labs)

- **An agent that handles sensitive data is always read-only.** §3 made an agent read-only only when "Real
  data" is ticked. Labs also offers an agent read-only, on sensitive-data runners, whenever
  canopy says its author declared it handles sensitive data, so `labsdata` never gains `workflow:act` on a
  synthetic workflow just because "Real data" is not ticked yet. This replaces the old
  "data agent" role's guarantee, from canopy's word rather than a typed role.
- **A call is held to its statement, not to the cached listing.** The page decision reads
  the site agents API (so drift withdraws an agent at once); a delegated call reads labs'
  ticks and its own verified `Canopy-Execution` statement, which is canopy's word on
  exactly that call, so a cache miss at canopy cannot refuse or admit a call by itself.
- **The Data page** offers "the agent for data work": the first agent with "Real data"
  ticked, else the first shown agent canopy declares handles sensitive data, else the default agent.
