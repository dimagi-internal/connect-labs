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

Connect Labs provides two MCP addresses. **Use `mcp/no_user_visit/` unless you need real visit data.** It is the default for everyone.

| Address | What it can access |
| --- | --- |
| `https://labs.connect.dimagi.com/mcp/no_user_visit/` (**default**) | Never shows real user visit data. Can read programme structure (workflows, pipelines, indicator registries, app structure, solicitations, org directory, targeting), can edit workflow and indicator definitions and trigger saved-run generation, and can profile real opportunities and generate synthetic data. Tools that read visits work only on synthetic opportunities whose data was generated. |
| `https://labs.connect.dimagi.com/mcp/` | Full access matching your Connect permissions, including real visit data. Only if your work needs it. |

!!! tip "Using the restricted address for a whole team"
    `mcp/no_user_visit/` is designed as a safe default for a shared or team setup. Signing in through this address gives a restricted token that stays restricted even if it is later used on the main `/mcp/` address.

!!! note "Locking a person to restricted access"
    An administrator can permanently lock an individual account to "no user visit data" (Django admin → Users → **MCP: no user visit data**). Once set, everything that person does through MCP is restricted — on either address, with any token, and including sign-ins and any automated action taken on their behalf. The Labs export API and the demo reseed endpoint also refuse them. This is stronger than the team default above, because it cannot be stepped around by switching to the full `/mcp/` address.

---

## First-Time Setup

### 1. Connect Claude to Labs

=== "Claude Code"

    ```bash
    claude mcp add --transport http connect_labs https://labs.connect.dimagi.com/mcp/no_user_visit/
    ```

    Only if you need real visit data, use the full address instead:

    ```bash
    claude mcp add --transport http connect_labs https://labs.connect.dimagi.com/mcp/
    ```

    Then type `/mcp`, choose `connect_labs` and sign in with CommCare Connect when the browser opens. There is no token to copy.

