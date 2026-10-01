# Connect MCP

Connect Labs gives technical program staff a way to edit workflows using Claude Code (an AI assistant) from the command line — without writing code themselves.

!!! note "Who this is for"
    This feature is for program administrators and technical staff who are comfortable working in a terminal. If you just want to use the AI assistant inside the Labs browser app, see [AI Features](ai-features.md) instead.

---

## What Is This For?

Normally, editing a workflow's display logic or data fields requires a developer to modify code. With the Connect MCP (Model Context Protocol), you can describe changes in plain English and Claude Code makes the edits for you.

**Claude Code** is the AI assistant CLI you run in your terminal. **MCP** (Model Context Protocol) is the server it connects to — hosted inside Connect Labs — that gives Claude Code the tools to read and update workflows. You use Claude Code; it uses the MCP behind the scenes.

<!-- prettier-ignore -->
> _"Add a column showing how many weeks since the last visit"_
> _"Change the status colors so 'Overdue' shows in red"_
> _"Remove the RUTF field from the table — it's not relevant for this program"_

Claude reads the workflow's current definition, makes the change, and pushes it back to Labs — all from your terminal.

---

## Prerequisites

| You need | For |
| --- | --- |
| A **Labs login** (the account you use at [labs.connect.dimagi.com](https://labs.connect.dimagi.com)) | Everything |
| **Claude Code** (`npm install -g @anthropic-ai/claude-code`), or Claude desktop / claude.ai | Everything |
| A clone of the connect-labs repository (`git clone https://github.com/dimagi-internal/connect-labs.git`) | The `/workflow-author` skill and Safe Mode |
| The 1Password CLI (`op`), with access to the **Employee** and **AI-Agents** vaults | Safe Mode only |

Ask in **#engineering-connect** if you're unsure about any of these.

!!! note "You don't need to run Labs locally"
    For workflow editing you do **not** need to run the Django app locally. The Labs MCP server is hosted on Labs itself; Claude pushes workflow changes directly to Labs prod, and you verify the result in your browser. Run locally only if you are modifying the core Connect Labs application code itself.

---

## MCP Addresses

Connect Labs provides two MCP addresses. Choose the one that matches how much data access you need.

| Address | What it can access |
| --- | --- |
| `https://labs.connect.dimagi.com/mcp/` | Full access matching your Connect permissions, including real visit data. |
| `https://labs.connect.dimagi.com/mcp/no_user_visit/` | Never shows real user visit data. Can read programme structure (workflows, pipelines, indicator registries, app structure, solicitations, org directory, targeting) and can profile real opportunities and generate synthetic data. Tools that read visits work only on synthetic opportunities whose data was generated. |

!!! tip "Using the restricted address for a whole team"
    `mcp/no_user_visit/` is designed as a safe default for a shared or team setup. Signing in through this address gives a restricted token that stays restricted even if it is later used on the main `/mcp/` address.

!!! note "Locking a person to restricted access"
    An administrator can permanently lock an individual account to "no user visit data" (Django admin → Users → **MCP: no user visit data**). Once set, everything that person does through MCP is restricted — on either address, with any token, and including sign-ins and any automated action taken on their behalf. The Labs export API and the demo reseed endpoint also refuse them. This is stronger than the team default above, because it cannot be stepped around by switching to the full `/mcp/` address.

---

## First-Time Setup

### 1. Connect Claude to Labs

=== "Claude Code"

    ```bash
    claude mcp add --transport http connect_labs https://labs.connect.dimagi.com/mcp/
    ```

    To use the restricted address instead:

    ```bash
    claude mcp add --transport http connect_labs https://labs.connect.dimagi.com/mcp/no_user_visit/
    ```

    Then type `/mcp`, choose `connect_labs` and sign in with CommCare Connect when the browser opens. There is no token to copy.

=== "Claude desktop or claude.ai"

    Open **Settings → Connectors**, choose **Add custom connector**, and add `https://labs.connect.dimagi.com/mcp/` (or `https://labs.connect.dimagi.com/mcp/no_user_visit/` for the restricted version). Click **Connect** and sign in with CommCare Connect.

Claude acts as you, with your Connect permissions. To see or disconnect the apps you have signed in, visit [labs.connect.dimagi.com/labs/mcp/tokens/](https://labs.connect.dimagi.com/labs/mcp/tokens/).

### 2. (Safe Mode only) Register a Labs token

[Safe Mode](connect-safe-mode.md) cannot use the browser sign-in — it needs a Personal Access Token. From **inside your connect-labs checkout**, start Claude Code and run:

```
/labs-token-setup
```

When asked, choose **Production labs environment**, then approve the token in the browser. Fully quit and restart Claude Code afterwards. The token lasts 90 days by default; run the skill again to replace it.

### 3. (Safe Mode only) Fetch the CommCare HQ credentials

```bash
op signin --account dimagi
op inject -f -i .env.tpl -o .env
```

The `.env` holds the CommCare HQ credentials Safe Mode's read-only app-structure tools use.

---

## Personal Access Tokens

Personal Access Tokens let you connect an MCP client (such as Claude Code in Safe Mode) to Labs without going through the browser sign-in flow. You create and manage them at [labs.connect.dimagi.com/labs/mcp/tokens/](https://labs.connect.dimagi.com/labs/mcp/tokens/).

### Access levels

When you create a token you choose an **Access** level:

| Access level | What it can do |
| --- | --- |
| **Standard** | Full access matching your Connect permissions, including reading visit data and making workflow edits. |
| **No user visit data** | Read-only access to workflow definitions, CommCare app structure, pipeline and indicator definitions, solicitations, funds, the organisation directory, targeting data, and microplan sampling. Can also profile real opportunities on the server and generate synthetic data from those profiles. Cannot access individual visit rows or per-visit values — including through the export API and data reseed endpoints — except on synthetic opportunities whose data was generated. Opportunity-level counts and dates, and contact details of people who submitted as an organisation, are allowed. |

Tools the token is not allowed to use do not appear to the agent at all, so the agent cannot accidentally attempt a blocked action.

The token list on the tokens page shows the access level of each token. When you rotate a token, its access level stays the same.

!!! tip "When to use 'No user visit data'"
    Choose this level when you want an AI assistant to help you navigate programme structure, explore indicator definitions, or query the organisation directory — but you do not want it to have access to any beneficiary visit data. It is a good default for any automated or shared setup where full data access is not needed. Tokens at this level can still profile real opportunities and generate synthetic data.

!!! note "Account-level lock overrides token access level"
    If an administrator has locked your account to "no user visit data" (see [MCP Addresses](#mcp-addresses) above), that restriction applies regardless of which token you use or which address you connect through. A Standard token issued to a locked account behaves as a No user visit data token.

---

## What the Restricted Address Blocks

The `mcp/no_user_visit/` address enforces a strict set of rules to prevent real visit data from reaching a restricted caller. The following actions are **not available** on that address, regardless of your token's access level:

- **Reading stored workflow snapshots.** Workflow runs are rebuilt live from generated data instead of reading a stored snapshot that might contain real visits.
- **Pointing `pipeline_preview` at another opportunity's export or at Open Chat Studio sessions.** This prevents a restricted caller from pulling in visit-level data through a pipeline preview.
- **Generating from an old mirror-mode profile.** Profiles saved in the old mirror mode (before case timelines were introduced) hold real cases lightly perturbed, so their data never counts as "generated". Profiling with `case_timelines=true` *is* allowed: it models each worker's caseload and each case's timeline and saves only newly sampled cases, which are generated — not copies of real data. Re-profile any old mirror-mode profiles to use this safer approach.
- **Writing synthetic data onto a real opportunity, wiping a shared demo environment, or reading or writing a profile bundle outside Drive.**

!!! note "No change for full-access callers"
    If you use the main `/mcp/` address, none of the above restrictions apply to you. The only change that affects everyone is described in the pipeline export name note below.

---

## Profiling Limits

The following limits apply when using the profiling and synthetic-data tools. Most limits apply to everyone; the per-call limit applies only to the restricted address.

| Limit | Value | Who it applies to |
| --- | --- | --- |
| Jobs running at the same time per person | 2 | Everyone |
| Opportunities profiled per person per day | 40 | Everyone |
| Opportunities per single call | 10 | Restricted address only |
| Time limit per job | 2 hours | Everyone |

If you submit a request that is identical to one already running, Labs returns the existing job rather than starting a duplicate.

!!! tip "If you hit a limit"
    Wait for a running job to finish before starting a new one, or spread your profiling requests across the day if you are working with a large list of opportunities.

---

## Pipeline Export Names

!!! note "Change for all callers"
    When naming a pipeline export, the name must now be a **single plain word** — no spaces, punctuation, or special characters. This applies to both the full-access and restricted addresses.

---

## Pipeline Reads of Open Chat Studio

When a pipeline reads Open Chat Studio (OCS) chat sessions, it now uses **your own OCS connection** rather than a shared server key. This means the pipeline sees exactly the sessions you can see when you log in to OCS directly — no more and no less.

If you have not connected OCS to your Labs account yet, the pipeline will tell you to connect at [labs.connect.dimagi.com/labs/ocs/initiate/](https://labs.connect.dimagi.com/labs/ocs/initiate/) before it can read your sessions.

!!! note "Web dashboards are unchanged"
    This change affects only pipelines run through MCP. Web dashboards that read OCS data continue to work as before.

---

## High-Fidelity Synthetic Data (Case Timelines)

When you profile an opportunity and generate synthetic data, you can request **case timelines** (`case_timelines=true`). This is the high-fidelity mode: it produces realistic, fully generated data rather than near-copies of real records.

### How it works

Profiling with case timelines uses real cases only to **fit statistical models**. The profile then saves **new cases sampled from those models** — no real case is ever stored or shipped. When synthetic data is generated from that profile:

- **Every worker keeps its caseload.** Each worker gets the same number of cases, with the same case lengths, as its real counterpart.
- **Each case's timeline is modelled.** Growth, visit spacing, per-case constants (such as birth weight and date of birth), and the links between them are drawn together, so patterns like heavier babies growing faster are preserved.
- **Outcomes are tied to growth.** A slow grower ends as often as in the source data, and a case closes when its baby's outcome is reached.
- **App-computed fields keep their meaning.** Visit counters and ages are rebuilt from each case's timing rather than copied.

### Safety guarantees

- Any categorical answer seen in fewer than 5 real cases is never modelled. Any field recorded in fewer than 5 cases is excluded entirely.
- Start dates are smoothed so no individual worker's start date is identifiable.
- A sampled case that lands too close to a real one is redrawn (a distance-to-closest-record check).

Because no real case values are stored or replicated, data generated this way counts as **generated** and is allowed on the restricted MCP address.

!!! warning "Old mirror-mode profiles are not the same"
    The previous high-fidelity mode (mirror mode, `mirror=true`) stored real cases with small perturbations — a near-copy of real data. Those profiles are still supported as an alias (`mirror=true` maps to `case_timelines=true` going forward) but **profiles saved in the old mirror mode before case timelines were introduced are not re-usable as generated data**. Re-profile any such opportunity to get a safe, fully generated profile.

---

## Editing Workflows

!!! tip "Working with real program data?"
    Launch Claude via [Safe Mode](connect-safe-mode.md) before running `/workflow-author` — it blocks data-exfiltration channels while keeping workflow edits available.

Use the MCP-powered workflow skill:

```
/workflow-author
```

Then describe what you want in plain English. Claude will:

1. Pull the current workflow definition from Labs
2. Show you what it plans to change
3. Apply the change and push it back
4. Confirm the update was successful

To verify your change: open the workflow in your browser at [labs.connect.dimagi.com](https://labs.connect.dimagi.com).

### Iteration loop and deployment bar

Workflow definitions are user-generated content stored in Connect prod — updating them requires no pull request and no code review. Keep a low bar for pushing changes: if something looks wrong, describe the fix and let Claude push again. To revert, tell Claude what to undo and it will push a corrected version. If you can't resolve an issue after a few iterations, ask in **#connect-labs**.

The power of this loop is: describe change → Claude pushes → reload browser → verify → repeat. Get comfortable with that cadence rather than doing a lot of intermediate work to validate locally first.

**Note:** changes to the MCP server itself (the Labs code that powers these tools) _do_ require a code deploy. But for all workflow edits, the MCP push is sufficient.

### Template authoring (regular Claude session only)

Safe Mode is for editing **live workflow instances**. If you are authoring or updating a **seed template** (a `.py` file in the repository that other workflows are cloned from), you need a regular Claude Code session instead — Safe Mode blocks the file writes that template authoring requires.

In a regular session, you can use `workflow_sync_from_template_file` to push a local `.py` file straight to a live preview workflow without a full redeploy. The full loop is in the "Two iteration loops" section of the [`workflow-author` skill](https://github.com/dimagi-internal/connect-labs/blob/main/.claude/skills/workflow-author/SKILL.md). It refuses a workflow that follows the deployed template.

---

## KMC Reports — Unified Indicator Set

!!! note "Recent change"
    KMC reports were updated to use a single set of 24 indicators. If you work with KMC data, read this section before interpreting any figures.

KMC reports previously used two overlapping sets of indicators — a workbook series and a demo scorecard series — which applied different rules for counting babies and judging outcomes. Those two sets have been merged into one.

### What the unified rules are

- **"Started" KMC** means a baby has two or more follow-up visits.
- **Outcome figures** are only calculated from babies whose first visit was at least 28 days ago.
- **Growth figures** are only calculated from babies whose first visit was at least 42 days ago.
- **Growth** is judged against the baby's birthweight band.
- Any figure with fewer than 20 babies behind it is not shown.

These rules now apply consistently across all KMC indicators.

### Indicators kept from the workbook series

Several workbook indicators were not covered by the old scorecard rules. These are kept and now follow the same unified rules above:

- Loss to follow-up
- Early growth rate
- Median days to enrolment
- Danger signs
- Referrals and self-referrals
- KMC hours
- Birth-copy rate
- Percentage of babies with computable growth

!!! warning "Working definitions"
    These indicators are still being refined. Each indicator's definition note says so where it applies. Treat them as working figures, not final metrics.

### What you will notice in the reports

- Indicators now have plain descriptive names instead of codes such as C14 or N13.
- The **Scorecard (N) / Workbook (C)** toggle on the opportunity report is gone — there is only one view now.
- Mortality figures are no longer automatically withheld on
