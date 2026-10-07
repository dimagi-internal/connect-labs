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
3. The **Initiate AI Assistant** modal opens — review the pre-filled prompt in the **Instructions to assistant** banner, edit it if needed, then click **Initiate AI**
4. The bot sends a message to the FLW through CommCare Connect
5. The conversation transcript appears in the task timeline as it progresses

The prompt shown in the modal is an instruction to the assistant describing what to coach the worker about and why — for example, noting which flag was raised and what it typically means. The assistant then opens the conversation with the worker in its own words. You can edit the instructions before clicking **Initiate AI** if you want to adjust the focus or add context.

!!! note
    The OCS bot is only available for programs that have been configured to use it. Ask your program administrator if you're unsure whether it's enabled.

!!! note
    If the OCS bot is unable to start a conversation, you will now see a specific error message explaining why — for example, if the worker is not reachable or the request was declined by the messaging service. If you see such a message, note the details and contact your program administrator.

---

## Report Actions — Buttons and the Agent Panel

Some reports offer **actions** — for example, **Initiate AI coach** or **Create follow-up task** — that you can run directly from the report without opening each task individually. This is currently available on the **Spark facilitator program report** only; it is off by default on all other reports.

### Running an action from a button

Action buttons appear in the report alongside worker or indicator rows.

1. Click the action button (for example, **Initiate AI coach**) for a worker or group of workers.
2. A confirmation panel opens showing exactly who will be contacted, what the coaching bot will be told, and — if more than one bot is available — a prompt to choose which one to use.
3. Review the details. Nothing is sent until you confirm.
4. Click **Confirm**. The panel then shows each worker's result as it completes.

**Start coaching on an indicator report.** The coaching button appears only for workers with at least one indicator off target (red) or on watch (yellow), and the button above the table counts only them — for example, "Start coaching · 6 facilitators off target or on watch". The confirmation panel shows, for each worker, the exact briefing the coach will start from: their own figures (for example, "Meetings held — 5 of 12 (42%), band red"), most urgent first, followed by any note your programme team set up. It also shows the opening line the worker receives first — for example, *opening: "Hello Tiyamike! This is a short, friendly check-in about how your work has been going. Is now a good time to talk for a few minutes?"* The worker never sees the briefing itself; the coach reads it privately and raises the topics in conversation. Workers with nothing off target are listed as left out rather than contacted. Dimagi staff coaching a single worker also see **Send to me instead (QA)**: enter your PersonalID username and the preview updates to show the conversation going to you on the worker's behalf.

### Running an action from the agent panel

Reports that have actions enabled also have an **agent panel** — an AI assistant that can see the same data you are viewing. The agent panel follows you as you drill into an organisation or an individual worker, and it understands each indicator: what "red" means for that metric and how it is calculated.

This means you can ask the assistant to act on what it sees — for example:

- *"Start AI coaching for everyone with a red metric."*
- *"Create follow-up tasks for the workers flagged this week."*

Before the assistant carries out any action, it shows you the same confirmation preview that the button does — who will be reached, what the bot will say, and which bot will be used. Nothing is sent until you approve. The assistant acts as you, using your access level.

**Testing a coaching bot yourself (Dimagi staff only).** When you run **Initiate AI coach** for a single worker, you can ask for the conversation to be sent to your own Connect app instead of the worker's — useful for trying out a coach on a synthetic (demo) report, where it is otherwise replaced by a sample conversation. The confirmation preview says plainly where the message will go ("sending to: *you* (QA, on behalf of *worker*)"). The follow-up task is still filed under the worker and is marked as a QA test. Only one worker can be redirected at a time.

When an action records which indicators a worker is being coached on, the task keeps that list, so the coaching progress shown on the report can tell you how many of those topics the conversation has covered.

You can also connect your own assistant (outside Labs) to the report if your program uses a separate tool. Once connected, it can run actions on your behalf even if the report page is not open — you do not need to keep the browser tab active.

!!! note
    The agent panel and report action buttons are turned on for the Spark facilitator program report only. If you do not see them on a report you use, contact your program administrator.

!!! tip
    Once Chat Studio (or another connected assistant) is linked, it stays connected — so it can start coaching conversations for you without requiring you to return to the page each time.

---

## Weekly Review Table — Manager Actions

During a weekly review (such as the CHC Nutrition review), the manager works through each worker row in the review table to record a decision.

**Marking a single row as No Issues:**
Click **Mark No Issue** in the Actions column for that row. The Decision column updates to show a green **No Issues** pill and the Actions cell clears — no further steps are needed for that worker.

**Marking all rows as No Issues at once:**
Use the **Mark all No Issue** toolbar button at the top of the table (above the column headers). This applies the green **No Issues** pill to every row in one click, clearing all Actions cells at the same time.

**Creating a task with coaching:**
Click **Create Task with Coaching** in the Actions column for a worker who needs follow-up. This opens the **Initiate AI Assistant** modal, where you can review and edit the outreach instructions before clicking **Initiate AI** to start the OCS bot conversation.

The task page shows a short, readable description of what the follow-up is about — for example, "Coach Maria on household visit selection." The full instructions the OCS bot will use are kept separate: they pre-fill the **Instructions to assistant** prompt field inside the **Initiate AI Assistant** modal when you're ready to send. The prompt textarea is tall enough to show the whole message without scrolling, and you can resize it vertically if you want more room to review or edit.

!!! tip
    Rows marked No Issues are visually distinct — the green pill makes it easy to scan the table and see which workers still need a decision.

---

## Completed Runs — Historical Records

Once a program run is saved and completed, it becomes a fixed historical record. In a completed run:

- Rows where no audit or task was created show **greyed-out, non-interactive** Create Audit and Create Task buttons — you cannot start new work from a finished run.
- Rows that did produce an audit or task still show a working **View Audit** or **View Task** link so you can navigate to the existing record at any time.

If you need to take action on a worker from a completed run, create a new task manually from the Tasks section instead.

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

**Can the agent panel act on my behalf when I'm not on the page?**
Yes — if you have connected an external assistant such as Chat Studio, it stays connected after you leave the page and can start conversations or create tasks for you without you needing to return to the report.

**The OCS bot showed an error when I tried to start a coaching conversation. What should I do?**
The error message should now describe the specific reason the request failed — for example, if the worker could not be reached through CommCare Connect or if the messaging service declined the request. Note the exact message and share it with your program administrator so they can investigate.
