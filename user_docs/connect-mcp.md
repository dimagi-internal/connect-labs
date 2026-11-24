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
| `https://labs.connect.dimagi.com/mcp/no_user_visit/` (**default**) | Never shows real user visit data. Can read programme structure (workflows, pipelines, indicator registries, app structure, solicitations and their responses, org directory, targeting), can edit workflow and indicator definitions and trigger saved-run generation, and can profile real opportunities and generate synthetic data. Tools that read visits work only on synthetic opportunities whose data was generated. |
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
- **Write workflows.** It can create, clone and edit workflows on any opportunity it has access to, real ones included: their configuration, opportunity list, render code, pipelines and template workflows. It can also create and edit indicator registries. Writing a workflow isn't reading its data. Render code runs over visits in the browser of whoever opens the page, with that person's access, and the token sees none of it. (`workflow_get` leaves out the one setting the page itself writes as people use it, `config.audit_batch`.)
- **Build and edit CommCare project pages.** It can create and update dashboard pages for a CommCare project — tiles, tables and charts drawn from that project's forms and cases. Pages live under `/commcare/` and can be embedded inside CommCare HQ. Anyone in the project can open or edit a page; only its author or a project admin can delete it. A page can also keep **saved runs**: use "Save a run now" above the page to freeze the data at that moment, including the period it covers. "Week 45" stays as it was even after the project syncs again. Saved runs are listed above the page; opening one draws the page from the frozen data.

    A CommCare project page can also use **indicator registries** as a data source. A registry saved with `commcare_registry_create` can be added to any page alongside the pipelines the registry names. The page receives one row per scope key and the full catalogue of titles, units and bands; a saved run freezes those too. Use `commcare_indicators` to evaluate a registry over the project's own pipelines on the spot and check how it looks before adding it to a page.

- **Share a personal page with everyone at Dimagi.** On **Your pages** (`/labs/p/me/`), or with the `personal_page_share` tool, add `dimagi` to the page's scopes. Anyone who signs in to Labs with a `@dimagi.com`, `@dimagi-ai.com` or `@dimagi-associate.com` address can then open the page. They can read it but cannot change it. Before this was available, a page could only be shared with named people or with the members of a program, opportunity or organisation the owner belongs to.
- **Set work going on the server.** Creating runs, saving snapshots, rebuilding a report's history, warming a visit cache, handing a report down and publishing a benchmark all work. For these the **server** reads the visits and keeps the result; what comes back to Claude is ids, versions, dates and counts.
- **Make synthetic data.** It can profile a real opportunity on the server and generate a synthetic copy from that profile — see [Synthetic Data](synthetic-data.md). Real rows are never copied anywhere during this process.
- **Import organisations from the LLO Directory sheet.** Dimagi members and platform support can run the marketplace import from the agent panel or any MCP client instead of waiting for the daily 04:20 automatic import. You can limit the import to **open rounds only** — rounds still taking applications — so a run takes a fraction of the time compared with re-reading all rounds. The result reports how many organisations and contacts were read from the sheet, how many submissions to each round matched an organisation, and how many submissions are still waiting for a verdict. It also lists each **unmatched submission** — the organisation name given, which form, which sheet row, when it was submitted, country and website — so the people adding organisations to the directory know exactly who is missing without cross-reading sheets. Organisation names that differ from the Organizations tab only by capital letters, accents or spacing are now joined to the correct organisation automatically; the result lists what was matched loosely so the sheet's spelling can be tidied later (the sheet itself is never rewritten). If a background job was queued but never started — for example, because a worker restarted at that moment — it is sent again within a few minutes rather than waiting to be marked as failed.
- **Check coverage from a CommCare project.** The Buildings reached coverage page and the `microplans_coverage_check` tool can read a campaign straight from a CommCare project (for example, Niger SMC ran on CommCare). On the page, **Data: A CommCare project** sits beside Connect opportunities and the CSV upload: pick one of your CommCare projects, optionally narrow to an app or form, and run either lens (buildings reached, population served). When a CommCare project is the data source, the coverage viewer loads facilities directly from CommCare's location hierarchy — you are not asked to supply a separate facility list. Each form records its facility as a CommCare location, and CommCare holds the name, type, parent and coordinates for each location; the page picks the location type that looks like the health facility for you. Locations that CommCare holds no coordinates for are counted and noted, but not drawn on the map. If you have not yet connected CommCare, a **Connect CommCare** link appears and brings you back to the page once you have. You stay signed in with Connect and connect CommCare alongside it; only projects CommCare lists for you can be read, and only as you. Nothing is stored; only counts and 1 km squares come
