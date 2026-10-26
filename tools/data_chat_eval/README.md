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

| What                        | Where                                                                                   | Fix when missing                                                                                                    |
| --------------------------- | --------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------- |
| ace's labs browser session  | `~/.ace/labs-session.json` (Playwright storage state)                                   | ace plugin `/ace:labs-login` (headless OAuth via CommCare HQ with `ACE_HQ_USERNAME/PASSWORD`). Expires after ~a day |
| ace's labs MCP PAT          | `LABS_MCP_TOKEN` in `~/.ace/.env` (or the environment)                                  | `/ace:labs-token-mint`                                                                                              |
| ace's canopy PAT            | `CANOPY_WEB_PAT` in `~/.ace/.env`                                                       | optional: without it a turn's end and its tool calls are read from the panel only                                   |
| ace's canopy test vault key | `ACE_CANOPY_TEST_VAULT_PASSPHRASE`, `ACE_CANOPY_TEST_VAULT_SECRET_KEY` in `~/.ace/.env` | needed to read a private (Data page) conversation back; canopy-web's agent "test vault key"                         |
| the `claude` CLI            | on `PATH`                                                                               | optional: the judge is skipped                                                                                      |

`preflight` checks the first three before any agent turn is spent.

## How it works

1. **Prepare.** Loads every opportunity the suite reads into labs' visit cache
   (`data_describe` with `load_missing`, one opportunity per call: a whole programme in one
   call outlives the load balancer).
2. **Ground truth.** Each scenario's `truth` SQL runs through labs' `data_query` as ace
   (its PAT), over the page's opportunities -- the same engine and the same person as the
   chat, so the truth is exact. A number check allows a count of 100 or more to be one
   off (a rounding or a boundary), a smaller count none; a case sets its own allowance with
   `tolerance` / `rel_tolerance`, or `exact: true`.
3. **Drive.** Per scenario: a fresh browser context with ace's labs cookies, the page, the
   panel (launcher "Ask an agent"), a NEW conversation. Each turn is typed into the
   composer; its end comes from canopy's session API (`turn_status`, read with ace's canopy
   PAT, since ace started the conversation), else from the panel going quiet. Then the
   reply, the tool calls, every Labs results card drawn that turn (each MCP Apps view's own
   frame: text, headers, chart) and screenshots.
4. **Check.** Deterministic checks per turn (`checks.py`): the reply or a card states the
   ground-truth number; a refusal is put plainly (any of the ways `checks.REFUSALS` knows); a card was drawn, with a chart, with plain-word headers; the right
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

### Containment (direct MCP, no chat)

`containment:` in the suite (`containment.py`) calls labs' `data_query`, `data_validate` and
`data_describe` directly as ace (its PAT), with no agent, and probes the Data page's SQL
boundary: the validator, the scope checks, and the `labs_data_reader` role with row-level
security on the caches (`labsdata/engine.py`, `labs/migrations/0039`):

- **correctness under RLS**: each opportunity's count matches `data_describe`'s `cached_visits`
  (an app-role read); a program equals the sum of its opportunities, the workflow selection
  and a data space on the program; `visits` holds exactly the selection; a pipeline
  relation's rows per opportunity match what it stores (each workflow's pipelines are rebuilt
  first with `data_describe(workflow, load_missing)`, since stored pipeline output lives an
  hour, and an empty relation must say why); and, where another workflow's relation has the
  same name but another pipeline, each reads only its own pipeline's rows;
- **containment**: the caches and the grants table by name (bare, qualified, quoted),
  Django's tables, the catalogs, functions that run a string as SQL or reach the server,
  role changes, several statements, writes, a pipeline relation without its workflow,
  `space_id` beside an outside opportunity, program or workflow, and opportunities and
  programs ace does not hold. Each one must be refused, or must read nothing;
- **error quality**: every refusal is a typed code with a short, readable message that
  leaks no Postgres text and no internal relation the caller didn't name, and that points
  only at tools labs actually has.

Every raw response goes to `<run>/<id>/probes.json`. `--surface containment` runs only this
set, and `--no-containment` skips it. It costs no agent turns. The chat scenario
`outside-selection` asks the run-page agent to add opportunities outside the page's
selection and checks that it refuses cleanly (`refuses`, `no_errors: {allow_refused: true}`) and
that no data call read them (`reads_none_of`): labs refuses such a call with `outside_page`
(`labsdata/page_holds.py`).

Adding a programme: pick a labs-only synthetic one whose data is GENERATED
(`labs/synthetic/provenance.py`; a workflow over anything else shows no panel to an agent
not cleared for sensitive data -- the run page says so in red beside "Agent:"), and a
workflow on it with `config.agent.share`.

## Private conversations (the Data page)

The Data page's agent runs on sensitive-data runners, so canopy seals the conversation to its
people; ace holds a passphrase-only vault key for this (canopy-web's agent "test vault key").
Starting and sending need nothing, and live replies arrive in the clear. `vault.py` unlocks
the panel when it shows its locked banner (`[data-testid="sealed-banner"]` → the unlock form,
inside canopy's `/embed/chat` frame), and after the last turn the driver reloads the page,
reopens the conversation from the panel's list ("Private conversation", newest first) and
checks it unlocks and the last reply reads again (`sealed_checks`). The two vault values are
read from `~/.ace/.env`, typed only into canopy's form, cleared from it before any
screenshot, and scrubbed from every file in the run (`vault.scrub_dir`). Because the reopen
picks the newest private conversation, run Data page scenarios one at a time
(`--surface data_page --parallel 1`).

canopy's API returns a sealed conversation's tool names but never their input, result or
words: replies come from the panel, and a labs call shows as canopy's `site_call` (the
`tool` check passes on it with "sealed" in its detail, and notes whether a Labs card was
drawn). The `canvas` check reads the panel's own page state (`filters.canvas`) and the Save
dialog, so `page_showQuery` and `page_proposeSave` are checked from what the person sees.

## Known limits

- **On the run page ace visits as its own owner, so its turn is FULL, not confined** -- and
  since canopy-web#2142 a full turn on a labs page reaches labs only through canopy's gateway
  (`site_call`), under the sender's page grant, owners included: canopy withholds ace's own
  `LABS_MCP_TOKEN` from the turn and ace's headers helper sends no credential there. So
  `outside-selection` now also tests labs' enforcement: labs' page hold
  (`labsdata/page_holds.py`) refuses the outside opportunities on the grant. The harness reads a
  gateway call as the labs tool it named (`canopy_api.through_gateway`: `short` and `input` are
  the inner tool's, and the site's own refusal -- `"is_error": true` in `site_call`'s answer --
  counts as a refused error), so `tool`, `reads_none_of` and `no_errors` see it as they see a
  direct call. On a laptop runner ace's token is still on disk; there the withholding is ace's
  helper and its instructions. `data-outside-selection` tests the same on the Data page, whose
  agent runs confined for everyone (its capability handles sensitive data).

- Spaces' teammate and lock-out rules need a second synthetic person (one who holds the
  sources, one who misses one); not covered yet.

## Tests

Not collected by CI (it runs `pytest connect_labs/` only), so run them yourself:

```bash
$VENV/python -m pytest -p no:django -o addopts="" tools/data_chat_eval/tests -q
```
