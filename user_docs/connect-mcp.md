# Connect MCP

Connect Labs has an MCP server: the connection Claude uses to read and act on labs (workflows, pipelines, solicitations, targeting, synthetic data)

---

## What Is This For?

Normally, editing a workflow's display logic or data fields requires a developer to modify code. With the Connect MCP (Model Context Protocol), you can describe changes in plain English and Claude Code makes the edits for you.

**Claude Code** is the AI assistant CLI you run in your terminal or in the 'code' tab of Claude Desktop. **MCP** (Model Context Protocol) is the server it connects to — hosted inside Connect Labs — that gives Claude Code the tools to read and update workflows. You use Claude Code; it uses the MCP behind the scenes.

<!-- prettier-ignore -->
> _"Add a column showing how many weeks since the last visit"_
> _"Change the status colors so 'Overdue' shows in red"_
> _"Remove the RUTF field from the table — it's not relevant for this program"_

Claude reads the workflow's current definition, makes the change, and pushes it back to Labs.

---

## Prerequisites

| You need | For |
| --- | --- |
| A **Labs login** (the account you use at [labs.connect.dimagi.com](https://labs.connect.dimagi.com)) | Everything |
| **Claude Code** (`npm install -g @anthropic-ai/claude-code`), or Claude desktop / claude.ai | Everything |
| A clone of the connect-labs repository (`git clone https://github.com/dimagi-internal/connect-labs.git`) | The `/workflow-author` skill |

Ask in **#connect-labs** if you're unsure about any of these.

---

## First-Time Setup

### 1. Connect Claude to Labs

=== "Claude Code"

    ```bash
    claude mcp add --transport http connect_labs https://labs.connect.dimagi.com/mcp/no_user_visit/
    ```

    Then type `/mcp`, choose `connect_labs` and sign in with CommCare Connect when the browser opens. There is no token to copy.

    !!! note "Full-access sign-in still goes to canopy and back"
        If you connect using the full `/mcp/` address, Labs will send you to canopy to confirm with your passkey before completing sign-in. No extra step is needed for the `mcp/no_user_visit/` address.

=== "Claude desktop or claude.ai"

    Open **Settings → Connectors**, choose **Add custom connector**, and add `https://labs.connect.dimagi.com/mcp/no_user_visit/` (or `https://labs.connect.dimagi.com/mcp/` only if you need real visit data). Click **Connect** and sign in with CommCare Connect.

    !!! note "Full-access sign-in still goes to canopy and back"
        If you connect using the full `/mcp/` address, Labs will send you to canopy to confirm with your passkey before completing sign-in. No extra step is needed for the `mcp/no_user_visit/` address.

Claude acts as you, with your Connect permissions. To see or disconnect the apps you have signed in, visit [labs.connect.dimagi.com/labs/mcp/tokens/](https://labs.connect.dimagi.com/labs/mcp/tokens/).

### 2. (Scripts and headless agents only) Register a Labs token

Anything that cannot open a browser to sign in — a script, a scheduled job, a headless agent — needs a Personal Access Token instead. From **inside your connect-labs checkout**, start Claude Code and run:

```
/labs-token-setup
```

When asked, choose **Production labs environment**, then approve the token in the browser. Fully quit and restart Claude Code afterwards. The token lasts 1 day by default; run the skill again to replace it, or set a longer lifetime (up to 365 days) on the tokens page when you approve it.

!!! note "Full-access tokens require a passkey confirmation"
    If you choose a full-access token during setup, a small canopy window opens over the page and asks you to touch your passkey. If you are not a member of Labs' canopy workspace, you can create a no-user-visit token only.

---

## MCP Addresses

Connect Labs provides two MCP addresses. **Use `mcp/no_user_visit/` unless you need real visit data.** It is the default for everyone.

| Address | What it can access |
| --- | --- |
| `https://labs.connect.dimagi.com/mcp/no_user_visit/` (**default**) | Never shows real user visit data. Can read programme structure (workflows, pipelines, indicator registries, app structure, solicitations, org directory, targeting), can edit workflow and indicator definitions and trigger saved-run generation, and can profile real opportunities and generate synthetic data. Tools that read visits work only on synthetic opportunities whose data was generated. |
| `https://labs.connect.dimagi.com/mcp/` | Full access matching your Connect permissions, including real visit data. Only if your work needs it. |

On the restricted address, anything that reads visits needs a synthetic opportunity whose data was generated. [Synthetic Data](synthetic-data.md) explains what those are and how to make one in a single request.

!!! note "Locking a person to restricted access"
    An administrator can permanently lock an individual account to "no user visit data" (Django admin → Users → **MCP: no user visit data**). Once set, everything that person does through MCP is restricted — on either address, with any token, and including sign-ins and any automated action taken on their behalf. The Labs export API and the demo reseed endpoint also refuse them. This is stronger than simply choosing the restricted address, because it cannot be stepped around by switching to the full `/mcp/` address.

---

## What "No User Visit Data" Means

In one sentence: **a restricted Claude never sees a real beneficiary's visit, but it can still see and change how your programme is built.**

It is about what Claude *sees*, not about whether it may change things.

### What it cannot do

- **See real visit data.** No visit rows and no per-visit values, whether raw or worked out by a pipeline, a workflow run, a snapshot or a report. The tools that read visits — querying data, listing visits, previewing a pipeline, opening a workflow run, dumping records — run **only** on synthetic opportunities whose data was generated. Point one at a real opportunity and it refuses. [Synthetic Data](synthetic-data.md) explains how to get one it can read.
- **Read a stored snapshot of a saved run.** A snapshot saved earlier was built from whatever the run read at the time, so a restricted Claude is never handed one. It gets a fresh build over generated data, or a refusal.
- **Delete a workflow or a pipeline.** Editing is allowed; deleting is not.
- **Write synthetic data onto a real opportunity**, or wipe a shared demo environment.
- **Run a page-type workflow.** Open pages in the browser instead.

### What it can do

- **Read how your programme is built.** Workflow definitions, pipeline schemas, indicator registries, CommCare app structure, solicitations and their responses, funds, the organisation directory, admin boundaries, targeting data and microplan sampling.
- **Edit definitions.** It can change a workflow's configuration and its opportunity list, and create and edit indicator registries. (Render code is the exception: it can only be edited on a workflow whose opportunities are all generated, because render code later runs over real data in whoever opens the page.)
- **Set work going on the server.** Creating runs, saving snapshots, rebuilding a report's history, warming a visit cache, handing a report down and publishing a benchmark all work. For these the **server** reads the visits and keeps the result; what comes back to Claude is ids, versions, dates and counts.
- **Make synthetic data.** It can measure a real opportunity on the server and generate a synthetic copy from that measurement — see [Synthetic Data](synthetic-data.md).

!!! note "Counts and dates are allowed"
    Opportunity-level counts and dates are on the allowed side of the line, as are the contact details of people who submitted something as an organisation. The line is drawn at an individual visit and its values.

---

## Checking Which Address You Are On

The restriction comes from **the address**, not from your token. A full-access token pointed at `mcp/no_user_visit/` is still a restricted call. So the address your tool is configured with is the thing to check.

=== "Claude Code"

    List your servers and their addresses:

    ```bash
    claude mcp list
    ```

    Look at the `connect_labs` row. (Typing `/mcp` inside a Claude Code session shows the same servers and lets you sign in or out.) The setting itself lives in `~/.claude.json`, under `mcpServers`.

    To switch to the restricted address, remove the server and add it back:

    ```bash
    claude mcp remove connect_labs
    claude mcp add --transport http connect_labs https://labs.connect.dimagi.com/mcp/no_user_visit/
    ```

    **Restart Claude Code afterwards.** A change to the address does not take effect in a session that is already running — quit fully and start again, then type `/mcp` and sign in.

=== "Claude desktop or claude.ai"

    Open **Settings → Connectors** and find the Connect Labs connector. The address it was added with is shown there.

    To switch, remove the connector and add it again with `https://labs.connect.dimagi.com/mcp/no_user_visit/`, then click **Connect** and sign in. There is no way to edit an existing connector's address in place.

Note the trailing slash on `mcp/no_user_visit/` — without it the address will not work.

!!! tip "Belt and braces: a restricted token too"
    If you want the restriction to hold even when something is pointed at the full address, mint a **No user visit data** token at [labs.connect.dimagi.com/labs/mcp/tokens/](https://labs.connect.dimagi.com/labs/mcp/tokens/) and use that. Either the address or the token is enough on its own; both is simply harder to undo by accident. Stronger still is the account-level lock described above, which an administrator sets.

---

# Advanced


## Personal Access Tokens

Personal Access Tokens let you connect an MCP client to Labs without going through the browser sign-in flow — a script, a scheduled job or any headless agent that cannot open a browser. You create and manage them at [labs.connect.dimagi.com/labs/mcp/tokens/](https://labs.connect.dimagi.com/labs/mcp/tokens/).

### Creating and rotating tokens

**No-user-visit tokens** can be created and rotated by you directly on the tokens page, with no extra step.

**Full-access (Standard) tokens** require a passkey confirmation through canopy. When you create or rotate a Standard token, a small canopy window opens over the tokens page, you touch your passkey, and the token appears — you stay on the same page throughout. If the window cannot open, a link lets you approve in canopy instead. Every outcome shows a message at the top of the page: for example, if you decline, if canopy does not recognise your account (in which case you can create a no-user-visit token instead), or if canopy is temporarily unreachable.

**Token lifetime** defaults to **1 day** for new tokens. You can type any value up to 365 days when creating a token. When you rotate a token, you can set a new lifetime the same way.

!!! note "Tokens you already have keep working"
    This confirmation step applies only when creating or rotating a token. Using an existing token is unchanged.

### Access levels

When you create a token you choose an **Access** level:

| Access level | What it can do |
| --- | --- |
| **Standard** | Full access matching your Connect permissions, including reading visit data and making workflow edits. Requires passkey confirmation through canopy to create or rotate. |
| **No user visit data** | Never sees user visit data. Reads workflow definitions, CommCare app structure, pipeline and indicator definitions, solicitations, funds, the organisation directory, targeting data, and microplan sampling. Can edit workflow and indicator definitions (including a workflow's opportunity list; render code only on synthetic opportunities whose data was generated, because render code runs over the visit data of whoever opens the page) and trigger saved-run generation — snapshots, history rebuilds, cache warms, hand-downs and benchmark publication — where the server computes over visits and the token gets back only ids, versions, dates and counts. Cannot delete workflows or pipelines. Can also profile real opportunities on the server and generate synthetic data from those profiles. Same permissions as the `mcp/no_user_visit/` address. Cannot access individual visit rows or per-visit values — including through the export API and data reseed endpoints — except on synthetic opportunities whose data was generated. Opportunity-level counts and dates, and contact details of people who submitted as an organisation, are allowed. No passkey confirmation required. |
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

The restricted address offers a simpler set of synthetic tools: `synthetic_clone_opp` (plus status checking, fidelity scoring, demo environments, and visibility) rather than the full set of step-by-step tools available on the full-access address. That one flow is all you need — see [Synthetic Data](synthetic-data.md).

!!! note "No change for full-access callers"
    If you use the main `/mcp/` address, none of the above restrictions apply to you. The only change that affects everyone is described in the pipeline export name note below.

---

## Profiling Limits

**There is no per-person limit** on how many jobs you start, or on how many opportunities you profile or clone in one call. The caps that used to apply were removed once each job began running on a machine of its own.

What still bounds the work:

| Limit | Value | Who it applies to |
| --- | --- | --- |
| Synthetic jobs running at the same time across the whole system | 6 | Everyone |
| Time limit per job, after which it is stopped and marked failed | 6 hours | Everyone |

Jobs over the system-wide limit wait their turn rather than being refused, so a large batch is slower, not rejected.

If you submit a request that is identical to one already running, Labs returns the existing job rather than starting a duplicate.

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

Tools that return a table (`data_query`, `workflow_run_indicators`) keep their rows in Labs for one day only when they will be read back: always through canopy, and for any other client only when you pass `keep_result: true` (otherwise you get every row in the answer and Labs keeps no copy). A result a card draws (`targeting_pmc_states`, public data) is always kept. A kept result carries a link to it, `result_uri`, which looks like `labs-result://<id>`. The same link is included as an MCP `resource_link`.

Any MCP client can read the link back with the standard `resources/read` request. That includes Claude Code, ACE, and canopy. Labs serves it as you, one page at a time:

- **Paging.** Add `?page=2&page_size=200` to the link. A page holds 100 rows unless you ask otherwise, and at most 1,000.
- **Sorting.** Add `&sort=<column>&dir=desc` to sort the whole result before it is paged. The column must be one of the result's columns.
- **Who can read it.** You can, and so can anyone who could run the same thing themselves (for example, someone with access to every opportunity the query read). Anyone else is refused with `forbidden`.
- **What it shows.** Each read returns exactly the rows the tool produced. A result is never re-run, so the numbers cannot change between reads.
- **For a day.** After a day the link returns `expired` and the rows are gone. Run the tool again to get a fresh result.

---

## Related

- [Synthetic Data](synthetic-data.md) — fake, labs-only copies of real opportunities, and the one request that makes them
- [Reports with Claude](reports-with-claude.md) — building and changing reports with Claude
- [Data Spaces and Raw Data](data-spaces.md) — the Data page and the tools Claude reads it with
