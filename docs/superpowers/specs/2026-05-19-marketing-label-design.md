# Design: `[Marketing]` Labels in Weekly Changelog

**Date:** 2026-05-19  
**Status:** Approved

## Problem

The weekly changelog summary mixes bullets about the Connect marketing/public site (the
`prelogin` app) with bullets about app features. Program staff reading the summary cannot
tell whether a bullet describes a product capability or a website update.

Example of the confusion (from May 18, 2026 summary):

> - **Mobile navigation now accessible** — Hamburger menu on phones…

This was a marketing-site fix, not an app feature, but it appears alongside app changes
with no distinction.

## Goal

Bullets that come from marketing/website PRs are prefixed with `[Marketing]` in both the
Confluence changelog and the Slack message:

> - **Mobile navigation now accessible** — [Marketing] Hamburger menu on phones…

## Scope

All changes are in `automation/weekly_changelog.py`. No changes to the GitHub Actions
workflow YAML or the PR template.

## Design

### 1. Marketing path definitions

A module-level constant lists the three path prefixes that constitute the public/marketing
site:

```python
MARKETING_PATHS = (
    "commcare_connect/prelogin/",
    "commcare_connect/templates/prelogin/",
    "commcare_connect/static/prelogin/",
)
```

### 2. Per-PR file fetching

A new function `fetch_pr_files(pr_number, repo)` shells out to the GitHub CLI:

```
gh api repos/{repo}/pulls/{pr_number}/files --jq '.[].filename'
```

`gh` is pre-authenticated in GitHub Actions — no token plumbing required. Returns a list
of filename strings.

### 3. PR classification

`classify_pr(files)` inspects the filename list and returns one of three strings:

| Result | Condition |
|---|---|
| `"marketing"` | Every changed file matches a `MARKETING_PATHS` prefix |
| `"app"` | No changed file matches any `MARKETING_PATHS` prefix |
| `"mixed"` | Some files match and some don't |

### 4. `load_user_visible_prs` update

After extracting the `Product Description` for each PR, the function calls
`fetch_pr_files` + `classify_pr` and adds a `category` field (`"marketing"`, `"app"`, or
`"mixed"`) to the PR dict.

### 5. Prompt and data changes

**PR text block** (fed to Claude) gains a category annotation:

```
PR #193 [category: marketing]: feat(prelogin): hamburger nav on mobile
<description>
```

**System prompt** gets one new rule appended to the format rules section:

> - A PR marked `[category: marketing]` — prefix every bullet it produces with `[Marketing]`
> - A PR marked `[category: mixed]` — split its description into separate bullets for app
>   changes and marketing/website changes; prefix only the marketing bullets with `[Marketing]`
> - A PR marked `[category: app]` — no prefix on its bullets

### 6. Output labels

Only `[Marketing]` appears in the final output. There is no `[App]` or `[Mixed]` label.
App-only bullets render exactly as today.

## What doesn't change

- Confluence storage format rendering (`markdown_to_storage`)
- Slack Block Kit structure
- PR template
- GitHub Actions workflow YAML
- All other system prompt rules (max 8 bullets, no jargon, etc.)

## Testing

Run the script locally against a manually crafted `merged_prs.json` that includes:

1. A pure-app PR (no prelogin files) — expect no `[Marketing]` label
2. A pure-marketing PR (all prelogin files) — expect `[Marketing]` on all its bullets
3. A mixed PR (both prelogin and app files) — expect split bullets, `[Marketing]` only on
   the marketing portion
