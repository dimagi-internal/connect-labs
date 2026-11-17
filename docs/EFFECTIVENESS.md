# Indicators and effectiveness

Three layers, each reading only the ones below it. `connect_labs/effectiveness/tests/test_component_boundaries.py`
enforces the direction.

| Layer | Package | What it is |
|---|---|---|
| **Baseline** | `labs/indicators`, `labs/admin_boundaries` | What the indicators say about a place: burden, coverage, population, with sources, methods and filters. A first-class system others build on. Knows nothing about models, costs or Pulse. |
| **Pulse** | `pulse` | What Connect delivered, and what it cost. Does not know about the models. |
| **Effectiveness** | `effectiveness` | The cost-effectiveness models run on the baseline, applied to what Pulse delivered (Implementations) and to places not yet served (Market size). The only layer that reads the other two. |

## The effectiveness app

```
effectiveness/
  engine.py valuation.py result.py groups.py explore.py sheets_check.py market.py   pure: no Django, no database
  scenario.py                                                                        pure: the scenario, what someone is working on (below)
  baseline.py places.py                                                              bind the models to places (read the baseline, Pulse)
  people.py                                                                          people per country in the baseline's selected places
  sizing.py                                                                          a scenario's market: people x models x dials, the one payload
  shapes.py                                                                          country outlines for the workspace map
  interventions.py                                                                   sizing presets, priced from the models
  implementation/                                                                    delivered opportunities, valued (below)
  views/ urls.py                                                                     the pages, mounted at /labs/cea/
  data/                                                                              the vendored model specs (CHC, MBW, KC)
  management/commands/effectiveness_check_sheets, effectiveness_vendor
templates/effectiveness/                                                             the pages: models, implementation, implementation_opp, market, workspace, tabs
static/effectiveness/                                                                ui.js (DOM builder, number formats), the shared scenario parts, and one script per page
```

### The scenario

The pages are views of one object, a `Scenario` (`effectiveness/scenario.py`): the **places** (an indicator over a
threshold in some countries; `indicator=all` is everywhere with data) at a **grain** (a `country`, or a `territory`: a state or
province), the **programmes** (models), a **target** (how many people to reach), three **dials** (`extra` dollars a person a
year, which may be below zero when delivery is cheaper; `effect` as % of the model's; `reach` as the most of a place's cases
that can be reached; link key `<model>_<dial>`), any model **assumptions** (`i.<model>.<input>`) and the **bar**. It is read
from a link and written back to one, and only what differs from the default is written, so the default scenario is the bare
page. A dial starts at its model's own value (`scenario.DIAL_DEFAULTS`): Kangaroo Care enrols small babies at home through
visits and starts at reaching half of a place's cases (owner, 17 Nov 2026); the others start at all of them. The dials are the
Implementations what-if's (`whatif.py` imports them), and `result.scale_multiple` is the one statement of how cost and effect
move a multiple, used by `result.reprice` (a delivery) and `market.size` (a market).

`sizing.market_of(scenario)` is the answer Market size and the workspace both draw (`cea:market_data`); `people.question_for`
turns the scenario's places into the baseline's own question. In the browser the same parts are shared:
`scenario.js` (state, link, fetch), `dials.js` (the controls), `marketview.js` (tiles, programme lines, country table) and
`areamap.js` (a country choropleth that knows nothing of models). `tests/scenario_contract.json` is the one definition of
the link, tested from Python (`test_scenario.py`) and from the page's script (`scenario.test.js`).

### Where to work: risk by place, reach, and the plan

