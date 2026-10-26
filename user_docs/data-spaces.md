# Data Spaces and the Data Behind a Report

The **Data page** at [labs.connect.dimagi.com/labs/data/](https://labs.connect.dimagi.com/labs/data/) is where you look at the raw data behind Labs: every Connect visit you have access to, down to the submitted form, and the rows a workflow's pipelines produced. You query it in SQL, or ask the assistant in the panel to write the SQL for you.

A **Data Space** is a named, saved set of sources on that page (opportunities, programmes and optionally one workflow), together with the queries, charts and small reports a team builds on them. Anyone who holds the same data sources can open the space and pick up where you left off.

!!! info "Who this is for"
    Anyone who wants to check a number on a dashboard against the visits behind it, explore an opportunity's data
    without waiting for a report, or keep a set of useful queries where their team can find them.

!!! warning "New, and still changing"
    The Data page arrived on 22 October 2026 and Data Spaces on 24 October 2026. Both are changing daily, so expect
    some rough edges. Post in **#connect-labs** when something doesn't behave as this page says.

---

## What you can see

When you choose your sources, the page gives you these tables to query:

| Table | What it holds | When it appears |
| --- | --- | --- |
| `visits` | One row per Connect visit, with every visit column and the full submitted form (`form_json`) | Always, for the opportunities you chose |
| `p_<alias>` | One table per pipeline of the workflow you chose (`p_approved_visits`, `p_flw_summary`). These are the **same stored rows the workflow's dashboard draws from** | When you choose a workflow |

The left rail lists each table and its columns (click a name to put it into the editor), along with the form fields of the chosen opportunities, how often each is filled in, and the values each categorical field takes.

### Checking a dashboard number

This is what the pipeline tables are for. Say a Ward Progress Tracker shows a work area as "Unvisited" and you want to know why. Choose the workflow on the Data page and compare what the pipeline kept with the raw visits:

```sql
-- What the dashboard's approved_visits pipeline kept, by visit status
select status, count(*) as visits
from p_approved_visits
group by status
order by visits desc
```

```sql
-- The raw visits for the same opportunities, for comparison
select status, count(*) as visits
from visits
group by status
order by visits desc
```

If the two disagree, the pipeline is filtering something out. That is often deliberate (a pipeline that keeps approved visits only), and sometimes it is the bug you were looking for.

### Which pipelines become tables

Not all of them. **Only pipelines computed from Connect visits get a `p_` table**, because their rows come from visits you can already read in `visits`. Pipelines over other sources (CommCare cases, a Google Drive sheet, or Connect's own exports such as work areas, implementation areas or user data) have access rules of their own, so they are listed but cannot be queried here.

For example, on the Ward Progress Tracker (workflow 13005), `approved_visits` is built from visits and can be queried as `p_approved_visits`. `wa_geo` (work-area geometry), `implementation_areas` and `flw_enroll` come from other Connect exports.

### Raw data vs. the SQL that produced it

The Data page shows a pipeline's **output**, not the SQL that built it. To see that SQL:

- open `https://labs.connect.dimagi.com/labs/workflow/api/pipeline/<pipeline id>/sql/?opportunity_id=<the pipeline's opportunity>` while logged in, or
- ask Claude: *"Show me the SQL for pipeline 20908 on opportunity 2156."* (the `pipeline_sql` tool).

To find a workflow's pipeline IDs, and the opportunity each one belongs to, ask Claude: *"Which pipelines does workflow 13005 use?"* (`workflow_get`). Each pipeline belongs to one opportunity, which is not always the first opportunity of the workflow.

!!! note "Some reports have no pipelines"
    Not every report is built on pipelines. The **Supply Stock Forecast**, for example, is calculated in Python from
    the supply-chain records (visits recorded per worker, stock movements, the dispensing rule and the stock network)
    and has no SQL to inspect. For a report like that, ask Claude for its inputs with the `supply_chain_*` tools,
    or open the JSON the page loads (`/labs/workflow/api/<workflow id>/supply-data/?opportunity_id=<opp>`).

---

## How to use it

### 1. Open the Data page and choose your sources

1. Go to [labs.connect.dimagi.com/labs/data/](https://labs.connect.dimagi.com/labs/data/).
2. Choose **programmes, opportunities or a workflow**. To query a workflow's pipelines, click **Use a workflow…** and pick it. Only workflows that have at least one run are offered, because a workflow with no run has no stored pipeline rows.
3. The left rail fills with the tables you can query and their columns.

You can only choose sources you currently have access to. For a workflow, that means **every** opportunity it covers.

### 2. Query, or ask the assistant

- **Write SQL** in the editor on the canvas and click **Run** (⌘↵ / Ctrl+↵). Set **max rows** to limit what comes back, and use **Download CSV** to take the result away.
- **Ask the assistant** in the panel on the right, in plain words: *"How many approved visits per worker last week?"*, *"Which work areas have visits but none approved?"* It writes the SQL, runs it on the page **as you**, and opens the result in a new canvas tab. The assistant can only read the sources the page has selected.

The canvas keeps your last few results as tabs, so the assistant opening something new never loses your work.

### 3. Save it as a space

Until you save, you are working in an **Unsaved space: this selection**, and what you build lasts only for the session.

1. Click **Save as a space** and give it a name ("Jigawa CHC — ward coverage checks").
2. Save the queries worth keeping with **Save query**. Choose **Save to** the space (shared with the space) or keep it under **My queries** (only you). A query saved with a chart opens on that chart next time.
3. Save useful assistant conversations with **Save this conversation**. The space keeps a *pointer* to the conversation, never its words, and only a conversation started on this space's page can be saved to it.

Your space now appears in the **Space** switcher for everyone who can open it.

### 4. Share it (there's no share button)

A space doesn't need sharing. **Anyone who holds every one of the space's sources can open it**, checked every time they open it. That is the "auto-sync across anyone holding the same data sources" in the release notes.

- A teammate on the same opportunities sees the space, its saved queries and its charts.
- Someone missing **even one** of the space's sources cannot open it at all. There is no partial view, so everyone who opens a chart sees the same numbers.
- If someone loses access to an opportunity, they lose the space with it.

**No rows are stored.** A saved query keeps its SQL and its chart settings, and runs again, live, as whoever opens it.

---

## Managing a space

The person who creates a space is its first **manager**. Managers can:

- **Update sources…**: change what the space reads. Adding a source locks out everyone who doesn't hold it, and Labs cannot tell you in advance who that will be, so the page asks you to confirm. Only a person can change sources; the assistant never can.
- **Who can open**: **Anyone with access to the sources** (the default) or **Only managers**. This setting can only *narrow* who gets in. It can never let in someone who doesn't hold the data.
- **Managers**: add or remove other managers.
- **Archive space…**: it disappears for everyone who can open it.
- Delete any item in the space.

Everyone else who can open the space is a **member**: they can run everything, save new queries and new versions, and delete what they saved themselves.

### Programmes are frozen to their opportunities

When you add a **programme** as a source, the space records the opportunities it had at that moment. To open the space you need the programme **and** each of those opportunities. When the programme later gains opportunities, the space tells people who hold them, and a manager can **Update sources…** to include them.

---

## Troubleshooting

| What you see | Why, and what to do |
| --- | --- |
| A `p_` table is empty, or says its data has expired | A pipeline's stored rows last about an hour after the pipeline last ran, and it runs when the workflow's live page loads. Open the workflow's run page, then come back, or ask the assistant to rebuild it. |
| A pipeline you expected isn't a table | It isn't built from Connect visits (see [Which pipelines become tables](#which-pipelines-become-tables)). It's listed, but not queryable here. |
| A workflow isn't offered under **Use a workflow…** | It has no runs yet, or it covers an opportunity you don't hold. |
| A space a teammate mentions isn't in your switcher | You're missing at least one of its sources, or its managers set it to **Only managers**. A space you can't open isn't shown at all. |
| "Failed to load pipeline" on a workflow's edit page | On a programme-owned workflow, the edit page's pipeline tab looks every pipeline up under the workflow's first opportunity. That fails for pipelines that belong to another opportunity. Open the pipeline directly instead, at `/labs/workflow/pipeline/<id>/edit/?opportunity_id=<the pipeline's opportunity>`, or use the Data page. |
| `/labs/workflow/pipelines/` is empty | That list shows the pipelines of the opportunity or programme currently selected in your Labs context. A programme-owned workflow's pipelines usually belong to individual opportunities, so switch your context to one of those opportunities. |

---

## Related pages

- [Connect MCP](connect-mcp.md#data-formerly-the-sql-explorer): the Data page's layout and the tools Claude uses to read it.
- [Workflow Engine](workflow-engine.md): how workflows and their pipelines fit together.
- [Reports with Claude](reports-with-claude.md): building and changing reports by talking to Claude.
