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

---

## MCP Access

The page where you manage sign-ins and tokens is called **MCP access** and is at [labs.connect.dimagi.com/labs/mcp/tokens/](https://labs.connect.dimagi.com/labs/mcp/tokens/). It explains the difference between signing an app in through the browser and using a Personal Access Token, and shows expiry dates including the time of day.

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
- **Set work going on the server.** Creating runs, saving snapshots, rebuilding a report's history, warming a visit cache, handing a report down and publishing a benchmark all work. For these the **server** reads the visits and keeps the result; what comes back to Claude is ids, versions, dates and counts.
- **Make synthetic data.** It can measure a real opportunity on the server and generate a synthetic copy from that measurement — see [Synthetic Data](synthetic-data.md).

!!! note "Counts and dates are allowed"
    Opportunity-level counts and dates are on the allowed side of the line, as are the contact details of people who submitted something as an organisation. The line is drawn at an individual visit and its values.

!!! warning "One gap worth knowing about"
    When work set going on the server fails — a cache warm, a history rebuild, a hand-down — the error message comes back to Claude. Those are configuration and infrastructure messages, but a pipeline that choked on a value can quote that value in its error. It is the one way a restricted caller can see something drawn from a real visit.

---

## Checking Which Address You Are On

**Either the address or the credential is enough to make a call restricted** — a full-access token pointed at `mcp/no_user_visit/` is still a restricted call, and a no-user-visit token is restricted even on the full address. The address is simply the easiest of the two to check, so start there.

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

## Advanced

Everything below is reference material: tokens, the exact rules of the restricted
address, job limits and how results are kept. Most people will not need it.

## Personal Access Tokens

Personal Access Tokens let you connect an MCP client to Labs without going through the browser sign-in flow — a script, a scheduled job or any headless agent that cannot open a browser. You create and manage them at [labs.connect.dimagi.com/labs/mcp/tokens/](https://labs.connect.dimagi.com/labs/mcp/tokens/).

### Creating and rotating tokens

**No-user-visit tokens** can be created and rotated by you directly on the tokens page, with no extra step.

**Full-access (Standard) tokens** require a passkey confirmation through canopy. When you create or rotate a Standard token, a small canopy window opens over the tokens page, you touch your passkey, and the token appears — you stay on the same page throughout. If the window cannot open, a link lets you approve in canopy instead. Every outcome shows a message at the top of the page: for example, if you decline, if canopy does not recognise your account (in which case you can create a no-user-visit token instead), or if canopy is temporarily unreachable.

**Token lifetime** defaults to **1 day** for new tokens. You can type any value up to 365 days when creating a token. Agents doing a single job can request a token that lasts **one hour** and cannot renew — this leaves your own setup untouched and expires automatically when the job is done. When you rotate a token, you can set a new lifetime the same way.

**Expiry dates** on the tokens page include the time of day, so you can tell exactly when a token will stop working.

!!! note "Tokens you already have keep working"
    This confirmation step applies only when creating or rotating a token. Using an existing token is unchanged.

### Creating and rotating tokens via `labs-token-setup`

When `/labs-token-setup` opens a browser to approve a token, it uses the same approval page as the tokens page at `/labs/mcp/tokens/`. The approve button and all links on that page work correctly, including when you were not already signed in and Labs redirected you to sign in first. If you previously encountered a "not found" page after signing in at that step, this has been fixed — run `/labs-token-setup` again and it will complete normally.

### Access levels

When you create a token you choose an **Access** level:

| Access level | What it can do |
| --- | --- |
| **Standard** | Full access matching your Connect permissions, including reading visit data and making workflow edits. Requires passkey confirmation through canopy to create or rotate. |
| **No user visit data** | Never sees user visit data. Reads workflow definitions, CommCare app structure, pipeline and indicator definitions, solicitations, funds, the organisation directory, targeting data, and microplan sampling. Can edit workflow and indicator definitions (including a workflow's opportunity list; render code only on synthetic opportunities whose data was generated, because render code runs over the visit data of whoever opens the page) and trigger saved-run generation — snapshots, history rebuilds, cache warms, hand-downs and benchmark publication — where the server computes over visits and the token gets back only ids, versions, dates and counts. Cannot delete workflows or pipelines. Can also profile real opportunities on the server and generate synthetic data from those profiles. Same permissions as the `mcp/no_user_visit/` address. Cannot access individual visit rows or per-visit values — including through the export API and data reseed endpoints — except on synthetic opportunities whose data was generated. Opportunity-level counts and dates, and contact details of people who submitted as an organisation, are allowed. No passkey confirmation required. |
| **Coaching pictures only** | Lets an Open Chat Studio team fetch coaching pictures. Nothing else. Cannot be used for MCP or the export API. |

Tools the token is not allowed to use do not appear to the agent at all, so the agent