=== "Claude desktop or claude.ai"

    Open **Settings → Connectors**, choose **Add custom connector**, and add `https://labs.connect.dimagi.com/mcp/no_user_visit/` (or `https://labs.connect.dimagi.com/mcp/` only if you need real visit data). Click **Connect** and sign in with CommCare Connect.

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
| **No user visit data** | Never sees user visit data. Reads workflow definitions, CommCare app structure, pipeline and indicator definitions, solicitations, funds, the organisation directory, targeting data, and microplan sampling. Can edit workflow and indicator definitions (including a workflow's opportunity list; render code only on synthetic opportunities whose data was generated, because render code runs over the visit data of whoever opens the page) and trigger saved-run generation — snapshots, history rebuilds, cache warms, hand-downs and benchmark publication — where the server computes over visits and the token gets back only ids, versions, dates and counts. Cannot delete workflows or pipelines. Can also profile real opportunities on the server and generate synthetic data from those profiles. Same permissions as the `mcp/no_user_visit/` address. Cannot access individual visit rows or per-visit values — including through the export API and data reseed endpoints — except on synthetic opportunities whose data was generated. Opportunity-level counts and dates, and contact details of people who submitted as an organisation, are allowed. |
| **Coaching pictures only** | Lets an Open Chat Studio team fetch coaching pictures. Nothing else. Cannot be used for MCP or the export API. |

Tools the token is not allowed to use do not appear to the agent at all, so the agent cannot accidentally attempt a blocked action.

The token list on the tokens page shows the access level of each token. When you rotate a token, its access level stays the same.

!!! tip "When to use 'No user visit data'"
    Choose this level when you want an AI assistant to help you navigate programme structure, explore or edit indicator and workflow definitions, regenerate saved runs, or query the organisation directory — but you do not want it to have access to any beneficiary visit data. Because it can edit a report's definition, give it to an assistant you would let edit the report itself. It is a good default for any automated or shared setup where full data access is not needed. Tokens at this level can still profile real opportunities and generate synthetic data.

!!! note "Account-level lock overrides token access level"
    If an administrator has locked your account to "no user visit data" (see [MCP Addresses](#mcp-addresses) above), that restriction applies regardless of which token you use or which address you connect through. A Standard token issued to a locked account behaves as a No user visit data token.

---

## What the Restricted Address Blocks

The `mcp/no_user_visit/` address enforces a strict set of rules to prevent real visit data from reaching a restricted caller. The following actions are **not available** on that address, regardless of your token's access level:

- **Reading stored workflow snapshots.** Workflow runs are rebuilt live from generated data instead of reading a stored snapshot that might contain real visits.
- **Pointing `pipeline_preview` at another opportunity's export or at Open Chat Studio sessions.** This prevents a restricted caller from pulling in visit-level data through a pipeline preview.
- **Generating from an old mirror-mode profile.** Profiles saved in the old mirror mode (before case timelines were introduced) hold real cases lightly perturbed, so their data never counts as "generated". Profiling with `case_timelines=true` *is* allowed: it models each worker's caseload and each case's timeline and saves only newly sampled cases, which are generated — not copies of real data. Re-profile any old mirror-mode profiles to use this safer approach.
- **Generating from a profile created before measurement numbers were fully modelled.** Profiles created before all numeric measurements (such as weights and visit-day sequences) were regenerated hold data that traces back to real cases. A synthetic programme profiled before this change no longer counts as generated data and must be re-profiled before it can be read on the restricted address.
- **Writing synthetic data onto a real opportunity, wiping a shared demo environment, or reading or writing a profile bundle outside Drive.**
- **Using the SQL explorer (`explorer_describe`, `explorer_query`).** These tools read live visit data and are not available on the restricted address.
- **Starting a run on a page-type workflow.** Pages are not run through MCP — open them in the browser instead.

The restricted address offers a simpler set of synthetic tools: `synthetic_clone_opp` (plus status checking, fidelity scoring, demo environments, and visibility) rather than the full set of step-by-step tools available on the full-access address.

!!! note "No change for full-access callers"
    If you use the main `/mcp/` address, none of the above restrictions apply to you. The only change that affects everyone is described in the pipeline export name note below.

---

## Profiling Limits

The following limits apply when using the profiling and synthetic-data tools.

| Limit | Value | Who it applies to |
| --- | --- | --- |
| Opportunities per single call | 10 | Restricted address only |
| Time limit per job | 2 hours | Everyone |
| Synthetic jobs running at the same time across the whole system | 2 | Everyone |

If you submit a request that is identical to one already running, Labs returns the existing job rather than starting a duplicate.

!!! tip "If you hit a limit"
    Wait for a running job to finish before starting a new one if the system-wide limit is reached.

---

## Long-Running Tools

Some MCP tools — such as grading a programme's indicators as of a past date or rebuilding a report's full history — can take several minutes to complete. These tools run as **background jobs** so that they survive a Labs update mid-run and do not hold the connection open.

### How it works

When you ask Claude to run one of these tools:

1. Labs starts the work in the background and returns a **job id** straight away.
2. If the job finishes within about 30 seconds, the result comes back immediately, just as it always did.
3. If it is still running after 30 seconds, Claude uses the `job_status` tool to check on it periodically and reports progress to you. When the job is done, `job_status` returns the full result.

You do not need to do anything differently — Claude handles the checking automatically.

### Job tools

| Tool | What it does |
| --- | --- |
| `job_status` | Checks the progress of a running job and returns the result when it is done. Called with no id, it lists your recent jobs — useful if a job id has been lost. |
| `job_cancel` | Stops a job that is still running. |

**Only you can see your own jobs and their results.** Results are kept for one week after the job finishes. This privacy rule applies to all background jobs, including microplans actions such as map previews, regrouping, reassigning, regenerating a plan, and finding comparable surrounding wards.

!!! tip "If a long-running task seems to have disappeared"
    Ask Claude to run `job_status` with no id to list your recent jobs. The job is most likely still running or has finished and its result is waiting.

---

## SQL Explorer

The SQL explorer at [labs.connect.dimagi.com/labs/explorer/](https://labs.connect.dimagi.com/labs/explorer/) lets you query live visit data for the opportunities you have access to, without needing a developer to write reports.

### How it works

Select one or more opportunities from the list, then use the two panels on the page:

- **Field browser.** Lists every question in the submitted forms for the opportunities you selected, shows how often each field is filled in, and displays the answer choices for multiple-choice questions. Use the search box to find fields quickly (for example, type "birth" to find all birth-related questions). Click any field to insert it directly into your query.
- **SQL editor.** Write a query against the `visits` table — one row per visit — which contains the opportunity, its LLO, the worker, the case, the visit status, and the full form data. Run your query and view results as a table. Download results as a CSV file.

You can also click **Ask an agent** on the page to have the AI assistant write a query for you based on a plain-English question (for example, "hospital vs home births by LLO"). The agent writes the SQL and places it in the editor for you to review and run. **The agent itself never receives your program's real visit data** — it only learns whether the query was valid, not what the results contain. This means you stay in control of your data at all times. On synthetic (demo) opportunities, the agent can run queries and read results in order to test and refine its SQL.

### Using the SQL explorer through MCP

The same two tools are available to Claude Desktop and Claude Code through the MCP:

| Tool | What it does |
| --- | --- |
| `explorer_describe` | Lists available fields for the selected opportunities, with fill rates and answer choices |
| `explorer_query` | Runs a SQL query against the `visits` table and returns results |

This means you can ask Claude questions like "show me hospital vs home births broken down by LLO" and it will use these tools to query your data and return an answer directly.

!!! note "Access and safety"
    The SQL explorer is read-only. It is limited to opportunities you hold, and every query is recorded in the audit trail. The explorer tools are not available on the `mcp/no_user_visit/` restricted address — they require the full `/mcp/` address. When you use **Ask an agent** on the page, the agent writes and can run the query in your browser but never receives real visit data in its context — results from real opportunities stay in your browser only.

---

## Solicitations

Solicitations (Expressions of Interest and Requests for Proposal) are calls for partners that organisations post when they are looking for implementing teams. MCP can read solicitation data through both the restricted and full-access addresses.

### Public directory

The solicitations directory at `/solicitations/` is public — no login is required. It lists every open call, showing the organisation behind each one. You can search and filter by organisation and type. A "Recently closed" section appears below the open calls. You can share a link to any call directly with outside organisations.

Each organisation also has its own page at `/solicitations/o/<organisation>/` listing its open and past calls. Calls close automatically when their deadline passes and are shown as "Closed — deadline passed" afterwards; nobody can respond to a closed call.

### Managing your organisation's calls

If you manage calls for an organisation, your workspace is at `/solicitations/o/<organisation>/manage/`, where you can see every call — including drafts — alongside response counts. Go to `/solicitations/manage/` to reach the organisations you can manage, or to find out how to request access.

### Responding to a call

You respond as one of your organisations, or by naming a new one. You can save one draft and return to it before submitting. After you submit, the form confirms what happens next and sends you a receipt by email. You can see only your own application — organisations cannot see or review responses from their own members.
