# Workflow Engine

The Workflow Engine lets program managers view configurable dashboards that pull live data directly from CommCare. Each workflow displays field worker performance metrics and supports drill-down into individual records, status tracking, and filtering.

---

## How Data Flows

```mermaid
flowchart LR
    CC[CommCare\nForm Submissions] -->|Pipeline extracts\nand aggregates| P[Pipeline]
    P --> W[Workflow Dashboard]
    W -->|Interactive\nview| PM[Program Manager]
    PM -->|Status updates\nand notes| W
```

**Pipelines** define what data to pull from CommCare and how to aggregate it — counts, sums, most recent values, percentages, and more. **Workflows** define what to display and how users interact with it.

---

## Finding Your Workflows

Click **Workflows** in the top navigation. You'll see a list of all workflows configured for your program.

Each row shows:

- Workflow name and type
- Last run time and data freshness
- Current status
- A schedule badge (for example, **⏱ Weekly**) if the workflow is running on an automatic schedule

Click any workflow to open its dashboard.

### The PERIOD column

The **PERIOD** column in the workflow list shows the date window that was actually audited for each run. Once a run has fired and audited data, this reflects the real window that was processed — which may differ from the date range that was set when the run was first created (for example, when you used the generic **Create Run** button).

When the audited window differs from the original creation-time range, the column shows an **ⓘ info icon** next to the date. Hovering over or tapping the icon shows the original creation-time range for reference. If the two windows are the same, no icon appears.

This means the PERIOD column is the authoritative record of what was actually covered by a run, not just what was intended when it was set up.

### Data freshness on scheduled reports

Scheduled reports can display when their data was actually last synced — and every reader sees the same figure regardless of which device they are on or when they last refreshed their browser.

Previously, the only freshness signal a report could show came from the reader's own browser, so it described when *that browser* last pulled data rather than when the underlying data was refreshed. On a report like the Ward Progress Tracker, this meant a reader on a second device — or one who simply had not refreshed in a while — might see "18h 36m ago" even though the data had in fact been refreshed at 04:21 that morning.

Scheduled reports that display a "last synced" or "last refreshed" time now draw that figure from the schedule itself, so it reflects the real sync time and is consistent across all readers.

### Run failure reasons

If a scheduled or unattended run fails, the run now records the reason for the failure alongside the failed status. Previously, a failure was logged with no error message, making it impossible to diagnose the problem without accessing production logs. You can now see what went wrong directly on the run record, which makes it easier to decide whether to retry, adjust settings, or contact support.

### Deep-linking to a specific workflow card

If someone shares a direct link to a specific workflow card — for example, a URL ending in `#workflow-5110` — the page will smoothly scroll to that card and briefly highlight it so you can spot it immediately, even on a long list. This works the same way in both the program view and the opportunity view.

### Program-level vs. opportunity-level workflows

Workflows in Connect Labs are owned by either a **program** or a specific **opportunity**:

- **Program-owned workflows** are scoped directly to the program — they have no owning opportunity at all. They appear in the program view only, cover the program as a whole — for example, the Program Audit Creator and Program Audit Report — and do not appear under any individual opportunity. All operations on these workflows (opening them, creating a run, viewing a run page) work entirely within the program context; no opportunity is needed. When you open a program-owned workflow, it verifies your access at the program level and loads its pipeline data across all the opportunities the workflow spans — you do not need to select an individual opportunity first.
- **Opportunity-owned workflows** appear under their specific opportunity only. They will not appear in the program-level workflow list.

This means each workflow appears in exactly one place. If you cannot find a workflow you expect to see, check whether you are viewing the program level or the relevant opportunity level.

!!! note "The opp: badge is not shown in the program view"
    When you are browsing the program-level workflow list, opportunity identifiers are not displayed next to workflow names. Only workflows that are explicitly owned by the program appear there, so the badge carries no useful information at that level and is hidden to keep the list uncluttered.

