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
- **Writing synthetic data onto a real opportunity, wiping a shared demo environment, or reading or writing a profile bundle outside Drive.**
- **Using the SQL explorer (`explorer_describe`, `explorer_query`).** These tools read live visit data and are not available on the restricted address.

The restricted address offers a simpler set of synthetic tools: `synthetic_clone_opp` (plus status checking, fidelity scoring, demo environments, and visibility) rather than the full set of step-by-step tools available on the full-access address.

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
| Synthetic jobs running at the same time across the whole system | 2 | Everyone |

If you submit a request that is identical to one already running, Labs returns the existing job rather than starting a duplicate.

!!! tip "If you hit a limit"
    Wait for a running job to finish before starting a new one, or spread your profiling requests across the day if you are working with a large list of opportunities.

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

## Targeting: Nigerian State Cost-Effectiveness Rankings

On the Targeting page, the agent can answer questions like "of these Nigerian states, which state and which malaria chemoprevention design is most cost-effective at my delivery costs? Rank the top 10."

### How it works

Each Nigerian state is modelled in its own setting using IDM's EMOD disease model. Transmission intensity is fitted to the state's malaria prevalence from DHS survey data, and the seasonal pattern comes from the state's rainfall. This means results reflect the actual epidemiological conditions of each state rather than a national average.

Each combination of state and chemoprevention design — PMC (Perennial Malaria Chemoprevention) or SMC (Seasonal Malaria Chemoprevention) — is ranked by **cost per under-5 death averted**. The ranking also shows each combination's multiple of GiveWell's cost-effectiveness benchmark, and the answer notes how many states in the top 10 clear GiveWell's 6× bar.

!!! note "A ranking below the bar is reported as-is"
    If the top 10 combinations all sit above the 6× threshold (meaning they cost more per death averted than GiveWell's bar), the agent reports that result clearly. It does not present any combination as a recommendation simply because it ranked highest.

### Changing costs

Entering a different price per visit re-prices the entire ranking instantly, without re-running the models. This lets you explore how your programme's delivery costs affect which states and designs are most attractive.

For a one-off "what if" question about a single state, the agent runs the scenario against that state's own fitted model.

!!! note "Per-state results grid"
    The full per-state results grid will appear once the model batch finishes computing. Until then, the tool will tell you that per-state results are not yet available.

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

## Drive-Backed Pipelines

Workflows that read data from Google Drive have received several improvements to speed, memory use, and correctness.

### Shared reads across pipelines

If a workflow page has several pipelines that all point at the same Drive source (same folder or file, file pattern, null markers, username column, and date column), Labs now reads that source **once** and shares the result between them. Previously each pipeline fetched its own
