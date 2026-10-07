# DDD Context — connect-labs supply (`supply-sophie-sheets`)

## Project

connect-labs, the labs/rapid-prototyping environment for Connect. The active narrative is
`supply-sophie-sheets` (2026-10-06, run `supply-sophie-sheets-2026-10-06-001`): the same tender world
told through the TABLES -- Sophie reads each tender, quote set and order as a sheet, corrects what the
AI recorded in the cell where she reads it, fills the comparison until every quote has a landed
price, and follows the RUTF out to the workers. It replaced `supply-sophie-unanswered-round`, whose
video Jonathan found "way too focused on the e-mail aspect". In that older story Sophie (program manager, Connect-RUTF, buyer of record) runs an
RUTF tender where half the invited suppliers never answer — the record shows who owes what, the
comparison shows what each quote is missing, and her answers move the procurement forward. Domain:
`/supply/` (`connect_labs/supply_chain/`), the labs DB is the system of record.

**The goal is a product Sophie would actually use for her current RUTF procurement**, not a
perfect demo (Jonathan, 2026-09-28). That is why this repo runs DDD with `loop.objective: product`
(`.canopy/ddd/config.yaml`): the demo is the probe; product findings decide.

Other supply narratives (`supply-sophie-rutf`, `supply-test-kits`, `supply-chc-copacks`,
`supply-chlorine-stopgap`, `supply-dispenser-import`, `supply-iptsc-shortfall`, `oes-supply-base`)
and older ones (`nutrition-demo`, microplans, solicitations) live in `docs/walkthroughs/`.

## base_url

https://labs.connect.dimagi.com

Auth: labs browser session at `~/.ace/labs-session.json` (seeded out-of-band via
`/ace:labs-login`). Specs carry NO `auth` block — cookies are seeded before the render and the
recorder gets `--storage-state ~/.ace/labs-session.json`. The session cookie's local expiry
(~12h) is honoured by Playwright even while the server session lives: an all-red preflight with no
hint means re-mint the session, not a broken recipe.

Artifacts publish to the canopy-web `connect` workspace (`.canopy/ddd/config.yaml`).

## The narrative

- Story (source of truth): canopy-web, `connect` workspace, `/ddd/supply-sophie-unanswered-round`.
  On disk: `docs/walkthroughs/supply-sophie-unanswered-round.{recipe.yaml,why_brief.yaml,narrative.lock.json}`;
  setup: `scripts/walkthroughs/supply-sophie-unanswered-round/`.
- Product design history: `docs/superpowers/specs/2026-09-26-supply-sophie-history-design.md`,
  `docs/superpowers/specs/2026-09-11-rutf-procurement-design.md` (§0-1, §5.6 basis flags, §11
  append-only quotes, §17 buyer of record).
- **Persona:** Sophie is the ONLY user inside the system. Suppliers and the freight forwarder email
  her; an external AI (ACE, Claude over MCP, or the canopy panel) records the facts through the same
  operations. The AI is never a persona.
