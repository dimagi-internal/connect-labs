# Task Management

The Task module helps program teams track follow-up actions for field workers. Create tasks from audit findings or manually, assign them to supervisors or managers, monitor progress, and trigger automated outreach via the OCS bot.

---

## Task Lifecycle

```mermaid
stateDiagram-v2
    [*] --> Investigating: Task created
    Investigating --> "FLW Action In Progress": Outreach started
    "FLW Action In Progress" --> "FLW Action Completed": FLW responds
    "FLW Action Completed" --> "Review Needed": Manager review required
    "Review Needed" --> Closed: Issue resolved
    Investigating --> Closed: No action needed
```

---

## Creating a Task

**Option 1 — From an Audit Session:**
After completing an audit, click **Create Task** next to any flagged visit. The task is pre-populated with the worker's name, audit details, and date.

**Option 2 — Manually:**
Click **Tasks** in the top navigation, then **New Task**.

Fill in:

| Field           | Description                                               |
| --------------- | --------------------------------------------------------- |
| Title           | Short description of the follow-up needed                 |
| Description     | Full context — what was found and what action is required |
| Assigned worker | The FLW this task is about                                |
| Assignee        | Who is responsible for resolving it                       |
| Priority        | High / Medium / Low                                       |
| Status          | Starting status (usually "Investigating")                 |

**Option 3 — Bulk Create:**
If you have many workers to follow up with after an audit, use **Bulk Create** to generate tasks for multiple workers from a single audit session at once.

---

## Task List

The task list shows all tasks for your program. Use filters to focus on what matters:

- **Status** — filter by where tasks are in the lifecycle
- **Priority** — surface high-priority tasks first
- **Search** — find tasks by worker name or keyword

Each row shows the current status, assigned worker, priority, and when it was last updated.

---

## Working on a Task

Open a task to see its full timeline — a chronological record of all activity:

- Status changes with timestamps
- Comments from team members
- OCS bot conversation transcripts (if automated outreach was used)

**Adding a comment:**
Type in the comment box and click **Post**. Comments are visible to all team members with access to the program.

**Updating status:**
Use the status dropdown at the top of the task to move it to the next stage. Each status change is recorded in the timeline automatically.

---

## OCS Bot (Automated Outreach)

The OCS bot sends an automated chat message to a field worker via CommCare Connect messaging — gathering information or prompting action without a supervisor needing to make a direct call. The conversation is logged automatically in the task timeline.

To trigger the OCS bot:

1. Open the task
2. Click **Create Task with Coaching** (from a review table) or **Start OCS Chat** (from within a task directly)
3. The **Initiate AI Assistant** modal opens — review the pre-filled prompt, edit it if needed, then click **Initiate AI**
4. The bot sends a message to the FLW through CommCare Connect
5. The conversation transcript appears in the task timeline as it progresses

!!! note
The OCS bot is only available for programs that have been configured to use it. Ask your program administrator if you're unsure whether it's enabled.

---

## Weekly Review Table — Manager Actions

During a weekly review (such as the CHC Nutrition review), the manager works through each worker row in the review table to record a decision.

**Marking a single row as No Issues:**
Click **Mark No Issue** in the Actions column for that row. The Decision column updates to show a green **No Issues** pill and the Actions cell clears — no further steps are needed for that worker.

**Marking all rows as No Issues at once:**
Use the **Mark all No Issue** toolbar button at the top of the table (above the column headers). This applies the green **No Issues** pill to every row in one click, clearing all Actions cells at the same time.

**Creating a task with coaching:**
Click **Create Task with Coaching** in the Actions column for a worker who needs follow-up. This opens the **Initiate AI Assistant** modal, where you can review and edit the outreach prompt before clicking **Initiate AI** to start the OCS bot conversation.

!!! tip
Rows marked No Issues are visually distinct — the green pill makes it easy to scan the table and see which workers still need a decision.

---

## Common Questions

**Who can see my tasks?**
Tasks are visible to all team members with access to your program in Labs.

**Can I delete a task?**
Tasks can be closed but not deleted — this keeps the audit trail intact.

**How do I know when a task is updated?**
Labs doesn't currently send email notifications. Check the task list regularly, or coordinate directly with your team.

**How do tasks connect to audits?**
Tasks created from an audit session link back to that session automatically. You can navigate between a task and its source audit from either view.
