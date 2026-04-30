# Workflow Engine

The Workflow Engine lets program managers create configurable dashboards that pull live data directly from CommCare. Each workflow displays field worker performance metrics and supports drill-down views, status tracking, and filtering.

---

## How Data Flows

```mermaid
flowchart LR
    CC[CommCare\nForm Submissions] -->|Pipeline extracts\nand aggregates| P[Pipeline]
    P --> W[Workflow Dashboard]
    W -->|Interactive\nview| PM[Program Manager]
    PM -->|Status updates\nand notes| W
```

**Pipelines** define what data to pull and how to aggregate it (counts, sums, last values, etc.). **Workflows** define what to display and how users interact with it.

---

## Finding Your Workflows

Click **Workflows** in the top navigation. You'll see a list of all workflows configured for your program.

Each row shows:

- Workflow name and type
- Last run time and data freshness
- Current status

Click any workflow to open its dashboard.

---

## Reading a Workflow Dashboard

A typical workflow dashboard shows a **table of field workers** with performance columns:

| Column type | What it shows |
|------------|---------------|
| Count | Number of visits or activities in the period |
| Status | Current enrollment or case status |
| Last value | Most recent recorded measurement |
| Percentage | Proportion meeting a threshold |

**Filtering and sorting:**

- Use the **date range picker** to focus on a specific period
- Click column headers to sort
- Use the **search box** to find a specific worker by name

**Drilling into a worker:**

Click any row to see that worker's detailed record — individual visit data, timeline of activities, and linked cases.

---

## Workflow Statuses

Many workflows include status columns that track where a case is in a process:

```mermaid
stateDiagram-v2
    [*] --> Active
    Active --> Review_Needed: Flag raised
    Review_Needed --> Action_Taken: Intervention done
    Action_Taken --> Closed: Case resolved
    Active --> Closed: Graduated
```

Program managers can update a case's status directly from the workflow view. These updates are stored in Labs and visible to all team members.

---

## Starter Templates

Labs includes pre-built workflow templates for common program types:

| Template | Best for |
|----------|---------|
| **KMC Longitudinal** | Kangaroo Mother Care tracking over time |
| **KMC FLW Flags** | Flag workers needing follow-up |
| **KMC Project Metrics** | Program-level KPIs |
| **MBW Monitoring** | Mother and baby wellness visits |
| **Performance Review** | FLW performance across programs |
| **SAM Follow-up** | Severe acute malnutrition case tracking |
| **OCS Outreach** | Community health outreach tracking |
| **Bulk Image Audit** | Image-based QA combined with workflow |

Your program administrator can create a workflow from any of these templates.

---

## Common Questions

**Why is a worker's data missing or outdated?**
Pipelines refresh data on a schedule. If a CommCare form was submitted recently, the data may take up to 30 minutes to appear. Look for the "Last refreshed" timestamp at the top of the workflow.

**Can I export the workflow data?**
Some workflows include an export button in the top toolbar. If yours doesn't, ask your program administrator — this can be configured.

**The dashboard looks different from yesterday — what changed?**
Workflow dashboards are actively developed. Check the [weekly changelog](https://dimagi.atlassian.net/wiki/spaces/connect/pages/3918528513/Connect+Labs+Changelog) for recent updates.