- **Product decisions already made by Jonathan (don't re-ask):** it is a _tender_, never a "round"
  (#2037); status-first screens on the labs design system (#2173); "On us / On suppliers" come from
  six coarse rules, each item naming its rule — judgement calls belong to the canopy AI layer, not
  hard-coded rules; duty follows the tender's duty terms by Incoterm, clearing and freight estimates
  live on the tender, freight is ours under EXW/FCA/FOB (#2170, #2179); a reminder counts only when
  marked sent (#2179). An award before the deadline is allowed; the Award line shows what is still
  open (deadline days, silent, replies owed) and the award records it (#2200, 2026-10-04). Moves and
  quote facts stay separate: "On us N" / "Us N" count moves only, a quote's missing fact is never a
  move and is not counted there (2026-10-04, product-lens "two ledgers" finding: leave as is).

## Product direction from Jonathan, 2026-10-06 (don't re-ask)

- **Tables, not email.** The story is the record read and corrected in place. A finding that is
  ONLY about drafted emails or clarification mail is low severity and never blocks a round; email
  is one short beat (scene 6). The 2026-10-06 run sat at 3/5 for six rounds chasing email/duty-mail
  findings until this was made explicit.
- **Every stored value is editable where it is read** (`supply_chain/cells.py`, #2244): no form
  trip for a one-value correction. One value to a cell, sheets full-width, records as ROWS -- the
  comparison is a quote to a row, a fact to a column (#2250).
- **End-to-end supply on a synthetic clone of the real RUTF opportunity**: program 10110 /
  opportunity 10111, a clone of opportunity 2230 (`/labs/synthetic/clone/`, #2255). Its supply
  chain is seeded by `manage.py supply_seed_clone_supply` (#2254) and topped up every Monday by beat
  (#2270). How much RUTF each worker RECEIVED is invented (every Distribution's note says so); what
  each visit GAVE OUT is the clone's. Narration must never present the deliveries as recorded.
- **Days to stock-out needs 30 days of dispensing** -- deliberate; say when the estimate arrives and
  the rate so far, never a bare "unknown". Don't lower the threshold.
- **A clone started over MCP must equal one made with the page's default button** (comment on #2153).
- **Data, not verdicts (2026-10-07).** "Comparable" is not a feature: no system-decided comparable
  counts, chips or tiles, and nothing (award, ranking) gated on one. The tables make every figure
  plain and sortable -- landed price where its inputs exist, the missing input named where they
  don't -- and analysis is an AI the person asks, through the canopy panel on the tender and
  comparison pages (`supply:read`, #2283). A finding that asks the product to decide
  comparability is out of scope; a finding that a figure is unclear, unsortable or wrong is in.
  Narration: "the table makes the data plain; Sophie asks the AI what it means", never
  "N of M comparable".

## Earlier phase (2026-10-04)

Four runs (2026-10-02..04, ~24 iterations, 12 PRs) under the old demo objective; the last,
`supply-sophie-unanswered-round-2026-10-03-002`, stopped not converged at iteration 6 with every
judge at 3/5 and only low-severity polish open. Under the product objective that iteration is
converged (one polish pass, then done). **Next:** a fresh run under `objective: product`; skip the
video unless asked.

## How the loop runs here

1. Gap walk before any render; build what it finds.
2. Product objective: blocking = product findings (product lens, product lint, task / trust /
   clarity / design soundness) at medium/high. Polish, arc and framing are deferred to one polish pass.
3. Fixers: the narration follows the product, never the reverse; no explanatory copy on screens —
   structure instead; general rules, not per-scene ones; labs design system (Work Sans, tailwind tokens).
4. Inner loop (`make serve-demo`) between checkpoints; checkpoints deploy through the deploy gate.

## Repo facts that bite supply renders (see learnings.md for detail)

- **A labs deploy restarts the Celery worker and kills synthetic clone jobs in flight** (since #2265
  the job then reads "Labs restarted ... start it again"). Before a deploy, check
  `/labs/synthetic/jobs/` and coordinate with other sessions that deploy labs (ListAgents +
  SendMessage); 2026-10-06 lost two clones of 2230 to deploys from two sessions.
- The walkthrough persona's session is scoped to its own program; a scene on another program
  (the clone, 10110) needs that program reachable for the persona.

- Deploy only from `main`; a labs deploy mid-seed yields new-schema/old-code errors — check
  `gh run list --workflow deploy-labs.yml` before blaming the product.
- Tom-select pickers: hold after `type` before `Enter`, and press `Tab` before a submit.
- `wait_for` text only the SAVED page has, never text the form also contains.
- Start effecting scenes with a short hold or a `wait_for` on the scene's own page, or the
  before-frame is the previous scene's page.
- `ddd upload` only finds `<slug>.yaml`; with a split recipe, compose a temporary one and put
  `why_brief.yaml` in the run dir.
- This repo is PUBLIC: no real supplier names, prices, emails, people other than first name
  "Sophie", or Drive contents in committed files.
