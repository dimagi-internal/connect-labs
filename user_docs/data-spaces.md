# Data Spaces and the Data Behind a Report

The **Data page** at [labs.connect.dimagi.com/labs/data/](https://labs.connect.dimagi.com/labs/data/) is where you can look at the raw data behind a Labs Workflow.  You can view every Connect visit you have access to down to the submitted form, and the rows a workflow's pipelines produced. You query it in SQL, or ask the assistant in the panel to write the SQL for you.

A **Data Space** is a SQL workbench over raw Connect visit data. It has a table called visits with every column plus the full submitted form, a list of the form fields, and a SQL editor to display results.

!!! info "Who this is for"
    Anyone who wants to check a number on a dashboard against the visits behind it, explore an opportunity's data, or keep a set of useful queries where their team can find them.

!!! warning "New, and still changing"
    The Data and Data Spaces on 24 October 2026 are new and changing daily.  Expect rough edges. Post in **#connect-labs** when something doesn't behave as this page says.

---

## What you can see

When you choose your sources, the page gives you these tables to query:

| Table | What it holds | When it appears |
| --- | --- | --- |
| `visits` | One row per Connect visit, with every visit column and the full submitted form (`form_json`) | Always, for the opportunities you chose |
| `p_<alias>` | One table per visit-based pipeline of the workflow you chose (`p_approved_visits`, `p_flw_summary`; see [Which pipelines become tables](#which-pipelines-become-tables)). These are the **same stored rows the workflow's dashboard draws from** | When you choose a workflow |

The left rail lists each table and its columns (click a name to put it into the editor), along with the form fields of the chosen opportunities, how often each is filled in, and the values each categorical field takes.

---

## How to use it

### 1. Open the Data page and choose your sources

1. Go to [labs.connect.dimagi.com/labs/data/](https://labs.connect.dimagi.com/labs/data/).
2. Choose **programmes, opportunities or a workflow**. To query a workflow's pipelines, click **Use a workflow…** and pick it. Only workflows that have at least one run are offered, because a workflow with no run has no stored pipeline rows.
3. The left rail fills with the tables you can query and their columns.

You can only choose sources you currently have access to. For a workflow, that means **every** opportunity it covers.

### 2. Query, or ask the assistant

- **Write SQL** in the editor on the canvas and click **Run** (⌘↵ / Ctrl+↵). Set **max rows** to limit what comes back, and use **Download CSV** to take the result away.
- **Ask the assistant** in the panel on the right, in plain words: *"How many approved visits per worker last week?"*, *"Which work areas have visits but none approved?"* It writes the SQL, runs it on the page **as you**, and opens the result in a new canvas tab. The assistant, once you enable it for your user in Canopy, uses your credentials to read the sources the page has selected leverages using a Zero Data Retention key.

The canvas keeps your last few results as tabs, so the assistant opening something new never loses your work.

### 3. Save it as a space

Until you save, you are working in an **Unsaved space: this selection**, and what you build lasts only for the session.

1. Click **Save as a space** and give it a name ("Jigawa CHC — ward coverage checks").
2. Save the queries worth keeping with **Save query**. Choose **Save to** the space (shared with the space) or keep it under **My queries** (only you). A query saved with a chart opens on that chart next time.
3. Save useful assistant conversations with **Save this conversation**. The space keeps a *pointer* to the conversation, never its words, and only a conversation started on this space's page can be saved to it.

Your space now appears in the **Space** switcher for everyone who can open it.

### 4. Share it (there's no share button)

A space doesn't need sharing. **By default, anyone who holds every one of the space's sources can open it**. This is checked every time they open it (its managers can narrow this to **Only managers**; see [Managing a space](#managing-a-space)).

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

## Data Space vs Pipeline Tables

A **pipeline** is a saved recipe that turns raw submissions into the tidy rows a report reads — which source, which answers to pull out of the form, and what one row means. [What a pipeline is](workflow-engine.md#what-a-pipeline-is) explains it in full.

The Data page and the pipeline editor are two views of that recipe. The editor shows the **definition** and lets you change it. The Data page shows its **output** (`p_<alias>`) and changes nothing.

| You want to… | Go to |
| --- | --- |
| Find out why a dashboard number looks wrong | **Data page** — compare the `p_` table with `visits` |
| Find the right form field, and how often it's filled in | **Data page** — the left rail lists every field and its fill rate |
| Answer a one-off question, or save a query or chart for your team | **Data page / a Data Space** |
| Add a column to a report, fix a wrong question path, change a filter or an aggregation | **[Pipeline editor](workflow-engine.md#where-to-edit-one)**, or ask Claude |
| See the SQL a pipeline generates | [Raw data vs. the SQL that produced it](#raw-data-vs-the-sql-that-produced-it), below |
| Change what a number *means* | Neither — [the indicator definitions](semantic-layer.md) |

The usual order is both: explore here until you know which field you want, then change the pipeline.

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

If the two disagree, the pipeline is changing the data on its way to the dashboard: filtering visits out, or joining, de-duplicating or aggregating rows. That is often deliberate (a pipeline that keeps approved visits only, or counts per work area), and sometimes it is the bug you were looking for.

### Which pipelines become tables

**Only pipelines computed from Connect visits get a `p_` table**, because their rows come from visits you can already read in `visits`. Pipelines over other sources (CommCare cases, a Google Drive sheet, or Connect's own exports such as work areas, implementation areas or user data) have access rules of their own, so they are listed but cannot be queried here.

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

## Sharing data outside Dimagi: the dictionary and de-identified exports

A saved space has a **Data dictionary and de-identified exports** page. The link is under the space's name. Use it when a programme's visit data has to go to someone outside Dimagi, such as evaluators or researchers, without anything that identifies a person.

### The dictionary

The dictionary lists every field the space's visits carry. That covers each form question and each column of `visits`. For each field it shows:

- **Kind**: what the values look like. A name, phone or address shows as *identifier*; also *free text*, *gps*, *case id*, *date*, *number* or *categorical*.
- **Meaning**: what the question asks, in your words, per app version where that differs.
- **Level**: whether the field is about the visit or the case.
- **Setting**: what an export does with the field.

| Setting | What it does |
| --- | --- |
| Keep as is | The value goes out unchanged |
| Drop | Left out |
| Round | Rounded to a step you choose, e.g. weights to 25 g |
| Replace with a code | The same id always gets the same code, in this export and the next. A code can't be turned back into the id. Every case-id field shares one set of codes, so visits still join up by case. |
| Month only | A date cut to the month (the default for dates of birth) |
| Shift dates per case | Every date of one case moves by the same hidden number of days, so intervals within a case stay true |
| Coarsen location | A GPS point rounded, by default to about 1 km |
| Scrub free text | Emails, long numbers and known names removed. This is a best guess, and a note can still identify someone. |

**Nothing is exported until a manager of the space has reviewed it.** Each field arrives with a suggested setting (identifiers, free text and GPS are dropped, ids are coded, dates and numbers are kept). It stays out of every export, marked *Dropped until reviewed*, until a manager accepts that suggestion or picks another. A field whose kind is *identifier* can never be kept as is.

### Scanning for fields and app changes

Press **Scan for fields and app changes**. Labs reads the structure of the space's loaded visits: which questions were answered, under which form, in which app build. It stores no values. The scan:

- adds every new field, unreviewed (so dropped)
- puts one question spelled two ways into the same field, such as `Lethargy` in one app version and `lethargy` in the next
- flags a field the newest build of a form no longer sends, and a field whose values now look more sensitive than before

Run it whenever a new app version goes out. When an app *renames* a question, rather than just changing its capitals, select both fields and press **Merge selected**. The merged field takes the stricter of the two settings and needs reviewing again.

### Asking for an export

Pick a date window (or none) and press **Ask for an export**. Anyone who can open the space can ask for one.

1. **It refuses unknown fields.** If the visits in the window carry a field the dictionary doesn't know, the export stops and names it. Scan, review the field, then ask again.
2. **It applies the settings to every visit in the window.** There is no 5,000-row limit.
3. **It checks its own output** before anyone can see it:
   - rows against visits
   - a search of every cell for the names, usernames, phone numbers, ids and GPS points of the people in the source
   - numbers far outside their usual range
   - duplicates
   - visits with no case
   - how many rows share their dates, worker and similar fields with fewer than 5 cases (fields that could single someone out together)

   A failed check blocks the export, and it never produces a file.
4. **Someone else signs it off.** A manager of the space who did *not* ask for it signs it off. They write who it is for and why, and confirm they have read any warnings.
5. **Download.** The person who asked, or a manager, can then download it for **7 days**, after which the file is deleted.

The download is a zip of four files:
- `data.csv`
- `dictionary.csv`: every column's meaning, setting and source form paths. It never holds a sample value.
- `README.md`: the caveats
- `checks.json`

### What the registry inherits

For a space made from a workflow, **What the registry's properties inherit** follows each semantic-registry property back to the form fields it is computed from. Each property gets the strictest setting of those fields. A property that reads any field nobody has reviewed shows as *unset (sensitive)*.

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
- [Workflow Engine](workflow-engine.md#what-a-pipeline-is): what a pipeline is, where to edit one, and how workflows and their pipelines fit together.
- [Reports with Claude](reports-with-claude.md): building and changing reports by talking to Claude.
