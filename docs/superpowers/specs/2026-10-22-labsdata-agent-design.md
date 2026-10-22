# labsdata -- a read-only data agent over real program data (the Scout experience in labs)

Status: in progress (2026-10-22). Labs side: this document and `connect_labs/labsdata/`.
Canopy side: canopy-web spec `2026-10-22-phi-agents-on-zdr-runners`, which this extends.

## What it is for

A person looking at a workflow (or naming one) asks questions of the data behind it, and
an agent answers by reading the raw data -- down to single visits and their submitted form
JSON -- alongside the pipelines, semantic layer and workflow that turn it into what the
person sees. Most of the use is **confirming a workflow does what we want**: recompute a
number from the raw visits, find the visits that disagree, read them whole, name the fix.

It is Scout (dimagi-rad/scout) inside labs, with two differences that matter:

- **Strictly read-only.** ACE has Drive, email, memory and `Write`; it cannot be declared
  safe over PII. `labsdata` is a new canopy agent -- a utility with an optional display
  name, slug `labsdata` -- whose one capability reaches labs only through canopy's gateway
  as the visitor, plus read-only local tools.
- **Real rows only on a ZDR runner, vouched for on every call.** Scout sends up to 500 rows
  to the model with no masking and no retention controls. Here real rows reach the agent
  only when `labs/canopy_panel.py:real_data_clearance()` grants it: the agent is in
  `CANOPY_PHI_AGENTS`, labs requires `zdr` runners, and canopy signs a per-call
  `Canopy-Execution` statement (agent, declared commit, runner flags) that labs verifies.

## Pieces and owners

| Piece                                                                                                                                                                                                           | Where                          | State                             |
| --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------ | --------------------------------- |
| Data app: `visits` (every raw column, full `form_json`), whole visits by id, workflow recipes, Scout's typed errors and result notes, agent result budget                                                       | labs `connect_labs/labsdata/`  | this PR                           |
| `data:read` page scope (data tools + workflow/pipeline/registry definition reads), on the Data page and workflow run pages                                                                                      | labs `labs/canopy.py`          | this PR                           |
| Real-row gate for agents (`service.agent_sees_real_rows`)                                                                                                                                                       | labs                           | this PR -- closed until clearance |
| Results View `ui://labs/data-result` (MCP App on `data_query`: table, SVG chart, SQL, "Open on the Data page"; re-asks as the viewer when a host strips the result's rows; shows no rows the agent was refused) | labs `mcp/ui/data_result.html` | this PR                           |
| Pipeline output as a relation (`pipeline_rows`: a workflow pipeline's computed rows), so a dashboard number and its recomputation sit side by side                                                              | labs                           | next                              |
| Per-page agent and runner requirements (offer `labsdata` + `zdr` on data pages, ACE elsewhere)                                                                                                                  | canopy SDK, then labs settings | canopy session 2                  |
| Expanded / full-screen panel, wide Views                                                                                                                                                                        | canopy widget                  | canopy session 2                  |
| `data: phi` declarations, storage rules for sensitive sessions, `Canopy-Execution` + `verify_execution`, PHI capabilities confined for owners too                                                               | canopy-web                     | canopy session 1                  |
| The `labsdata` agent repo, its answering skill and interface; optional display names                                                                                                                            | canopy                         | canopy session 3                  |
| Verify `Canopy-Execution` in `mcp/server.py`, flip `CANOPY_EXECUTION_STATEMENTS`, set `CANOPY_PHI_AGENTS=labsdata`                                                                                              | labs                           | after canopy step 6               |

## What labs took from Scout (and what it did not)

Already taken by the explorer (#2330): the sqlglot validator, the read-only / time-boxed
statement, the answering rules in tool output (canopy forwards host tools only, so tool
output is the only way to teach an arbitrary agent).

Taken here:

- **Typed errors** -- every failure carries `category`, `retryable` and `recovery_action`
  (`engine.ERROR_GUIDE`), and the rules tell the agent what each means, cap alternate-name
  attempts at two and stop after three failures in a row.
- **Truncation that is never ambiguous** -- fetch one row past the cap; `truncated` is true
  only when the result overflowed.
- **Notes written for the model** -- a cut result says "do not present these rows as
  complete; aggregate", a cut cell says "read the whole visit with data_visits".
- **A result budget for the model** (`service.shape_for_agent`): cells over 4,000 chars and
  results over 200,000 chars are cut, with notes. A person on the page gets results whole.
- **Row level by design** -- `data_visits` returns whole visits, form and all.

**Withheld, not refused.** An uncleared agent's `data_query` over real data is not an error: the
SQL is checked over zero rows (so a broken query still comes back as one to fix) and the result
says `withheld: true` with `open_on_page`, a link that opens the Data page on that query for the
person to run inside labs. The results View shows the same hand-off and never asks for the rows
itself: rows cross canopy only for a cleared agent, even when it is the person's View asking.

Not taken: Scout's per-tenant schemas and roles (labs scopes the data with an injected CTE
and an empty `search_path` instead), its lack of PII controls, and workspace "memory"
(which would hold PHI). Planned: Scout's "dashboards store the query, not the rows" for the
results View, and a structured semantic query tool beside raw SQL.

## Safety model (labs side)

1. **Scope** -- every opportunity a call names is one the caller holds (`scope.resolve`),
   or the call is refused.
2. **Validate** -- one SELECT, analytics functions only, no catalogs, the only relation is
   `visits` plus the query's own CTEs.
3. **Scope the data** -- `visits` is a CTE over the cache already filtered to those
   opportunities; nothing else has a name.
4. **Lock the statement** -- read-only, 20 s, empty `search_path`, always rolled back.
5. **Who sees rows** -- a person: everything they hold. An agent through canopy: real rows
   only when cleared (today: never); otherwise structure, validation over zero rows and
   synthetic data, and it hands SQL to the page.
6. **Audit** -- every query (hash of the SQL, never the SQL) and every visit read, per
   opportunity.
