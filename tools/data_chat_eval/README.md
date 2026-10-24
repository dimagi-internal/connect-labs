# Data chat eval

A harness that chats with the labs data agent the way a person does -- in the canopy panel
of a real labs page, in a headless browser, signed in as **ace** -- on labs-only synthetic
data, and grades what comes back. One command runs the suite and writes a report, so the
loop is: run, read the findings, fix, run again.

Design: `docs/superpowers/specs/2026-10-24-data-spaces-design.md` § "Testing it as ace, on
synthetic data".

## Run it

From the repo root (a worktree uses the main checkout's venv):

```bash
VENV=$(git rev-parse --git-common-dir)/../.venv/bin
$VENV/python -m tools.data_chat_eval preflight          # ace's credentials, nothing else
$VENV/python -m tools.data_chat_eval list               # the scenarios and their pages
$VENV/python -m tools.data_chat_eval run                # everything, 2 conversations at a time
$VENV/python -m tools.data_chat_eval run --only header-matches-raw drill-visit --no-judge
$VENV/python -m tools.data_chat_eval run --surface data_page --headed
```

It writes `tools/data_chat_eval/runs/<timestamp>/` (gitignored): `report.md`, `report.json`,
and per scenario its screenshots (`turnN-page.png`, `turnN-panel.png`, `turnN-cardM.png`),
`capture.json` (what the browser saw), `transcript.json` (the conversation as canopy
recorded it, tool calls included) and `reply.md`. Exit status is 0 only when every scenario
passes. A run of the whole suite takes 10-15 minutes and costs real agent turns.

## What it needs (ace's own credentials, nothing new)

| What                       | Where                                                  | Fix when missing                                                                                                    |
| -------------------------- | ------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------- |
| ace's labs browser session | `~/.ace/labs-session.json` (Playwright storage state)  | ace plugin `/ace:labs-login` (headless OAuth via CommCare HQ with `ACE_HQ_USERNAME/PASSWORD`). Expires after ~a day |
| ace's labs MCP PAT         | `LABS_MCP_TOKEN` in `~/.ace/.env` (or the environment) | `/ace:labs-token-mint`                                                                                              |
| ace's canopy PAT           | `CANOPY_WEB_PAT` in `~/.ace/.env`                      | optional: without it a turn's end and its tool calls are read from the panel only                                   |
| the `claude` CLI           | on `PATH`                                              | optional: the judge is skipped                                                                                      |

`preflight` checks the first three before any agent turn is spent.

## How it works

1. **Prepare.** Loads every opportunity the suite reads into labs' visit cache
   (`data_describe` with `load_missing`, one opportunity per call: a whole programme in one
   call outlives the load balancer).
2. **Ground truth.** Each scenario's `truth` SQL runs through labs' `data_query` as ace
   (its PAT), over the page's opportunities -- the same engine and the same person as the
   chat, so number checks are exact.
3. **Drive.** Per scenario: a fresh browser context with ace's labs cookies, the page, the
   panel (launcher "Ask an agent"), a NEW conversation. Each turn is typed into the
   composer; its end comes from canopy's session API (`turn_status`, read with ace's canopy
   PAT, since ace started the conversation), else from the panel going quiet. Then the
   reply, the tool calls, every Labs results card drawn that turn (each MCP Apps view's own
   frame: text, headers, chart) and screenshots.
4. **Check.** Deterministic checks per turn (`checks.py`): the reply or a card states the
   ground-truth number; a card was drawn, with a chart, with plain-word headers; the right
   tool was called; the rows were not pasted under a card; an ambiguous question was asked
   back or its definition stated; and, on every turn, no errors (finished, no panel error,
   no failed tool call). Gates decide PASS/FAIL; `gate: false` checks are advisory.
5. **Judge.** `claude -p` scores each scenario 1-5 on correct, clear, used the page well,
   card readable, looking at the screenshots, and names the one fix most likely to help.
   Scores inform; they never pass or fail a scenario. A mean under 3.5 is a finding.
