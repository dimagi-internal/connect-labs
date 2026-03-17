# CommCare Connect Labs

A rapid prototyping environment for CommCare Connect experiments. Labs operates entirely via API against production CommCare Connect — there is no direct database access to production data.

**AI agents:** Start with [CLAUDE.md](CLAUDE.md) — it's auto-loaded in Claude Code and provides the full architecture overview, app map, and links to deeper docs.

## Quick Start

```bash
# Create and activate a Python 3.11 virtual environment
python3.11 -m venv venv && source venv/bin/activate

# Install requirements
pip install -r requirements-dev.txt

# Install git hooks
pre-commit install

# Copy env template and configure
cp .env_template .env

# Start services (PostgreSQL, Redis)
inv up

# Install JS deps and build frontend
npm ci && inv build-js

# Run migrations and start server
python manage.py migrate
python manage.py runserver
```

**Important:** Use `config.settings.local` (the default) for local development, NOT `config.settings.labs_aws`. The `labs_aws` settings are only for the AWS deployment at `labs.connect.dimagi.com`.

## Labs Apps

| App              | Purpose                                            | Docs                                                                       |
| ---------------- | -------------------------------------------------- | -------------------------------------------------------------------------- |
| `labs/`          | Core infrastructure: OAuth, API client, middleware | [LABS_GUIDE.md](commcare_connect/labs/LABS_GUIDE.md)                       |
| `audit/`         | Quality assurance review of FLW visits             | [README](commcare_connect/audit/README.md)                                 |
| `tasks/`         | Task management for FLW follow-ups                 | [README](commcare_connect/tasks/README.md)                                 |
| `workflow/`      | Configurable workflow engine with React UIs        | [README](commcare_connect/workflow/README.md)                              |
| `ai/`            | AI agent integration via pydantic-ai               | [README](commcare_connect/ai/README.md)                                    |
| `solicitations/` | RFP management scoped by program                   | [README](commcare_connect/solicitations/README.md)                         |
| `coverage/`      | Delivery unit mapping from CommCare HQ             | [Commands README](commcare_connect/coverage/management/commands/README.md) |

## Documentation Map

- **[CLAUDE.md](CLAUDE.md)** — Architecture overview, app map, critical warnings (auto-loaded by Claude Code)
- **[.claude/AGENTS.md](.claude/AGENTS.md)** — Full per-app architecture reference, API endpoints, common mistakes
- **[CONTRIBUTING.md](CONTRIBUTING.md)** — Code style, data_access.py pattern, how to add a new feature
- **[LABS_GUIDE.md](commcare_connect/labs/LABS_GUIDE.md)** — OAuth setup, API client usage, proxy model patterns
- **[LABS_ARCHITECTURE.md](docs/LABS_ARCHITECTURE.md)** — Architecture diagrams, data flow, decision tree
- **[PR Guidelines](pr_guidelines.md)** — Pull request best practices

## Key Commands

```bash
inv up                              # Start docker services
npm ci && inv build-js              # Build frontend
inv build-js -w                     # Watch mode
python manage.py runserver          # Dev server
pytest                              # Run tests
celery -A config.celery_app worker -l info   # Celery worker
pre-commit run --all-files          # Linters/formatters
```

## Setting up Auth

### Labs OAuth (for web UI)

Access labs features at `http://localhost:8000/labs/login/` — this initiates OAuth against production CommCare Connect.

### CLI OAuth (for scripts)

```bash
python manage.py get_cli_token
```

### CommCare HQ OAuth (for coverage app)

See [coverage commands README](commcare_connect/coverage/management/commands/README.md).

### AI Integrations

Add API keys to your `.env` file:

```
OPENAI_API_KEY=sk...
ANTHROPIC_API_KEY=sk-ant-...
```

## Retained Non-Labs Apps

Most production apps have been removed. The remaining non-labs apps (`opportunity/`, `organization/`, `program/`, `users/`, `commcarehq/`) are kept only for their Django models and migrations (needed by foreign key references). Their tables are empty in this environment — do not modify them for labs features. See [docs/upstream-reference.md](docs/upstream-reference.md) for details on what was removed.

## Deployment

- **Labs:** Use `/deploy-labs` skill or `gh workflow run "Deploy to AWS Labs"` — see [deploy skill](.claude/skills/deploy-labs/SKILL.md)
