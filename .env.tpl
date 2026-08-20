# Local dev config. Generate the real .env with:
#     op inject -i .env.tpl -o .env
#
# WARNING: op inject resolves secret references ANYWHERE in this file, including
# inside comments, and ONE unresolvable reference aborts the entire injection --
# no .env is written and the only clue is a terse "invalid secret reference".
# So never write a bare reference in prose here; name the vault and item in words.

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

# Safe-mode Claude Code needs NOTHING in .env — auth mode is a required
# CLI flag on `inv safe-claude --auth=...`. See docs/SAFE_MODE.md.

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

# Mapbox (satellite basemap for microplanning / rooftop-surveys maps; public pk. token)
MAPBOX_TOKEN={{ op://Employee/Connect Labs .env/MAPBOX_TOKEN }}

# Superset
SUPERSET_URL={{ op://Employee/Connect Labs .env/SUPERSET_URL }}
SUPERSET_USERNAME={{ op://Employee/Connect Labs .env/SUPERSET_USERNAME }}
SUPERSET_PASSWORD={{ op://Employee/Connect Labs .env/SUPERSET_PASSWORD }}

# Google Drive service account (connect-labs-sa) for synthetic-opp fixtures — DriveClient
# uses it to read profile bundles and upload generated fixtures during the two-phase clone.
# Lives in the AI-Agents vault item "connect-labs GCP service account key (connect-labs-sa)".
# Referenced by item ID, NOT title: the title's parentheses are illegal in a 1Password
# secret reference, so a title-based ref (op read / op inject) fails to resolve.
# NB: do not write a bare "op:" + "//" scheme in these comments -- op inject resolves
# such references ANYWHERE in the file, comments included, and an unresolvable one
# aborts the whole injection (see the header note).
# This field is the minified single-line JSON (op-inject-friendly); DriveClient
# json.loads it, or accepts a file path. Also mirrored in AWS Secrets Manager (labs-jj-synthetic-gdrive-sa-key) for the
# deployed labs environment.
LABS_SYNTHETIC_GDRIVE_SA_KEY={{ op://AI-Agents/swvkqixqoyprbtleply2p4hnta/LABS_SYNTHETIC_GDRIVE_SA_KEY }}

# Audit trail (HIPAA-bar logging — docs/AUDIT_LOGGING.md). Optional locally:
# unset AUDIT_TRAIL_ARCHIVE_BUCKET disables the nightly S3 archive task.
# AUDIT_TRAIL_ARCHIVE_BUCKET=labs-jj-audit-archive

# Self-hosted Umami analytics (first-party; empty = tracking disabled).
# Prod values live in the ECS task definitions, not 1Password.
# UMAMI_HOST_URL=https://labs.connect.dimagi.com/umami
# UMAMI_WEBSITE_ID=
