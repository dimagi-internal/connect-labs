DATABASE_URL={{ op://Employee/Connect Labs .env/DATABASE_URL }}
CELERY_BROKER_URL={{ op://Employee/Connect Labs .env/CELERY_BROKER_URL }}
REDIS_URL={{ op://Employee/Connect Labs .env/REDIS_URL }}

# Connect Production OAuth
CONNECT_OAUTH_CLIENT_ID={{ op://Employee/Connect Labs .env/CONNECT_OAUTH_CLIENT_ID }}
CONNECT_OAUTH_CLIENT_SECRET={{ op://Employee/Connect Labs .env/CONNECT_OAUTH_CLIENT_SECRET }}
CLI_OAUTH_CLIENT_ID={{ op://Employee/Connect Labs .env/CLI_OAUTH_CLIENT_ID }}

# CommCare HQ
COMMCARE_HQ_URL={{ op://Employee/Connect Labs .env/COMMCARE_HQ_URL }}
COMMCARE_API_KEY={{ op://Employee/Connect Labs .env/COMMCARE_API_KEY }}
COMMCARE_USERNAME={{ op://Employee/Connect Labs .env/COMMCARE_USERNAME }}
COMMCARE_OAUTH_CLIENT_ID={{ op://Employee/Connect Labs .env/COMMCARE_OAUTH_CLIENT_ID }}
COMMCARE_OAUTH_CLIENT_SECRET={{ op://Employee/Connect Labs .env/COMMCARE_OAUTH_CLIENT_SECRET }}
COMMCARE_OAUTH_CLI_CLIENT_ID={{ op://Employee/Connect Labs .env/COMMCARE_OAUTH_CLI_CLIENT_ID }}

# Open Chat Studio
OCS_URL={{ op://Employee/Connect Labs .env/OCS_URL }}
OCS_OAUTH_CLIENT_ID={{ op://Employee/Connect Labs .env/OCS_OAUTH_CLIENT_ID }}
OCS_OAUTH_CLIENT_SECRET={{ op://Employee/Connect Labs .env/OCS_OAUTH_CLIENT_SECRET }}
OCS_API_KEY={{ op://Employee/Connect Labs .env/OCS_API_KEY }}

# AI API Keys
OPENAI_API_KEY={{ op://Employee/Connect Labs .env/OPENAI_API_KEY }}
ANTHROPIC_API_KEY={{ op://Employee/Connect Labs .env/ANTHROPIC_API_KEY }}

# ─── Safe-mode Claude Code auth mode ─────────────────────────────────────
# `vertex`  — route through Google Vertex AI (GCP-governed). Preferred
#             once Vertex quota is available on `connect-labs`.
# `api_key` — route through an Anthropic ZDR API key. The key is fetched
#             fresh from 1Password on every `inv safe-claude` launch
#             (item "Connect Labs Safe-Claude ZDR Anthropic API Key",
#             vault AI-Agents) — NEVER read from .env or a local file.
#
# Default in code is `vertex`. While Vertex quota on `connect-labs` is
# still zero, we route through api_key; flip this line (or delete it to
# return to the default) once Vertex is serving traffic.
SAFE_CLAUDE_AUTH=api_key
# ────────────────────────────────────────────────────────────────────────

# PAT for `inv safe-claude` (locked-down Claude Code against labs MCP).
# Generate via the `/labs-token-setup` skill in Claude Code (recommended) —
# that writes it to ~/.claude.json and `inv safe-claude` picks it up
# automatically. Alternatively, set LABS_MCP_TOKEN here manually (not via
# op-inject, to avoid coupling to a 1Password field).

# Scale Image Validation (KMC)
SCALE_VALIDATION_API_URL={{ op://Employee/Connect Labs .env/SCALE_VALIDATION_API_URL }}
SCALE_VALIDATION_API_KEY={{ op://Employee/Connect Labs .env/SCALE_VALIDATION_API_KEY }}

# Google OAuth (for Sheets/Drive MCP tools)
GOOGLE_OAUTH_CLIENT_ID={{ op://Employee/Connect Labs .env/GOOGLE_OAUTH_CLIENT_ID }}
GOOGLE_OAUTH_CLIENT_SECRET={{ op://Employee/Connect Labs .env/GOOGLE_OAUTH_CLIENT_SECRET }}

# Superset
SUPERSET_URL={{ op://Employee/Connect Labs .env/SUPERSET_URL }}
SUPERSET_USERNAME={{ op://Employee/Connect Labs .env/SUPERSET_USERNAME }}
SUPERSET_PASSWORD={{ op://Employee/Connect Labs .env/SUPERSET_PASSWORD }}