!!! note "Creating a run from the program view"
    Clicking **Create Run** on a program-owned workflow works the same as creating a run from any other context. Because these workflows are genuinely scoped to the program rather than to any opportunity, Create Run resolves correctly from the program view with no extra steps required.

    If you have recently opened a per-opportunity run in another tab (for example, by clicking an "open run ↗" link"), that should no longer affect Create Run on program-owned workflows. The program view keeps the program context in place, so Create Run on a program-owned workflow will always create the run under the program — not under whichever opportunity you last visited. If you do see a "Workflow not found" error, try refreshing the program workflow list page and clicking Create Run again.

!!! note "Drilling into worker visits from a program-level report"
    When you open a worker's case in the worker review from a programme report created at program level, their visits now load correctly. Previously, this could show a "pipeline not found" error; this has been fixed and visit data loads as expected.

---

## Coaching Workers from a Run Page

When you ask the canopy agent to coach a field worker from a Labs run page, the agent can show a **coaching card** directly in the chat. The card displays:

- The worker's name
- The topics the coach will raise
- The first message the worker will receive
- A picture of the worker's figures

### Connecting Open Chat Studio before the card loads

Every coaching send — including test sends — runs on your own Open Chat Studio (OCS) connection. Because of this, the card checks your OCS connection before showing anything else. If you are not yet connected, the card shows only:

> **Connect Open Chat Studio first**

with a **Connect Open Chat Studio** button and an **I've connected — continue** link that rechecks the connection. The rest of the card appears once your connection is confirmed.

### Sending from the card

The card has one primary action and a quiet secondary link:

| Action | What it does |
|---|---|
| **Send to \<worker\>** | Sends the coaching message to the field worker |
| **Send a test to me instead** | Switches the card to test mode — enter your PersonalID username, then click **Send test** to preview and send in one step (Dimagi staff only) |

Leaving the card without clicking **Send to \<worker\>** simply means you are not ready yet — there is no separate "Not yet" button.

!!! note "You decide when coaching is sent — the agent never sends it automatically"
    Sending a coaching message is always your action, triggered by clicking **Send to \<worker\>** on the card. The agent will show you the briefing and tell you where to click — either **Send to \<worker\>** on the card in canopy, or **Start coaching** on the Labs run page itself — but it will never send on your behalf. If you prefer to act from the run page rather than the chat, the **Start coaching** button there works exactly as it always has.

!!! note "Sending the same coaching twice"
    If you need to send the same coaching to the same worker again within a short window, this now works. Previously, a repeat send within 15 minutes was refused and the card would loop on "That preview went stale". You can now resend deliberately without hitting that block.

!!! note "If a test send can't go through"
    If **Send test** cannot complete, the card now tells you why underneath the button instead of leaving the button stuck on "Sending…" indefinitely. The most common reason is that your Labs account is not connected to Open Chat Studio. In that case the card shows "Connect Open Chat Studio in Labs first, then Send test again" with a **Connect it** link. Follow that link, connect your account, then return to the card and try **Send test** again.

### How the worker's figures picture looks

The **"Your figures"** picture a worker receives as part of a coaching message is now drawn as a proper chart in Connect's own style. Each topic is shown with its label, its figure, and a bar. The bars are coloured to match Connect's standard status colours:

| Bar colour | Meaning |
|---|---|
| Red | Off target |
| Amber | On watch |
| Green | On target |

The chart uses Connect's typography and deep-purple title styling, so it looks consistent with the rest of the Connect experience rather than a plain data table.

This is the first stage of coaching pictures that can be shaped further — for example, to add anonymous peer comparisons or a trend line. Those capabilities will arrive in later updates.

---

## Pipeline Data Sources

Pipelines can pull data from CommCare form submissions or from external files such as Google Drive exports. The two source types work differently and have different access rules.

### Supply data as a pipeline source

Workflows can now use **supply data** as a first-class data source, alongside CommCare form submissions and Drive files. This means stock views — covering what field workers hold, what the stores contain, orders, tenders, and more — can be built into workflows and updated per programme or opportunity without a software deployment.

Supply pipelines read data with the same access rules as the Supply pages elsewhere in Connect Labs. A workflow reading supply data across several opportunities (or even across programmes) works the same way programme-level reports already do — it spans the opportunities you configure it for and shows only what you have permission to see.

The Supply Stock page links directly to any supply workflow that has been set up for your programme or opportunity.

### Multiple summaries from a single pipeline

A summary pipeline can now produce several different breakdowns in one pass rather than requiring a separate pipeline for each breakdown. For example, instead of one pipeline for totals by questionnaire, another for totals by question, another for totals by state, and so on, a single pipeline can declare all of those as named **groupings** and compute them all together from one read of the data.

Each grouping can also break down by several fields at once — for example, question × answer type × state — without needing a separate pipeline per combination.

When a dashboard reads a pipeline that uses this feature, it receives all the summaries together as a single list of rows. Each row identifies which grouping it belongs to, so the dashboard can separate them and display each breakdown in the right place.

**What this means in practice:**

- Dashboards that previously required many pipelines to cover different breakdowns can now be powered by far fewer.
- Figures are computed consistently because all breakdowns come from the same single read of the underlying data.
- Existing pipelines are unaffected — this is an optional capability available to new and updated pipeline configurations.

### Google Drive pipelines scoped to a whole program

A pipeline can be configured to read a Google Drive file that covers an **entire program** rather than one opportunity. The Connect Interviews dashboard — whose interview exports span every cohort — is the first dashboard to use this.

**How access works**

Only staff in the organisation that **manages the program** can see data from a program-scoped Drive pipeline. Staff whose organisation runs one of the program's opportunities (partner network organisations) cannot. This means one cohort's partner never sees another cohort's raw interview answers, even though the underlying file contains data for all cohorts.

**How data is read**

The Drive file is read **once for the whole program**. Before this change, a program dashboard spanning many opportunities would have counted every row in the file once per opportunity — potentially inflating every figure by the number of opportunities. With a program-scoped pipeline the file is processed a single time, so counts and aggregations are correct regardless of how many opportunities the program contains.

**How caching works**

Once a Drive file has been read, Connect Labs keeps the processed data in cache and reuses it until the underlying file in Drive changes — or for up to a week, whichever comes first. Previously, the cache expired every hour regardless of whether the file had changed, so the first person to open the dashboard after an idle hour would wait for a full rebuild. On the interview-classification dashboard that rebuild took around 16 minutes. Now, a cold load only happens when it needs to — when the file genuinely has new content — and subsequent opens are fast for everyone.

**Limiting which columns are read**

A Google Drive pipeline can now declare a list of the specific columns it actually uses. When a column list is present, Connect Labs keeps only those cells from each row and discards the rest before caching. This means a wide export file where the dashboard only reads a handful of columns loads and caches a fraction of the data compared to reading the whole file.

- **If you omit the column list**, every column in the file is kept — exactly as before. Existing pipelines are unaffected.
- **If a column list is present but a field in the pipeline tries to read a column that is not on the list**, the pipeline cannot be saved. The error message names every missing column and the field that tries to read it, so you know exactly what to add to the list (or correct in the pipeline). This prevents dashboards from silently showing empty values because a column was accidentally left off the list.

If you are setting up or editing a Google Drive pipeline and you want to use this feature, ask your program administrator or the person who configured the pipeline to add the relevant column names to the pipeline's data source settings.

**Setting one up**

Program-scoped Drive pipelines are built through the labs MCP. The steps are:

1. Create the pipeline at the **program** level (not under an individual opportunity).
2. Preview the pipeline to confirm the data looks correct.
3. Create a program-owned dashboard and attach the pipeline.
4. Enable **load on demand** if you want the data to refresh only when a user opens the dashboard, rather than on a fixed schedule.

!!! note "Program-scoped Drive pipelines are separate from opportunity-level Drive pipelines"
    If your program already has Drive pipelines attached to individual opportunities, those are unaffected. A program-scoped pipeline is a distinct configuration that sits at the program level and is subject to the managing-organisation access restriction described above.

---

## Built-in Workflow Templates

### Supply Stock Review

The **Supply Stock Review** is a ready-made workflow template for monitoring field worker stock. You can enable it for the opportunities you choose without any software deployment. It reads supply data using the same access rules as the Supply pages, and can span several opportunities — or several programmes — the same way a programme-level report does. The Supply Stock page links to it directly once it is set up.

The dashboard is organised into the following views:

**Headline figures**
A summary of what is currently with field workers, expressed in sachets and in children's courses; how much has been given out (with the share that sits on visits not yet approved broken out separately); and the daily rate at which stock is going out.

**Runway**
One bar per worker, sorted so the worker who will run out soonest appears first. Each bar shows the worker's projected run-out date based on their own pace over the **last 14 days**, their most recent count against the ledger, and a short label confirming the window used — for example, "last 14 days" or "last 9 days" for a worker who started recently. A worker who has nothing left and has given nothing out in the last 14 days is shown as stocked out rather than as unknown.

**Does it add up?**
A reconciliation table showing, for each worker: stock issued, stock given out, what the ledger says they hold, what they actually counted, the gap between the ledger and the count, visits that did not record a stock figure, and the share of transactions sitting on unapproved visits.

**A worker's history**
Click any worker to open a day-by-day view of their stock movements and counts. The pace shown here — days to stock-out and sachets per day — is based on the **last 14 days** and is labelled accordingly, for example "5 sachets a day · last 14 days".

**The stores behind them**
Stock held in the stores that supply the workers, shown in sachets with cartons alongside. Store pace figures use the **last 90 days**, because store demand arrives in larger, less frequent collections such as monthly distributions. Their labels read "last 90 days".

!!! note "One pace figure per worker, everywhere"
    Every page in the Supply Stock Review — the Runway view, the Workers list, and an individual worker's page — now uses the same 14-day window to calculate a field worker's pace. Previously, the Stock review used 14 days while the Workers list and a worker's page used up to 90, so the same worker could show different "days left" figures depending on which tab you were on. The figures are now consistent across all views.

#### Viewing past dates in the Supply Stock Review

When you pick a past date using the **View as of** control in the supply header, the Stock review and all related pages — Stock, Movements, Workers, individual worker, Network, and "Where it went" — show the stock as it actually stood on that day. This includes visits that were made on or before the chosen date, even if they were synced to the
