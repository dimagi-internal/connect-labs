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

### Case coaching — talking to a worker about one baby

In addition to coaching on indicator scores, the agent can now coach a field worker about a **specific baby**. Labs reviews each baby's recent visits and identifies one of four stories that best describes their situation. Every figure in the coaching comes from that baby's own visits.

#### The four stories

| Story | What it means |
|---|---|
| **Danger sign recorded — not referred** | A danger sign was recorded for the baby but the baby was not referred for follow-up care |
| **Weighing to check** | A recorded weight is hard to believe and should be verified |
| **Weight has stalled** | The baby's weight has stopped growing while skin-to-skin time is also falling |
| **Growing well** | The baby is growing healthily — an opportunity to recognise the worker's good practice |

Each baby gets one story, and Labs only surfaces babies whose story happened recently — within 30 days of the baby's latest visit.

#### How the briefing is built

When the agent prepares a case coaching conversation, the briefing it shows you comes directly from the **case state** recorded in the registry for that baby. Each case state carries its own coaching guidance, which appears in the briefing immediately after the topic name:

- **What it means** — a plain-language description of the baby's situation
- **How to talk about it** — suggested language and tone for the conversation
- **The step to agree** — the specific action the worker and coach should agree on
- **What it does not tell you** — important caveats to keep in mind

This means the briefing reflects whatever guidance has been configured for that case state in the registry, rather than fixed text. If the guidance for a state is updated in the registry, future briefings will pick up the change automatically.

The picture the worker receives is drawn using the picture type that the case state names — so different states can produce different picture styles without any further configuration in Labs.

#### How the agent suggests a case coaching conversation

For each worker on an opportunity, Labs lists — per story — the babies that are currently eligible. The canopy agent panel uses this list to suggest one conversation per worker. You review the suggestion and approve it before anything is sent.

!!! note "Saved run weeks record eligible cases"
    Each saved week of the KMC Opportunity Report records which babies were eligible for which story. This means the agent can see what earlier weeks offered, so suggestions take previous opportunities into account.

#### Starting a test case coaching conversation from the worker review

The KMC Worker Review's case panel includes a **Coach about this baby** button. This lets you start a test coaching conversation about one specific baby directly from the baby's case panel, without going through the canopy agent.

The button appears when two things are true:

- The workflow has a coaching action set up.
- The baby's case is in a recognised case state in the registry.

Clicking the button opens the coaching dialog, which shows:

- The picture the coach will send
- The baby's story with its supporting facts
- The first message the worker would receive
- The exact briefing

**Test sends only.** The only option available from this button is **Send to me (QA test)** — the conversation goes to your own phone. There is no option to send to the worker from this button. Labs remembers your PersonalID username in your browser, so after your first test send, subsequent sends need just one click.

#### Case coaching pictures

Each story has its own picture designed to be read on a phone. The picture type is determined by the case state recorded in the registry for that baby:

| Story | Picture |
|---|---|
| **Growing well** | The baby's weight line climbing above the healthy-growth band, with a "Great work!" badge |
| **Weighing to check** | The doubtful weighing circled on the growth line, with a four-step weighing checklist |
| **Weight has stalled** | A flat growth line, with skin-to-skin hours per visit shown as bars |
| **Danger sign** | A card listing the signs recorded, "Not referred", and what to do next |

!!! note "Sending case coaching on synthetic KMC opportunities"
    On synthetic KMC opportunities used for testing, the coaching card offers only **Send to me (QA test)** — messages cannot be sent to real workers from these opportunities.

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

!!! note "If Open Chat Studio refuses a coaching send"
    When Open Chat Studio refuses to start a coaching conversation, the run now records the specific reason that Open Chat Studio returned, rather than a generic refusal message. The card and the agent will show you that reason directly — for example: "Open Chat Studio refused the request: Failed to create channel: Participant not found in CommCare Connect". This makes it much easier to understand what went wrong and whether you need to take action (such as checking that the worker is registered in CommCare Connect) or contact support.

### Choosing what the worker's picture shows

When preparing coaching — either through the canopy agent card or via **Start coaching** on the run page — you can now choose what chart the worker receives in their picture. The options are:

| Picture option | What the worker sees |
|---|---|
| **Their own figures** | The worker's results for the topics being coached — the same chart style as today |
| **Peer comparison** | How the worker's figures compare with others on the same run; other workers appear only as "Peer A", "Peer B", and so on — never by name |
| **Week by week** | The worker's figures broken down week by week, so they can see how their performance has changed over time |
| **Custom chart** | A chart of your own design |

Every number in the picture comes from Labs, and every picture is drawn in Connect's style regardless of which option you choose.

The picture shown on the card is exactly what the worker will receive — it is not recalculated or regraded after you approve it. What you see is what they get.

!!! note "Worker names must not appear in coaching notes"
    A coaching note that names another worker will be refused. Workers must only ever see peers anonymously. If your note includes another worker's name, remove it and try again.

### How the worker's figures picture looks

The **"Your figures"** picture a worker receives as part of a coaching message is now drawn as a proper chart in Connect's own style. Each topic is shown with its label, its figure, and a bar. The bars are coloured to match Connect's standard status colours:

| Bar colour | Meaning |
|---|---|
| Red | Off target |
| Amber | On watch |
| Green | On target |

The chart uses Connect's typography and deep-purple title styling, so it looks consistent with the rest of the Connect experience rather than a plain data table.

This is the first stage of coaching pictures that can be shaped further — for example, to add anonymous peer comparisons or a trend line. Those capabilities will arrive in later updates.

### How peer labels appear on the Week by week chart

In the **Week by week** picture, each peer is shown as a line with a label at its right-hand end. These end labels are arranged so they are always readable:

- **No overlapping labels.** When two or more peers finish the period at the same figure, their names are combined into a single label — for example, **Peer A, B** — rather than stacking on top of one another.
- **Always spaced apart.** Labels that finish close to each other are nudged up or down so there is always at least one line of space between them.
- **Always inside the chart.** No label slips below the bottom edge or above the top edge of the plot area.
- **The worker's own label stays bold.** **You** always appears in bold, as it does elsewhere in the picture, so the worker can immediately pick out their own line.

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
