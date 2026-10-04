# Labs MCP Setup

The labs MCP server is a standard remote MCP server at:

    https://labs.connect.dimagi.com/mcp/

(Streamable HTTP.) Any MCP client can connect to it. Tools run as you, with
your Connect permissions.

## Two endpoints

| URL | What it reaches |
|---|---|
| `https://labs.connect.dimagi.com/mcp/` | Every tool, as you. |
| `https://labs.connect.dimagi.com/mcp/no_user_visit/` | The same tools **without access to real user visit data** — the one to give a whole team by default. |

The second is the same server behind a different URL, and it can only narrow
what you reach, never widen it: whatever credential you connect with, a request
on it gets the restricted tools. It can read how things are built (workflow,
pipeline and indicator definitions, app structure, solicitations, the org
directory, targeting), edit workflow and indicator definitions and trigger
saved-run generation (snapshots, history rebuilds, cache warms, benchmark
publication -- the server computes over visits, the caller gets back only ids,
versions, dates and counts), profile a real opportunity server-side and generate a
synthetic set from the profile. Tools that read visits (pipeline previews,
workflow runs, record dumps) run there only on synthetic opportunities whose
data was **generated** (`connect_labs/labs/synthetic/provenance.py`). The
profile is aggregate statistics; the visits themselves never reach the AI.

Signing in through it asks for the `mcp:no-uservisit-data` scope, so that
token is restricted even if it is later used against `/mcp/`. A
`no-uservisit-data` Personal Access Token is the same thing: the address and the
token resolve to exactly the same tools. The lists are in
`connect_labs/mcp/token_scopes.py`, deny-by-default: a new tool reaches neither
until someone adds it on purpose.

## Connect — people

1. Add `https://labs.connect.dimagi.com/mcp/` to your MCP client as a remote
   (HTTP) server.
2. When the client says the server needs authentication, sign in. It opens the
   labs login in your browser; sign in with Connect and approve the client.

That is the whole setup. The client does the rest by following the MCP
authorization spec: it finds the sign-in from the server's 401, registers
itself, and runs an OAuth 2.1 authorization-code flow with PKCE.

For example, in Claude Code:

    claude mcp add --transport http connect_labs https://labs.connect.dimagi.com/mcp/

then run `/mcp`, pick `connect_labs` and authenticate.

To see or disconnect the apps you have signed in, visit
`https://labs.connect.dimagi.com/labs/mcp/tokens/` — the same page that manages
Personal Access Tokens. Use that rather than the OAuth toolkit's own
`/o/authorized_tokens/` page: that one deletes the access token but leaves the
refresh token, so the app signs itself back in.

## Connect — scripts and headless agents

A process with no browser uses a Personal Access Token (PAT) instead:

1. Create one at `https://labs.connect.dimagi.com/labs/mcp/tokens/` (create,
   rotate, revoke). The raw token is shown once — copy it then. Pick the access
   level there:
   - **Full access** reaches every tool, as you.
   - **No user visit data** is restricted the same way as the
     `/mcp/no_user_visit/` endpoint above, on either URL. Rotating keeps the
     access level.
2. Send it as `Authorization: Bearer <token>`. For a client that takes a JSON
   config:

       {
         "mcpServers": {
           "connect_labs": {
             "type": "http",
             "url": "https://labs.connect.dimagi.com/mcp/",
             "headers": {
               "Authorization": "Bearer <your-raw-token>"
             }
           }
         }
       }

   In Claude Code, add it with
   `claude mcp add --transport http connect_labs https://labs.connect.dimagi.com/mcp/ --header "Authorization: Bearer <token>"`
   (user scope lives in `~/.claude.json`, project scope in `.mcp.json`;
   `~/.claude/mcp.json` is not read). Or run `/labs-token-setup` from a
   connect-labs checkout, which mints the token and registers the server.

## How the sign-in works

- An unauthenticated request to `/mcp/` gets a 401 whose `WWW-Authenticate`
  names `/.well-known/oauth-protected-resource/mcp` (RFC 9728).
- That document names labs as the authorization server; its metadata is at
  `/.well-known/oauth-authorization-server` (RFC 8414).
- The client registers at `/o/register/` (RFC 7591) and signs the user in
  through `/o/authorize/` and `/o/token/` — labs' existing OAuth server
  (django-oauth-toolkit), whose login is the labs login.
- The token it gets carries only the `mcp` scope, and MCP clients can hold no
  other. Tokens for labs' other OAuth APIs are refused by the MCP server, and an
  MCP token is refused by labs' REST API — the separation holds both ways.
- Clients register themselves, so the name on the consent screen is whatever the
  app called itself. The screen says so and shows where the sign-in will be
  sent; approve only an app you just started connecting.
- The token is bound to this server by scope and by the registered client, not
  by an audience (`resource`) claim. Labs has one MCP resource today, so there
  is nowhere else such a token could be replayed.

Implementation: `connect_labs/mcp/oauth.py` (and the discovery routes in
`config/asgi.py`).

## Troubleshooting

**401 Unauthorized** — the body says which failure it was: no
`Authorization` header at all (a PAT header helper that fails silently sends
none), or a token that is unknown, expired or revoked. Sign in again, or
rotate the PAT.

**Tools fail with "No Connect OAuth token stored"** — tools act on Connect as
you, so labs needs your Connect login. Sign in at
`https://labs.connect.dimagi.com/labs/login/` once in a browser.

**A headless agent's PAT expired and its client offered to sign in** — that is
the browser flow taking over, and whoever is signed in to that browser is who
the agent would then act as. For an agent with its own identity, rotate the PAT
rather than signing in: `https://labs.connect.dimagi.com/labs/mcp/tokens/`.

**Cannot connect** — confirm the URL. Some corporate networks block labs; try
from a non-corp network to isolate.

**Unexpected tool failures** — check
`https://labs.connect.dimagi.com/admin/mcp/mcpauditlog/` (if you have admin
access). Every tool call is logged with the error code.

## Token hygiene

- Treat a PAT like a password. Store it in a password manager or your OS
  keychain. Do not commit it to git.
- PATs default to 90 days and never exceed 365.
- Sign-in access tokens last two weeks and the client refreshes them
  indefinitely; disconnect an app at `/labs/mcp/tokens/` to end that.
- Admins can revoke any PAT in the Django admin at
  `/admin/mcp/mcpaccesstoken/`.
