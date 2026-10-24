# Data spaces: a shared place on the Data page, and a way to keep its chat honest

Status: built (#2714). Since then every read goes through a space, saved or generated: see
`2026-10-24-one-path-through-data-spaces.md` (where this doc says `service.select`, read
`spaces._resolve`). Originally: approved for build (Jonathan, 2026-10-24: "just build it out then we can improve";
"we should create a data model to ensure 100% security for any given set of data. Something
like a data space"). Builds on `labsdata` (the Data page, `service.select`, `SavedQuery`,
`display.py`) and the canopy agent panel (`labs/canopy_panel.py`, page actions).

## What it is for

A team working on a program or opportunity wants one place to look at its data, ask an
agent questions about it, and keep what was useful -- queries, charts, small reports --
so a teammate can open it next week and pick up where they left off. This is Scout's
workspace (a named set of data sources with the things built on them), rebuilt inside
labs on the engine labs already has.

Jonathan's picture of the page: **the left rail has all the information, the middle shows
the results, and the chat is the canopy SDK panel, dockable or overlaid, which switches
the rendering area as things change.**

## The rules (the reason for the model)

A **data space** is a named, fixed set of sources -- opportunities, programs (frozen to
their opportunities, below) and optionally one workflow. Everything in it -- each saved
query, each artifact, the agent's conversation on it -- reads only those sources.

1. **You may open a space only while you hold every one of its sources, now.** Checked on
   every read through `service.select` (which already refuses anything not held), never
   stored as a grant. There is no sharing step and no membership table: a teammate who
   holds the same sources sees the space; someone who loses one opportunity loses the
   whole space. A space you may not open answers `not_found`, so its name does not leak.
2. **Programs are frozen to their opportunities.** A program source records the
   opportunities it had when added. Opening requires holding the program and every one of
   those opportunities. So a person who holds only part of a program cannot open the space
   (no partial view), and everyone who can open a saved chart sees the same numbers under
   the same name. When the program gains opportunities, the space says so to people who
   hold them, and "Update sources" re-freezes (a source change, rule 4).
3. **No rows are stored.** A saved query keeps its SQL, selection-by-space and `display`;
   an artifact keeps its code and the named queries it reads. Opening either runs it again,
   live, as the person opening it (as `SavedQuery` does today).
4. **Changing a space's sources is the one dangerous write.** Adding a source locks out
   everyone who does not hold it; the page says who loses access is not knowable to us,
   and asks for a confirm. Only a person in their own session can change sources (no
   agent tool, no page action). Audited.
5. **The agent in a space is held to the space** (since generalised: the agent on any page is
   held to that page's selection, from what labs recorded at the mint -- `labsdata/page_holds.py`). The panel's page state carries the
   space id; every `data_*` call made with a `space_id` resolves the selection from the
   space (rule 1) and refuses SQL naming anything else. The existing real-row rules
   (`agent_sees_sensitive_rows`, `Canopy-Execution` clearance) are unchanged: an uncleared
   agent still gets structure and hands SQL to the page.
6. **One gate.** A test fails if any `labsdata` view or `data_*` tool that takes a
   `space_id` reads a space without `spaces.open(caller, space_id)`, and if any query over
   a space bypasses `service.select`. (The shape of `tests/test_workspace_authorizer_is_sole_gate.py`
   in canopy-web.)

## The model (labs, `connect_labs/labsdata/models.py`)

- `DataSpace`: `name`, `description`, `opportunity_ids` (frozen, the union of every
  source's opportunities), `program_ids`, `workflow` (`{id, opportunity_id|program_id}` or
  empty -- as `SavedQuery.workflow`), `created_by`, `created_at`, `updated_at`,
  `archived_at`. Listed under each of its programs/opportunities.
- `SavedQuery` gains a nullable `space` FK. With a space it belongs to the space (anyone
  who can open the space sees it, any of them can add a version); without one it stays
  personal and owner-only, as today. Versioning per (space, name) as today per
  (owner, name). Keep `owner` as "saved by".
- `SpaceArtifact`: `space`, `name`, `version`, `parent`, `code` (render code, as a labs
  page's), `queries` (`[{name, sql, max_rows}]`, each validated against the space as a run
  would be), `saved_by`, `created_at`. Rendered only in the page sandbox (below).
- Audit every create/version/delete/source change per opportunity, as saves are today.

## The page (`/labs/data/`, and `/labs/data/space/<id>/`)

Three regions:

- **Left rail -- everything about the space:** space switcher (spaces you can open,
  grouped by program/opportunity, and "New space from this selection"), its sources as
  chips (with "Update sources"), tables and columns (today's Tables card), saved queries
  and artifacts in the space (open, version history, delete), and "My queries" (personal).
- **Middle -- the canvas:** shows one thing at a time: the SQL editor + result (today's
  view), a saved query's result, an artifact, or a visit. A small tab strip keeps the last
  few so the agent switching the canvas never loses the person's work.
- **Right -- the chat:** the canopy panel, docked by default, can be undocked to overlay.
  `canopy_panel.data_page_agent()` as today, with `resource: labs-data-space://<id>`,
  `filters: {space_id}`, `backing_tool: data_describe`.

**The agent switches the canvas with page actions** (canopy-web embedding guide §7: browser-only
things, run in the person's tab, as them):

- `page_showQuery({sql, display, title})` -- put a query in the canvas and run it here, as
  the person (rows never go to canopy; this is the "hand SQL to the page" path made smooth).
- `page_openItem({kind, id})` -- open a saved query or artifact of this space.
- `page_showArtifactDraft({name, code, queries})` -- render a draft artifact in the sandbox.
- `page_proposeSave({kind, name, sql|code, display, queries})` -- fill the Save dialog;
  nothing is saved until the person clicks Save (the page's own POST, CSRF, as them).
- `page_showVisit({visit_id})`.

Each action validates its input and throws to refuse (the guide's contract).

## Artifacts in the sandbox (DRY with labs pages)

An artifact is a small labs page: render code run in the page sandbox
(`docs/superpowers/specs/2026-10-12-page-sandbox-design.md`: opaque-origin iframe, enforced
CSP, `MessageChannel` bridge). The host runs the artifact's named queries as the viewer
through `service.query` over the space and passes rows in; the frame asks for nothing else.
Reuse the sandbox host and frame labs pages use; do not build a second one.

## The same chat on workflows

A workflow run page's chat and the Data page's chat are one engine: the same `data_*`
tools, `display`, results card, kept results and guidance. A workflow's chat gains:
"Save to a space" on the card (opens the Data page on the space of the workflow's
program/opportunity, or offers to make one), and `page_showQuery` there opens the Data
page in a new tab rather than changing the run page. The Data page chat gains everything
a workflow chat has that is not tied to a run (definition reads via `data_workflow_context`
when the space has a workflow), plus spaces, artifacts and page actions.

## Testing it as ace, on synthetic data

A harness that drives both chats like a person would and grades what comes back, so the
experience can be improved in a loop. Lives in connect-labs under `tools/data_chat_eval/`.

- **Who and where:** ace's labs identity (`view_synthetic_opps`), on labs-only synthetic
  programs and opportunities (`synthetic_env_*`), never real data. A second synthetic
  person (or ace plus a restricted test user) checks the teammate and lock-out rules.
- **Scenarios** (YAML): each is a page (workflow run or Data space), a conversation
  (one or more turns), and checks. Ground truth is computed by the harness itself by
  running SQL through `data_query` as ace, so a check like "the answer's number matches
  visits per FLW" is exact.
- **Checks:** facts in the reply match ground truth; a results card rendered with sane
  labels/formats; the canvas switched to the right thing; Save via the page produced the
  right item in the right space; a teammate holding the sources sees it; a person missing
  one source gets `not_found`; the agent refused SQL outside the space.
- **Drive** the real pages in a headless browser signed in as ace (the `ace:labs-login`
  session), open the canopy panel, send messages, wait for the turn to end, capture
  screenshots of the panel and canvas. A judge (LLM) scores each scenario with a short
  rubric (correct, clear, used the canvas well, card readable); deterministic checks gate.
- **Loop:** one command runs the suite and writes a report (pass/fail per check, scores,
  screenshots, transcripts). Failures are routed to a fix: tool descriptions and
  `guidance.py`, the labsdata agent's skill (canopy repo), the card, the page. Rerun until
  green twice in a row.

## Order of work

1. Data spaces: model, gate + its test, API, left rail and canvas, Save into a space, page
   actions, `space_id` on `data_*` tools. Personal saved queries keep working.
2. The harness, first against today's workflow chat and Data page chat, then spaces.
3. Artifacts in the sandbox.
4. Workflow chat: "Save to a space", parity gaps the harness finds.

## Not now

Space memory/notes the agent writes (would hold PHI), scheduled refreshes, sharing a space
outside the people who hold its sources, any stored result rows.

## Who can open a space, and who can change it (the ACL)

Jonathan, 2026-10-24: "the default mode is that the creator of the space sets it to anyone
with permission to the underlying objects, checked on loading of the space. But we might
want alternatives later."

Access to a space is **two rules, both required, checked together on every load and every
read** (`spaces.open`):

1. **The data rule -- fixed, never configurable.** You hold every source of the space now
   (rules 1-2 above). No setting, role or audience can get round it.
2. **The audience -- chosen by the space's managers, and it can only narrow.** Who, among the
   people who pass the data rule, may open the space. Today:
   - `holders` (the default): anyone who passes the data rule.
   - `private`: only the space's managers.
   Later audiences (named people, an organisation's members, a program team) are each one
   function in one registry (`spaces.AUDIENCES`), asked only after the data rule passes, so
   adding one can never let anyone see data they do not hold.

**Roles inside a space.** *Managers* (the creator, and anyone a manager adds) change the
sources, the audience and the managers, archive the space, and delete any item. *Members*
(anyone else who can open it) run everything, save new queries and new versions, and delete
items they saved. A manager is held to both rules like anyone else: a manager who loses a
source cannot open, or manage, the space.

**When no manager can open it.** The space keeps working for its members (an empty
`private` space is closed to everyone). A labs admin (Dimagi staff, Labs Admin) can make
someone who can open it a manager. Nobody can make themselves a manager.

**Agents.** An agent in a space acts as the visitor, with the visitor's access, and can
never change sources, audience or managers -- those are person-session writes only, behind
CSRF, audited (`SettingChange`-style before/after in the audit trail).

**No per-item permissions.** Items inherit their space. A thing that needs a different
audience goes in a different space.

## Saved conversations

Jonathan, 2026-10-24: "We should also be able to save pointers to canopy sessions in the data
space that we can load later."

- **A space item of kind "conversation" is a POINTER, never a transcript**
  (`models.SpaceConversation`): canopy's session id, a label the saver types (default
  "Conversation, <date>"), `saved_by`, `created_at`. Nothing the conversation said is ever
  stored in labs: not its words, not canopy's title, not its opening. A private conversation's
  words are sealed to its people (canopy itself cannot decrypt them), and labs is not one of
  them; a title or opening copied into a label would be a copy labs must not hold.
- **It inherits the space's ACL.** Listed, saved and deleted only through `spaces.open` (the
  data rule, then the audience). Any member saves one; a member deletes their own; a manager
  deletes any (`spaces.may_delete_conversation`). No agent surface saves or deletes one: no
  `data_*` tool, no page action (`test_spaces_gate.py`).
- **A pointer grants nothing.** Opening it asks canopy to open that session in the space's
  panel (`canopyHost.openSession(id)`, the widget's `openSession`), and the panel reads it as
  the person, so canopy's session ACL (`apps/canopy_sessions/access.py`) decides. Someone not in
  a private conversation sees canopy's own interstitial, which offers to ask its people to add
  them (canopy-web's join requests). A private conversation stays readable only by its people.
- **Only a conversation started on this space's page may be saved to it.** The widget reports
  the conversation open in the panel as `{id, resource, private}` (canopy-web's embedding guide
  §5e), where `resource` is canopy's record of the page it was started on: the first `resource`
  its page declared (`Session.origin_resource`). Labs refuses a save unless that is
  `labs-data-space://<id>` for this space (`not_this_space`). canopy has no signed attestation of
  this. The report is relayed by the person's own browser, so the check stands as the person's
  own write, behind CSRF and the space's rules. Faking it would let a member point at a
  conversation from elsewhere, which grants nobody anything.
- **The page.** A "Conversations" card in the left rail lists the space's pointers (label,
  who saved it, when). Clicking one opens it in the panel. "Save this conversation" saves the
  conversation the panel has open, with the label typed beside it. The button says why when it
  cannot save: no conversation is open, it was started on another page, or it is already saved.
  The SDK's panel relays the widget's report as a `canopy:session` event and
  `canopyHost.session()` (dimagi-canopy 0.22.1). An older panel is asked on demand
  (`canopyHost.widget.currentSession()`).
- Routes: `GET|POST /labs/data/api/spaces/<id>/conversations/`,
  `POST /labs/data/api/spaces/<id>/conversations/<pk>/delete/`. Saves and deletes are audited
  per opportunity with the space and session ids only.
