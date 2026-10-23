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
- **Reading visit data with the Data tools (`data_describe`, `data_query`, `data_visits`, and the older `explorer_describe` / `explorer_query` names).** These tools read live visit data and are not available on the restricted address, except over generated synthetic opportunities. `data_validate` and `data_workflow_context` read no visit data and are available there.
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

## Results Labs Keeps

Tools that return a table (`data_query`, `workflow_run_indicators`) or a result a card draws (`targeting_pmc_states`) also keep that result in Labs for one day. The result carries a link to it, `result_uri`, which looks like `labs-result://<id>`. The same link is included as an MCP `resource_link`.

Any MCP client can read the link back with the standard `resources/read` request. That includes Claude Code, ACE, and canopy. Labs serves it as you, one page at a time:

- **Paging.** Add `?page=2&page_size=200` to the link. A page holds 100 rows unless you ask otherwise, and at most 1,000.
- **Who can read it.** You can, and so can anyone who could run the same thing themselves (for example, someone with access to every opportunity the query read). Anyone else is refused with `forbidden`.
- **What it shows.** Each read returns exactly the rows the tool produced. A result is never re-run, so the numbers cannot change between reads.
- **For a day.** After a day the link returns `expired`. Run the tool again to get a new one.

Sensitive data follows the same rules as the tool that produced it. A token that cannot see user visit data cannot read a result that holds them. Through canopy, only an agent Labs has cleared for sensitive data can read one. Every read is recorded in the audit trail.

## Data (formerly the SQL explorer)

The Data page at [labs.connect.dimagi.com/labs/data/](https://labs.connect.dimagi.com/labs/data/) lets you query live visit data for the opportunities you have access to, down to each submitted form, without needing a developer to write reports. The old `/labs/explorer/` address redirects here.

### How it works

Select one or more opportunities from the list, then use the two panels on the page:

- **Field browser.** Lists every question in the submitted forms for the opportunities you selected, shows how often each field is filled in, and displays the answer choices for multiple-choice questions. Use the search box to find fields quickly (for example, type "birth" to find all birth-related questions). Click any field to insert it directly into your query.
- **SQL editor.** Write a query against the `visits` table — one row per visit — which contains the opportunity, its LLO, the worker, the case, the visit status, the review and flag details, the rejection reason, location, justification, completed work, image references, and the full form data. Run your query and view results as a table. Download results as a CSV file.
- **Whole visits.** Click any `visit_id` in a result to open that visit with every field and the complete submitted form, so you can check a number against the visits behind it.

You can also ask the agent on the page to write a query for you from a plain-English question (for example, "hospital vs home births by LLO"). On production opportunities, whose visit data is sensitive, the agent writes the SQL and places it in the editor for you to review and run — **it does not receive your program's visit data** — unless Labs has cleared it for sensitive data. On synthetic (demo) opportunities it can run queries and read results directly, and shows the output as a table and a chart in the chat, along with the SQL and a link that opens the query on the Data page.

The chat card reads the result from Labs as you, a page at a time — use Previous/Next and the rows-per-page menu, and click a column heading to sort the page. The card shows the rows the agent actually reasoned on, so there is no risk of a re-run producing different numbers. Labs keeps each result for a day; after that the card says "This result has expired — ask again in the chat". The card also tells you if a result isn't yours to see, or if it holds sensitive data that stays in Labs (in which case a button lets you open the query on the Data page). The PMC states card reads its ranking the same way.

An agent that Labs has cleared for sensitive data (the **Sensitive data** tick in Labs Admin › Canopy agents), running on a sensitive-data runner (a zero-data-retention, locked-down machine), can read production rows too; the data stays in Labs and that runner.

!!! note "How much the agent sees"
    In a canopy chat, the agent receives a short summary plus the first 20 rows of a result, and reads further rows only when it needs them. Claude Code, ACE, and scripts calling Labs directly still receive every row, as before.

### Editing workflows from the agent on a workflow's page

The agent embedded in a workflow's run page runs on sensitive-data runners, reads the run and the data behind it, and can propose edits to the workflow and its pipelines for you to confirm. Those edits stay in Labs. It runs none of the workflow's actions.

The agent **never saves anything on its own.** When it has a change to propose, it presents a card in the chat showing exactly what would change, with the old version and the new version side by side. Nothing is written until you decide:

- **Confirm and save** — saves the change straight to the workflow. The workflow's version history is your undo if you change