6. **Report.** `report.md`: a table, then **findings** -- each failure with its evidence
   (what the check saw, the reply, the screenshot) and where it is most likely fixed:
   labs guidance and tool descriptions (`connect_labs/labsdata/guidance.py`,
   `connect_labs/mcp/tools/labsdata.py`), the results card
   (`connect_labs/mcp/ui/data_result.html`), the run tools
   (`connect_labs/workflow/agent_sharing.py`), the agent's own skill (the labsdata agent:
   `dimagi-internal/labsdata` `skills/answer-data/SKILL.md`; ace: its labs capability), or
   setup (canopy identity, Labs Admin › Canopy agents).

## Scenarios

`scenarios/kmc.yaml`, on the synthetic **KMC (Synthetic) Oct 2026** programme (10121,
12 opportunities, generated data): its programme report "KMC Program Metrics" (workflow
9027, shares its run with the agent) and the Data page on the same programme.

```yaml
pages:
  kmc_run:
    {
      workflow: { id: 9027, program_id: 10121, run: latest },
      opportunities: kmc,
    }
scenarios:
  - id: top-workers
    page: kmc_run
    truth: # one row each; checks name <truth>.<column>
      top:
        select username, count(*) as n from visits where opportunity_id = 10132 and status = 'approved'
        group by username order by n desc, username limit 1
    turns:
      - say: In the Kikapu opportunity, which five field workers recorded the most approved visits?
        checks:
          - text: { any_of: [top.username] }
          - number: { value: top.n }
          - card: { min: 1 }
```

`say` may use `{truth.column}` (e.g. a visit id picked live). A check may carry `route:`
to say where its failure is fixed. Check kinds and their arguments: `checks.py`.

### Data spaces

`spaces:` in the suite (`spaces.py`) walks one space's life on synthetic data, checking each
step: create it (`POST /labs/data/api/spaces/`, ace's own session and CSRF), open
`/labs/data/space/<id>/` (the page draws it and the panel's state carries `space_id`),
`data_query` held to it over MCP (answers the truth; another opportunity beside `space_id` is
refused `outside_space`; SQL naming an outside opportunity reads nothing), the agent's page
actions called exactly as canopy calls them (`window.LABSDATA_ACTIONS.showQuery` switches the
canvas, shows the truth and sets `filters.canvas`; `proposeSave` fills the dialog and saves
nothing until `#ex-save-confirm` is clicked, then the item is in the rail and the items API),
one chat turn in the space's panel (the canvas should switch), and archive (the page then
404s). The space is archived at the end whatever happens. `--surface data_space` runs only
these; `--no-spaces` skips them.

Adding a programme: pick a labs-only synthetic one whose data is GENERATED
(`labs/synthetic/provenance.py`; a workflow over anything else shows no panel to an agent
not cleared for sensitive data -- the run page says so in red beside "Agent:"), and a
workflow on it with `config.agent.share`.

## Known limits

- **The Data page's chat needs a canopy vault key for the visitor.** Its agent (labsdata)
  runs on sensitive-data runners, so canopy seals the conversation to its people, and a
  person with no vault key is refused ("a sensitive conversation needs a canopy account with
  a vault key"). ace's canopy account has none, so the `data-*` scenarios report BLOCKED
  until it does. They are kept so they start running the day it does.
- Sensitive conversations are sealed: canopy's API returns no words for them, so the reply
  and tool calls fall back to the panel's text (`source: panel` in `capture.json`).
- Spaces' teammate and lock-out rules need a second synthetic person (one who holds the
  sources, one who misses one); not covered yet. The space's chat step is BLOCKED by the
  vault key above, like the other `data-*` scenarios.

## Tests

Not collected by CI (it runs `pytest connect_labs/` only), so run them yourself:

```bash
$VENV/python -m pytest -p no:django -o addopts="" tools/data_chat_eval/tests -q
```
