# Connect Labs Documentation Automation

Automated system that keeps user-facing documentation current without manual effort. Every merged PR can update the help site and Confluence; every Monday a changelog summary goes to Confluence and Slack.

## Overview

| Component | What it does | When it runs |
|---|---|---|
| GitHub Pages site | Rich HTML docs for non-developer program staff | Deployed on every push to `user_docs/` or `mkdocs.yml` |
| Per-merge doc updater | Updates the relevant help page and Confluence summary when a PR ships a user-visible change | On every push to `main` |
| Weekly changelog | Plain-English summary of the week's changes → Confluence + Slack | Mondays 09:00 UTC (+ manual trigger) |

## How it knows what changed

Every PR in this repo uses `.github/PULL_REQUEST_TEMPLATE.md`. The `## Product Description` section is the signal:

- **Filled in** → user-visible change; automation updates docs and changelog
- **Left blank** → infra/refactor; automation skips entirely (zero API calls, zero cost)

The per-merge updater maps changed file paths to feature areas:

```
commcare_connect/audit/          → Audit & QA Review
commcare_connect/workflow/       → Workflow Engine
commcare_connect/tasks/          → Task Management
commcare_connect/solicitations/  → Solicitations
commcare_connect/custom_analysis/→ Custom Analysis
commcare_connect/coverage/       → Coverage Maps
commcare_connect/ai/             → AI Features
docs/WORKFLOW_EDITOR_QUICKSTART.md
docs/SAFE_MODE.md                → Connect MCP & Safe Mode
```

## File map

```
automation/
  confluence_client.py   Confluence REST API v2 wrapper
  update_docs.py         Per-merge: triage → update markdown + Confluence summary
  weekly_changelog.py    Weekly: summarise PRs → Confluence row + Slack message
  bootstrap_docs.py      One-time seeder (already run; do not re-run)

user_docs/
  index.md               Landing page
  audit.md               Audit & QA Review
  workflow-engine.md     Workflow Engine
  task-management.md     Task Management
  solicitations.md       Solicitations
  custom-analysis.md     Custom Analysis
  coverage-maps.md       Coverage Maps
  ai-features.md         AI Features
  connect-mcp-safe-mode.md  Connect MCP & Safe Mode
  assets/screenshots/    Manually maintained screenshots

mkdocs.yml               MkDocs Material theme config (Mermaid.js via superfences)

.github/workflows/
  docs-deploy.yml        Deploys MkDocs → GitHub Pages (gh-pages branch)
  docs-update.yml        Per-merge doc updater
  weekly-changelog.yml   Weekly changelog cron + workflow_dispatch
```

## GitHub Actions secrets required

| Secret | Used by | Status |
|---|---|---|
| `ANTHROPIC_API_KEY` | `update_docs.py`, `weekly_changelog.py` | ✓ Set |
| `CONFLUENCE_API_TOKEN` | Both scripts | ✓ Set |
| `CONFLUENCE_EMAIL` | Both scripts (`connect-wiki-bot@dimagi.com`) | ✓ Set |
| `SLACK_WEBHOOK_URL` | `weekly_changelog.py` | Add when ready |

## Confluence pages

| Page | ID |
|---|---|
| Connect Labs Documentation (parent) | `3916103691` |
| Connect Labs Changelog | `3918528513` |
| Audit & QA Review | `3927900187` |
| Workflow Engine | `3927801864` |
| Task Management | `3928293395` |
| Solicitations | `3927179271` |
| Custom Analysis | `3928817669` |
| Coverage Maps | `3927867398` |
| AI Features | `3928817690` |
| Connect MCP & Safe Mode | `3927801885` |

## GitHub Pages

Site deploys to `https://dimagi-internal.github.io/connect-labs/docs/`. Enable in repo settings if not already live: Settings → Pages → Source: Deploy from branch → `gh-pages`.

## Model usage and cost

| Call | Model | When | Approx cost |
|---|---|---|---|
| User-visibility triage | Haiku | Per merge (skipped if no Product Description) | ~$0.001 |
| Markdown doc rewrite | Sonnet | Per merge, per affected feature | ~$0.01–0.05 |
| Confluence one-liner | Haiku | Per merge, per affected feature | ~$0.001 |
| Weekly summary | Haiku | Once per week | ~$0.002 |

All system prompts use `cache_control: ephemeral` to reduce repeat costs.

## Running manually

**Trigger weekly changelog now** (without waiting for Monday):
GitHub → Actions → Weekly Changelog → Run workflow

**Test per-merge updater** on any PR:
The `docs-update.yml` workflow fires automatically on every push to `main`. Check Actions for the run log.

**Re-run bootstrap** (only if Confluence pages are deleted and need recreating):
```bash
CONFLUENCE_EMAIL=... CONFLUENCE_API_TOKEN=... python automation/bootstrap_docs.py
```
Then update `FEATURE_PAGE_IDS` in `automation/update_docs.py` with the new IDs.

## Screenshots

Screenshots in `user_docs/assets/screenshots/` are maintained manually. When a PR's `## Product Description` contains UI-change keywords (button, screen, dashboard, etc.), the `docs-update.yml` workflow posts a comment on the PR flagging which doc pages need updated screenshots.

## Linter notes

- `check-yaml` uses `--unsafe` to allow `mkdocs.yml`'s `!!python/name:` tags — this is intentional
- `automation/` imports use `# noqa: E402` on the post-`sys.path.insert` local import
- Run prettier with `--single-quote --tab-width 2` to match the pre-commit hook version