**Why the places differ.** A model's multiple is its deaths averted per dollar over the benchmark. For Kangaroo Care, deaths
averted per small baby is `post_ref x effect x validity`, so the multiple is proportional to `post_ref`: the 28-day death
risk of an enrolled small baby without the programme. The published model holds that at one number (13%, "not scaled by
place", SM, 30 Oct 2026), so every country came back at $60 and 10.3x and the model could not say where it is worth more.
Its own scenarios say it varies: 18% "like Nigeria", 15.2% "like Liberia", 9.6% "like Uganda", which are 4.62, 4.62 and
4.51 times each country's national neonatal mortality (39.0, 32.9, 21.3 per 1,000). So **a labelled Labs reading, on by
default and switchable off (`place_risk=0`)**: the risk is scaled by the place's neonatal mortality from the point where it
is the model's 13% (28.4 per 1,000, `market.PLACE_RISK`; `test_market` pins the three anchors to 3%). Nothing else about the
model changes, and a risk you set yourself is scaled to the place, not replaced by it. Implementations still value KC with the
model's flat figure.

**Territories.** Neonatal mortality is held nationally for every African country and below the country for about 24
(`Baselines.newborn_mortality`): the territory's own figure, else the country's scaled by how the territory's under-five
mortality compares (the models' own recipe for a subnational place), else the country's. At the `territory` grain every state
or province is a place, so where small babies are likelier to die shows within a country. **Every model** takes its
territory's own place inputs where Labs holds them, by the same recipes Implementations value a delivery with
(`Baselines.for_area`: surveyed ORS, vitamin A and deworming coverage; the country's diarrhoea and child death rates, and
newborn and infant death rates, scaled by the territory's mortality); an input with nothing local keeps the model's country
figure. A country takes the model's own figures, not a roll-up of its territories. `place_risk=0` switches all of it off.

**Believing the figures.** A territory's figure is only as good as the survey or estimate behind it (a remote area's survey
can under-report; the UN estimate for Zimbabwe's neonatal mortality is 33.7 with a range of about 22 to 50, and earlier
releases put it near 24). So `trust` (0 to 100, link key `trust`) pulls every local figure toward what is known one level up:
a territory toward its country's, a country toward the model's. A figure more than twice, or under half, its country's is
marked in the table (`far_from_country`), with the figure, its country's and its source in the tooltip. Every plan also carries
`check`: the same places valued at half trust, set beside the best plan for those figures. Near 100% the plan does not depend
on the figures being right; low, it leans on them, and the page says so.

The IGME loaders (national and subnational) keep the published 90% bounds (`ci_low`/`ci_high`, `extra.interval_level`), and
where a place has them they are carried through (`mortality.py`, `reliability.py`):

* `mortality.shrink` pulls each territory's own figure toward its country's by as much as its interval warrants (empirical
  Bayes on the log scale, DerSimonian-Laird for the real spread between territories; a country with fewer than eight
  territories that have an interval is left alone). A territory with a wide interval moves a long way, one with a tight
  interval hardly at all; the published figure is kept beside it (`risk_raw`) and the tooltip says so.
* Each row has a 90% range for its multiple (`multiple_low`/`multiple_high`) from the elasticity the model measures at the
  place (one extra model run), and the plan's `reliability` redraws every place's mortality within its interval (400 draws,
  a part shared by a country's territories, a part its own), rebuilds the plan each time, and reports the deaths averted
  at the 5th and 95th percentile, how much of the shown plan a typical redraw's own best plan contains (`overlap`), and per
  place the share of redraws it is chosen in (`p_in_plan`). No interval, no range: places without one are left as they
  were, and `reliability` is `null` when no place has any.
* Not done: subnational estimates are benchmarked to the national estimate of their own release, which is later revised
  (Zimbabwe's current national series says 32.2 for 2021 while its provinces' 2021 values average 22.6). Putting territories
  back on the current national needs the whole country's set, so it waits for that. Production has no bounds until the
  mortality loaders are re-run (`load_indicators --stage mortality`).

**The plan.** Give a target (`target=1000000`) and `market.plan` fills the places with the highest multiple first, each up to
the most that can be reached there (the `reach` dial), until the target is met, then reports the people, cost, deaths
averted, cost per death averted and the spend-weighted multiple of the whole, how far short the places fall if they cannot
give it, and what spreading the same people evenly would have averted instead. Three limits (`scenario.PlanLimits`, one object so a new
limit is one field, not an argument through every layer) make it something a team can run: `max_conflict` leaves out a place
whose reported conflict deaths (UCDP, per 100,000 people a year; `ucdp.py`) are above the ceiling, never changes a multiple, and
keeps a place Labs has no figure for (nothing says it is unsafe); `min_place` leaves out any place that can give fewer people (and
counts them); and `max_countries` keeps to the countries the
best plan leans on most (the best plan is made, its countries ranked by the deaths they avert, and it is made again within
the first N); `countries_needed` says how many countries it takes to reach the target at all. The plan adds up by country, and
downloads as a CSV (`/labs/cea/market/plan.csv`, same link keys). A place with no model figures is never chosen.
`sizing.overall` is the headline the pages show across the programmes chosen.

On real data the best 1M-case Kangaroo Care plan is spread over 145 territories in 26 countries, but ten countries reach the
same million at 97% of its deaths averted: concentrating costs little, which is what the limits are for.

### Saved scenarios

A person keeps a scenario under a name (`models.SavedScenario`, rules in `saved.py`, HTTP in `views/scenarios.py`, the
panel in `scenarios.js`, shared by Market size and the workspace). What is stored is the scenario's canonical link, never
the numbers, so opening one reads it with today's models; `saved_against` (the vendored model commit and each programme's
spec version) is how a scenario says "the models have moved since this was saved" (`models_changed`).

* Private until its owner shares it (the panel's Share / Make private button, or `PATCH` `{shared}`); a shared one is read and
  copied by every signed-in user, replaced, renamed (`PATCH` `{name}`) or deleted only by its owner. Someone else's private scenario is "not found", the same answer as one that does not exist.
* A name is per person. Saving a name you already have is refused (409) unless `overwrite`, and the panel asks first.
* Open one with `?scenario=<id>` on either page (any other key in the link then changes it); the address bar becomes the plain
  link at once. One that cannot be opened (not yours, or an input of a model that has gone) opens the default and says why.
* An agent has the same: `cea_scenarios` (list, or one by `id`, `run: true` to size it now) and `cea_scenario_save`
  (full tokens only, like the other tools that write; not in the restricted sets).

One evaluator, reached every way, so a number is the same wherever it appears. `engine` runs a model's own formulas
from its spec; `valuation` is GiveWell's value of a death, the benchmark and the bar, read from the specs; `result`
turns a run into a multiple and holds the one re-pricing formula (`reprice`); `explore` adds sensitivity;
`groups` places each input among the four that decide an answer; `market` sizes a market.

`implementation/` is split along its seams, each module one concern: `catalog` (which model values which delivery),
`reach` and `pooling` (what Pulse knows: people, places, where a cost-only opportunity's spend goes), `delivery`
(valuing one delivery), `totals` (roll-ups), `build` (the ranking), `whatif` (the what-if, filters and ordering, pure),
`page` (the page's view of the ranking) and `detail` (one opportunity end to end).

The baseline is read through its public modules: `labs/indicators/query.py` turns a query string into a `Question` that
selects places, `access.py` is the page gate, `measures.py` and `bases.py` describe the indicators. Effectiveness never
imports the baseline's views. `people.py` is the one seam from a selection to people per country, shared by the Market
size page and the `cea_market_size` tool.

The boundary test keeps the layers apart, the core pure, and says which modules may read the baseline (`baseline.py`,
`places.py`, `interventions.py`, `people.py`, `views/market.py`).

## The pages

The Cost-Effectiveness menu group, and the same four tabs on each page:

| Page | URL | What it does |
|---|---|---|
| **Health indicators** | `/labs/indicators/` | The baseline's explorer: map, tiles and table of an indicator over places. |
| **CEA models** | `/labs/cea/models/` | Pick a model and change any input in one table, grouped as baseline, counterfactual, effectiveness, cost, then other assumptions. Each input has a switch for whether it is in the sensitivity; the page shows the spread of everything switched on together, and each group's. |
| **Implementations** | `/labs/cea/implementation/` | Every delivered opportunity, priced at what it cost, with a server-side what-if, filters and a By programme table; each opportunity has a page that links to CEA models set to it. |
| **Market size** | `/labs/cea/market/` | People in chosen places (default: everywhere with data) times each chosen model's cost per person per year, with the part where the model clears the bar. |
| **Workspace** (preview) | `/labs/cea/workspace/` | The same scenario around a map: lenses (Market, Worth it) paint the countries, a click opens a country's card, and the scenario panel (places, programmes, three dials, the bar, every assumption) is one component shared with Market size. A candidate layout for every page, kept beside the others to compare. |

Cost data and Donor reports are Pulse pages under Network. Malaria PMC was retired on 13 Nov 2026.

## Agent tools

An agent computes what the page shows, from the same code, and gets the page's link back:

- `cea_model_run`: a model at inputs you set, with the sensitivity (inputs switched on, grouped), scenarios, places.
- `cea_scenarios`, `cea_scenario_save`: save, list and open scenarios (above).
- `cea_market_size` with `model` takes the scenario's options too: `grain`, `target` (returns the `plan`), `min_place`, `max_countries`, `trust`, `dials`, `place_risk`.
- `cea_opportunities`: any selection of delivered opportunities with its roll-up (full tokens only).
- `cea_market_size`: Market size. With `model` it is the page's answer (the model's own price, run at each country's
  figures, with the part that clears the bar); without it, a unit cost you set.
- `indicators_*`: the baseline's own tools (list, select, methodology, admin_levels, compare_criteria, research).

## Applying the models to delivered opportunities

`effectiveness/implementation/` has no arithmetic of its own: it reads Pulse's data (approved works, costs) and hands
the rest to the core.

| Step | Where | Shared with |
|---|---|---|
| Where the visits were | `places.py`, on the shared placement core (`labs/admin_boundaries/placement.py`) | Pulse maps, the coverage check |
| The place's baseline | `baseline.py`, most local figure first, each with its source | the models page's country and place inputs |
| The model at that place | `result.evaluate`, which runs `engine` | the models page |
| Re-pricing at what it cost | `result.reprice`, the one formula | `evaluate`, and the what-if |
| A programme, partner or country | `implementation.totals.rollup` | the tiles, the programme table, the JSON, `cea_opportunities` |
| Test the answer's sensitivity | `explore.delivery_overrides`: the place inputs and a cost input chosen so the model's price equals the observed cost | the models page gives the opportunity's own multiple (tested to 1e-6) |

## Where the models come from, and how they stay current

The three models are Google Sheets their owner edits by hand; each is built from a builder in `connect-cea-models`, which
exports a `spec.json` Labs vendors. Labs reads no Sheet at run time.

1. **Check** (`manage.py effectiveness_check_sheets`): compares what each live Sheet's Model tab *computes*, cell by cell, with
   what the vendored spec computes, and names every difference. Exits 1 on drift. It checks results, not formulas or notes.
2. **Refresh** (`manage.py effectiveness_vendor --from <connect-cea-models checkout>`): copies the specs, records the commit
   and re-derives the per-country summary. The vendored commit and version show on every models page.
3. When the check reports drift, someone with the repo runs the refresh and opens a PR; the tests pin the Sheets' headline
   numbers, so a model change is seen in review.

A daily task (`tasks.check_model_sheets`, 06:12 UTC) runs the same check inside Labs, where the service account already is. On drift it logs an
ERROR (Sentry) and the models page shows a one-line note until the specs are refreshed; a Sheet it cannot read fails the task.

## Rebuilding it

The effectiveness layer keeps no numbers of its own except saved scenarios (a link and a name, `models.SavedScenario`). Every
figure on a page is computed from three things, and a rebuild is restoring them:

1. **The baseline** (`labs/indicators`): boundaries and indicator values. Restore a snapshot (`BOOTSTRAP.md`, about two
   minutes) or rebuild from source with `indicators_bootstrap`, which runs every stage in `labs/indicators/stages.py` in order
   (`--skip-heavy` for a refresh that leaves out the hours-long raster and quota-limited stages). That module is the one list
   of stages, what each needs and why; `load_indicators`, the bootstrap command and the table in `BOOTSTRAP.md` all read it,
   and `tests/test_stages.py` fails if a stage, a source or the table is added without the rest.
   What the sizing reads from it is `baseline.MEASURES_READ`, checked by `test_baseline_reads.py`: each must be registered and
   policy-governed, and none may be read unlisted.
2. **The models** (`effectiveness/data/*.json`, vendored from `connect-cea-models`): `effectiveness_vendor`.
3. **The code's own assumptions**, each named where it is made: `market.PLACE_RISK` (Kangaroo Care's risk scaling),
   `scenario.PlanLimits` (the plan's limits), `mortality` and `reliability` (how mortality's uncertainty is used).

Nothing else needs restoring. Computed answers are cached under `response_cache.data_version()`, which `load_indicators`
and the bootstrap start afresh on every run, so a reload cannot leave a stale answer; the people cache key carries a version
(`market:people:vN`) that is bumped when the shape of what it holds changes.

## Left to do

- Saved scenarios: rename and share/unshare without re-saving, versions, and a way to attach one to a proposal.
- Delivery cost by place (Connect's actual price per case by country or programme) and territory reachability (travel time,
  security) as inputs to the plan; today the places differ by their own mortality and coverage figures, not by what it
  costs to work in them or how hard they are to reach (conflict is a ceiling a plan can be held to, not yet a travel time).
- Put CEA models and Implementations on the scenario too (their own link keys differ: `i_<input>`, `m`), and give the
  workspace a Need lens, a Delivered layer, and region-level shapes (the models hold country figures, so a region's number
  would be an allocation of its country's).
- Price measles, vitamin A, nets and water from a model if the team builds one.

## See also

- `docs/ON_DEMAND_COMPUTE.md`: how to run an expensive external model (a simulation) from Labs. The malaria PMC explorer was the one consumer; it is retired and kept at the tag `archive/pmc-emod`.
