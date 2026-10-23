# Connect Labs Documentation Automation

Automated system that keeps user-facing documentation current without manual effort. Every merged PR can update the help site and Confluence; every Monday a changelog summary goes to Confluence and Slack.

## Overview

| Component             | What it does                                                                                | When it runs                              |
| --------------------- | ------------------------------------------------------------------------------------------- | ----------------------------------------- |
| Labs help site        | Rich HTML docs for non-developer program staff, at `/labs/docs/help/` (login required)      | Built into the Labs image on every deploy |
| Per-merge doc updater | Updates the relevant help page and Confluence summary when a PR ships a user-visible change | On every push to `main`                   |
| Weekly changelog      | Plain-English summary of the week's changes → Confluence + Slack                            | Mondays 09:00 UTC (+ manual trigger)      |

## How it knows what changed

Every PR in this repo uses `.github/PULL_REQUEST_TEMPLATE.md`. The `## Product Description` section is the signal:

- **Filled in** → user-visible change; automation updates docs and changelog
- **Left blank** → infra/refactor; automation skips entirely (zero API calls, zero cost)

The per-merge updater maps changed file paths to feature areas:

```
connect_labs/audit/          → Audit & QA Review
connect_labs/workflow/       → Workflow Engine
connect_labs/tasks/          → Task Management
connect_labs/solicitations/  → Solicitations
connect_labs/custom_analysis/→ Custom Analysis
connect_labs/coverage/       → Coverage Maps
connect_labs/microplans/simple_*, static/ + templates/microplans/simple_*
                             → Simple Microplan (help site + the editor's help popup; no Confluence page)
connect_labs/ai/             → AI Features
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
  regenerate_confluence.py  Rebuild every Confluence summary page from user_docs/ (manual)
  tests/                 Tests for the scripts above

user_docs/
  index.md               Landing page
  audit.md               Audit & QA Review
  workflow-engine.md     Workflow Engine
  task-management.md     Task Management
  solicitations.md       Solicitations
  custom-analysis.md     Custom Analysis
  coverage-maps.md       Coverage Maps
  simple-microplan.md    Simple Microplan (also rendered as the editor's in-page help)
  ai-features.md         AI Features
  connect-mcp.md         Connect MCP & Safe Mode
  connect-safe-mode.md   (linked from connect-mcp.md; not auto-updated)
  reports-with-claude.md (not auto-updated)
  semantic-layer.md, benchmarks.md, cross-program-rollups.md,
  shared-report-templates.md, weekly-trends-and-snapshots.md
                         Report/indicator pages (not auto-updated: no entry in
                         update_docs.py's FEATURE_TO_DOC_FILE, so no Confluence page)
  embedding-labs.md      Embedding Labs (programme teams and staff; not auto-updated)
  developers/            Developer documentation: for engineers on OTHER systems
                         (see "Two audiences" below). Never auto-updated.
  assets/screenshots/    Manually maintained screenshots

mkdocs.yml               MkDocs Material theme config (Mermaid.js via superfences)

.github/workflows/
  docs-deploy.yml        Strict MkDocs build check on PRs (no deploy)
  docs-update.yml        Per-merge doc updater
  weekly-changelog.yml   Weekly changelog cron + workflow_dispatch
  regenerate-confluence.yml  workflow_dispatch only: runs regenerate_confluence.py
```

## Two audiences

The help site has two kinds of page, produced differently.

|               | Programme documentation                                                             | Developer documentation                                                                                     |
| ------------- | ----------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------- |
| Where         | `user_docs/*.md` (nav: Feature Guides, For Power Users)                             | `user_docs/developers/` (nav: **For Developers**)                                                           |
| Reader        | Programme staff using labs in the browser                                           | Engineers on other systems integrating with labs (an embedding system today)                                |
| Style         | Plain English, no code (the updater's prompt enforces it)                           | Technical by design: claims, HTTP, code, error codes                                                        |
| Written by    | The per-merge updater, from the PR's Product Description (mapped pages), or by hand | **By hand, in the same PR as the interface change**, by the engineer making it                              |
| Automation    | `update_docs.py` may rewrite mapped pages                                           | `update_docs.py` refuses to touch it (`is_developer_doc`, tested in `automation/tests/test_update_docs.py`) |
| Compatibility | Describes behaviour as it is                                                        | A public interface: a breaking change is announced on the page and in #engineering-connect before it ships  |

A feature that has both (embedding: `embedding-labs.md` for staff, `developers/embedding.md` for
integrators) gets one page for each reader, linked to each other, rather than one page doing both.

**When to write developer docs:** any change to something another system calls or relies
on. Today that means the embed token endpoint, the frame address, the frame's messages, and
the error codes (`connect_labs/embed/`). Update `user_docs/developers/` in the same PR. A
reviewer should treat a missing update as a missing test.

**Access:** the developer pages sit behind the labs login like the rest of the help site
(decided 2026-10-23: Connect's engineers have labs accounts). Revisit, by serving only
`developers/` publicly, when a third party with no labs account integrates.

**What never goes in them:** keys, tokens, real client ids of other organisations,
internal hostnames or anything that only works inside Dimagi. The repo is public.

## GitHub Actions secrets required

| Secret                 | Used by                                      | Status         |
| ---------------------- | -------------------------------------------- | -------------- |
| `ANTHROPIC_API_KEY`    | `update_docs.py`, `weekly_changelog.py`      | ✓ Set          |
| `CONFLUENCE_API_TOKEN` | Both scripts                                 | ✓ Set          |
| `CONFLUENCE_EMAIL`     | Both scripts (`connect-wiki-bot@dimagi.com`) | ✓ Set          |
| `SLACK_WEBHOOK_URL`    | `weekly_changelog.py`                        | Add when ready |

## Confluence pages

| Page                                | ID           |
| ----------------------------------- | ------------ |
| Connect Labs Documentation (parent) | `3916103691` |
| Connect Labs Changelog              | `3918528513` |
| Audit & QA Review                   | `3927900187` |
| Workflow Engine                     | `3927801864` |
| Task Management                     | `3928293395` |
| Solicitations                       | `3927179271` |
| Custom Analysis                     | `3928817669` |
| Coverage Maps                       | `3927867398` |
| AI Features                         | `3928817690` |
| Connect MCP & Safe Mode             | `3927801885` |

## Labs help site

The MkDocs build of `user_docs/` is served by Labs itself at
`https://labs.connect.dimagi.com/labs/docs/help/`, behind the Labs login
(`connect_labs/labs/help_site.py`). The `build-docs` stage of the `Dockerfile`
builds it, so it ships with every deploy. Preview locally with
`mkdocs build --site-dir help_site` and open `/labs/docs/help/` on a dev server.

It used to deploy to GitHub Pages; that was never re-enabled after the repo moved
to `dimagi-internal`, and the repo is public, so Pages would publish the docs openly.

## Model usage and cost

| Call                   | Model  | When                                          | Approx cost |
| ---------------------- | ------ | --------------------------------------------- | ----------- |
| User-visibility triage | Haiku  | Per merge (skipped if no Product Description) | ~$0.001     |
| Markdown doc rewrite   | Sonnet | Per merge, per affected feature               | ~$0.01–0.05 |
| Confluence one-liner   | Haiku  | Per merge, per affected feature               | ~$0.001     |
| Weekly summary         | Haiku  | Once per week                                 | ~$0.002     |

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
