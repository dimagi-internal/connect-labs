# AI Features

Connect Labs has AI assistants embedded throughout the application. They help program managers and administrators make changes, understand data, and get things done faster — without needing to write code.

---

## Where AI Appears

```mermaid
flowchart LR
    AI[Claude AI] --> W[Workflow Editor\nAssistant]
    AI --> P[Pipeline Editor\nAssistant]
    AI --> A[Audit Image\nReviewer]
    AI --> S[Solicitation\nCriteria Generator]
    AI --> C[Application\nCoach]
```

### Workflow & Pipeline Editor Assistant

When editing a workflow or pipeline in the Workflow Engine, an AI assistant is available to help make changes. You can describe what you want in plain English:

- _"Add a column showing the average weight from the last 3 visits"_
- _"Change the status labels from Active/Inactive to Enrolled/Graduated"_
- _"Remove the RUTF field from the table — it's not relevant for this program"_

The AI understands the current workflow's structure and makes targeted changes. Replies appear word-by-word as the AI writes them, so you can start reading immediately rather than waiting for the full response to arrive. After each change, you can preview the result and either keep it or ask for a revision. More advanced edits can be done with Claude through the [Connect MCP](connect-mcp.md) — see [Reports with Claude](reports-with-claude.md).

---

### Audit Image Reviewer

In the Audit module, you can trigger an AI pre-screen before doing a manual image review. The AI checks each image for:

- **Image quality** — blur, poor lighting, or incomplete framing
- **Measurement validity** — scale or MUAC readings that are outside expected ranges
- **Required elements** — whether the required items are clearly visible in the photo

The AI flags images it thinks need attention, but you always make the final Pass/Fail decision. See [Audit & QA Review](audit.md) for the full workflow.

---

### Solicitation Criteria Generator

When creating a solicitation (RFP or EOI), you can paste in a program description or upload a document and ask the AI to suggest:

- A structured set of evaluation criteria
- Recommended scoring weights for each criterion
- Sample questions for the response template

Review and edit these suggestions before saving. The AI drafts criteria for your organisation's call — other organisations running their own calls through the public directory go through the same process when they create theirs. See [Solicitations](solicitations.md) for the full process.

---

### Application Coach

When filling out a response to a solicitation, the **AI Application Coach** is available on the response form to help applicants strengthen their submissions. It can:

- Review draft answers and suggest improvements
- Flag sections that may be incomplete or unclear
- Offer guidance on how to address specific evaluation criteria

For survey-firm solicitations specifically, the coach also pushes applicants to back up claims with **verifiable evidence** — real numbers, named prior projects, and back-check rates — rather than general assertions. This helps reviewers assess submissions fairly and consistently.

The coach is available whether you are responding to your own organisation's call or to a call posted by another organisation in the public directory. It works the same way in both cases.

See [Solicitations](solicitations.md) for the full process.

---

## Survey-Firm Selection Safeguards

When running a survey procurement (an RF Survey solicitation), Connect Labs applies a set of built-in fairness controls to help you run a defensible, unbiased process.

### Locked Rubric

Before you publish the call, you review the AI-drafted weighted criteria and **lock the rubric**. Once the call is published:

- The criteria and scoring weights are fixed for every applicant.
- No changes can be made while the call is open.
- A **"Rubric locked before publishing"** badge appears on the call and on the responses list, giving applicants and your team confidence that the rules didn't change mid-process.

### Blind Scoring

While you are scoring responses, firms are shown only as **"Response #\<number\>"** — never by name. The firm's identity is revealed only at the award step, after scores are recorded. This ensures reviewers are judging the application, not the applicant.

!!! info "Conflict of interest protection"
    Reviewers cannot score or award a response submitted by their own organisation. The platform blocks this automatically.

### Anti-Anchoring on AI Scores

For each scoring criterion, the AI provides a suggested score — but that suggestion is **hidden until after you record your own score** for that criterion. Once you submit your score, the AI's score is revealed so you can compare. This prevents the AI from anchoring your judgment before you've formed your own view.

### Staged-Contract Award

Rather than awarding the full survey contract immediately, you can issue a small **verification contract** first. This lets you confirm that the firm performs as expected on the ground before committing to the full scope. A verification contract can later be upgraded to the full engagement. Staged contracting is how the platform handles performance risk: start small, verify, then scale.

When an award is made, the winning organisation is notified by email. The award page shows clearly whether that email was sent successfully.

!!! info "Award to a Connect opportunity"
    Connecting the awarded contract to a live Connect opportunity is not yet available. The award page notes this as a planned next step.

See [Solicitations](solicitations.md) for the step-by-step procurement workflow.

---

## What the AI Can and Can't Do

| Can do                                      | Can't do                                    |
| ------------------------------------------- | ------------------------------------------- |
| Edit workflow display logic                 | Submit CommCare forms                       |
| Update pipeline data fields                 | Change CommCare HQ settings                 |
| Pre-screen audit images                     | Access individual patient health records    |
| Suggest solicitation criteria               | Submit responses on behalf of organizations |
| Coach applicants on response quality        | Make changes in the main CommCare platform  |
| Answer questions about the current workflow |                                             |

---

## Data Privacy

AI features in Connect Labs route through a **governed endpoint** — content that passes through AI processing is handled under Dimagi's Zero Data Retention (ZDR) agreement with Anthropic. This means prompt content is not stored by the AI provider after processing.

!!! info "Stricter data handling"
    If you work with programmes that have stricter requirements, connect Claude to Labs through the restricted address, `https://labs.connect.dimagi.com/mcp/no_user_visit/`. It lets Claude read and edit how your programme is built, but never shows it a real beneficiary's visit. See [What "No User Visit Data" Means](connect-mcp.md#what-no-user-visit-data-means). An administrator can also lock an account to that level permanently, so it holds whatever address or token is used.

---

## Common Questions

**The AI made a change I don't like. Can I undo it?**
Yes. In the workflow editor, use the **Undo** button or ask the AI to revert its last change. The workflow version history also lets you restore any previous version.

**Can the AI see patient data?**
The AI used for workflow and pipeline editing does not have access to individual patient records. For audit image review, images are sent to the AI for analysis but processed under ZDR terms — they are not retained by the AI provider.

**Which AI is being used?**
Connect Labs uses Claude (made by Anthropic) as the AI for workflow editing, pipeline assistance, solicitation criteria generation, and the Application Coach. Audit image review also uses Claude's vision capability.

**Why does the AI reply appear word by word instead of all at once?**
AI responses now stream in as they are written, so you see the answer building in real time rather than waiting for the full reply to arrive. This is normal — you do not need to wait for the response to finish before you start reading.

**Can the Application Coach see responses submitted by other organisations?**
No. Applications are private. Each organisation can see only the responses submitted to its own calls, and applicants can see only their own submission. The coach works only within the form you are filling in — it has no access to anyone else's responses.
