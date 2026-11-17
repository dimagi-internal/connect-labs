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

You can also build dashboards over a CommCare project in the same way. A page can show tiles, tables and charts drawn from the project's forms and cases, opens at its own address under `/commcare/`, and can be embedded inside CommCare HQ. Anyone in the project can open and edit a page; only its author or a project admin can delete it. A page can also keep **saved runs** — snapshots that freeze the data at a point in time so a period like "week 45" stays as it was even after the project syncs again.

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

### What the sign-in page tells you

When you sign in, Labs shows you a page that explains exactly what the app is asking for before you approve. Every sign-in page says the same things in the same order:

- **What it can do as you** — whether it can see real visit data or only programme structure.
- **How long that access lasts** — the expiry date and time.
- **How to undo it** — where to go to disconnect the app.
- **Whether a passkey step comes next** — only for full-access sign-ins.

The headline reads "An app on this computer wants to use Connect Labs as you". The name the app gives itself appears beneath that, labelled as something the app claims — not something Labs has verified. Check the access level and expiry rather than relying on the name.

### What the permission prompt tells you

When an agent on a Labs page asks for your permission, the prompt names Connect Labs and explains each permission in plain English before you approve. For example:

- _"read a round's applicants' answers and the reviews written on them"_
- _"propose AI reviews and evaluation criteria for a round; nothing is saved until you confirm"_

The raw permission names appear underneath for reference. Read the plain-English lines to understand what the agent will be able to do — nothing happens until you confirm.

### 2. (Scripts and headless agents only) Register a Labs token

Anything that cannot open a browser to sign in — a script, a scheduled job, a headless agent — needs a Personal Access Token instead. From **inside your connect-labs checkout**, start Claude Code and run:

```
/labs-token-setup
```

When asked, choose **Production labs environment**, then approve the token in the browser. Fully quit and restart Claude Code afterwards. The token lasts 1 day by default; run the skill again to replace it, or set a longer lifetime (up to 365 days) on the tokens page when you approve it.

!!! note "Full-access tokens require a passkey confirmation"
    If you choose a full-access token during setup, a small canopy window opens over the page and asks you to touch your passkey. If you are not a member of Labs' canopy workspace, you can create a no-user-visit token only.

!!! tip "If you were not signed in when you opened the approval page"
    If Labs sent you to sign in before showing the approval page, the approve button and all links on the page now work correctly after you sign in. If you previously saw a "not found" page at that step, this has been fixed — run `/labs-token-setup` again and it will complete normally.

### 3. (Agents doing one job) Get a short-lived token

If you ask an agent to carry out a single task — rather than setting it up as a permanent assistant — it can request a token that lasts **one hour** and cannot renew itself. This leaves your own Claude Code setup untouched. The agent asks for the token, does the work, and the token expires on its own.

Full access (including real visit data) still requires your passkey, even for a one-hour token.

#### Agents not on your computer

An agent running in the cloud or on another machine can also request a one-hour token. Instead of opening a browser itself, it gives you a link. You open that link while signed in to Labs, and it takes you to the **MCP access page** ([labs.connect.dimagi.com/labs/mcp/tokens/](https://labs.connect.dimagi.com/labs/mcp/tokens/)) with the agent's request shown at the top. The request tells you:

- which Labs account the agent is acting as
- what the agent says it needs the access for
- the access level it is asking for (No user visit data or Full access) and that it lasts one hour
- **the name of the runner that made the request**, if the agent signed its request with a recognised runner key — this lets you confirm the request came from the agent you expect

Your active tokens and the token creation form are shown beneath the request. The **Decline** and **Continue** buttons are in the request panel at the top. Pressing **Continue** takes you to the same passkey step in canopy that a full-access token requires. This passkey step is required at both access levels when the token will leave Labs for another machine — the bar is higher than for a token that stays on your own computer.

If you approve, the agent collects the token on its own, and the new token appears in your active tokens list on the same page. If you decline, no token is created and no passkey is needed. You can revoke any token at any time from the same page.

!!! note "If canopy refuses the agent's request"
    If canopy cannot verify the agent's runner signature — for example, because the key is not recognised or has expired — the agent will now receive a clear explanation of why the request was refused, rather than a generic error. If this happens, contact your programme administrator to check the agent's credentials.

---

## MCP Access

The page where you manage sign-ins and tokens is called **MCP access** and is at [labs.connect.dimagi.com/labs/mcp/tokens/](https://labs.connect.dimagi.com/labs/mcp/tokens/). It explains the difference between signing an app in through the browser and using a Personal Access Token, and shows expiry dates including the time of day. When an agent requests a short-lived token, that request also appears at the top of this page for you to review and act on.

The `claude mcp add` command shown on that page is the correct one to use. Do not edit `~/.claude/mcp.json` directly — Claude Code does not read that file.

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
- **Build and edit CommCare project pages.** It can create and update dashboard pages for a CommCare project — tiles, tables and charts drawn from that project's forms and cases. Pages live under `/commcare/` and can be embedded inside CommCare HQ. Anyone in the project can open or edit a page; only its author or a project admin can delete it. A page can also keep **saved runs**: use "Save a run now" above the page to freeze the data at that moment, including the period it covers. "Week 45" stays as it was even after the project syncs again. Saved runs are listed above the page; opening one draws the page from the frozen data.
- **Set work going on the server.** Creating runs, saving snapshots, rebuilding a report's history, warming a visit cache, handing a report down and publishing a benchmark all work. For these the **server** reads the visits and keeps the result; what comes back to Claude is ids, versions, dates and counts.
- **Make synthetic data.** It can measure a real opportunity on the server and generate a synthetic copy from that measurement — see [Synthetic Data](synthetic-data.md).
- **Work with cost-effectiveness scenarios.** It can list and open saved scenarios and size one using `cea_scenarios`. It can also plan to a reach target, work at territory (state/province) level, and read place-level cost-effectiveness figures that account for local newborn mortality. When planning to a reach target it can apply plan limits (smallest place worth including, maximum number of countries), download a by-country summary as a CSV, and assess how the plan holds up if the place figures are only partially reliable. Each territory and country also shows a **Conflict** column — the reported conflict deaths per 100,000 people per year from the Uppsala Conflict Data Program (UCDP) — and a **Remoteness** column — the percentage of a place's people who live more than two hours' walk from care. A plan can be held to either or both of the following ceilings, each shown as its own column beside the multiple:

    - **"Most reported conflict deaths (per 100,000 a year)"** — leaves out places above the threshold and reports how many were excluded. A place Labs has no conflict figure for stays in the plan; a multiple is never changed by the conflict figure.
    - **"Most people beyond two hours' walk of care"** — leaves out places above the remoteness threshold and reports how many were excluded. A place Labs has no remoteness figure for stays in the plan; a multiple is never changed by the remoteness figure. Remoteness is calculated from the travel-time surface already in Labs (MAP × population), so no new data needs to be loaded.

    A Kangaroo Care plan also reports how many of its people are counted from a regional low-birthweight rate rather than a national one — UNICEF-WHO publish no national figure for sixteen African countries (Nigeria and Ethiopia among them), so those countries use their subregion's rate instead. The plan summary says how many people and how many places rest on a regional rate, lists which countries are affected, and each row in the plan carries a flag where the regional rate applies. Saving a scenario with `cea_scenario_save` requires a full-access token, and now saves the **complete plan** — not only the selected places, bar, dials and inputs, but also the grain (territory or country level), the reach target, any plan limits and ceilings, the price basis, the trust setting and the place-scaled-risk switch (including when that switch is off). A scenario you reload therefore reopens as the same plan you built, with all its choices intact. Saved scenarios can be renamed, shared with others, or made private again from the scenario list without saving again.

    **Scenario groups** let you keep a set of scenarios together under the question they are all answers to. When you open a saved scenario, a panel above its numbers shows the group it belongs to ("scenario 2 of 6", with links to the scenario either side), a note on that scenario, what it sets differently from the model as published, and a link to compare all scenarios in the group. A new **Scenario groups** tab lists every group; opening a group sets its scenarios side by side so you can see:

    - **What each sets** — only what differs from the published model, with differing settings shaded so the choices that actually vary stand out.
    - **What each gave** — deaths averted with its redraw range, cost, cost per death averted, the multiple, people, places, countries, steadiness, and the people resting on a regional low-birthweight rate or on births counted far from the UN figure; the best result in each column is shown in green, and each column shows the change against a reference scenario you can switch.
    - **What each changes from the scenario it was built from**, written in plain words.
    - **What has moved since it was baselined** — if the underlying data has changed since you last saved the
