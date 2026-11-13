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
  baseline.py places.py                                                              bind the models to places (reads the baseline, Pulse)
  implementation.py                                                                  delivered opportunities, valued (reads Pulse)
  interventions.py                                                                   sizing presets, priced from the models (reads the baseline)
  views/ urls.py                                                                     the pages, mounted at /labs/cea/
  data/                                                                              the vendored model specs (CHC, MBW, KC)
  management/commands/effectiveness_check_sheets, effectiveness_vendor
templates/effectiveness/                                                             models, implementation, implementation_opp, market, tabs
```

One evaluator, reached every way, so a number is the same wherever it appears. `engine` runs a model's own formulas
from its spec; `valuation` is GiveWell's value of a death, the benchmark and the bar, read from the specs; `result`
turns a run into a multiple and holds the one re-pricing formula (`reprice`); `explore` adds sensitivity;
`groups` places each input among the four that decide an answer; `market` sizes a market. The test keeps this core pure
and says which modules may read the baseline (`baseline.py`, `places.py`, `interventions.py`, `views/market.py`).

## The pages

The Cost-Effectiveness menu group, and the same four tabs on each page:

| Page | URL | What it does |
|---|---|---|
| **Health indicators** | `/labs/indicators/` | The baseline's explorer: map, tiles and table of an indicator over places. |
| **CEA models** | `/labs/cea/models/` | Pick a model and change any input in one table, grouped as baseline, counterfactual, effectiveness, cost, then other assumptions. Each input has a switch for whether it is in the sensitivity; the page shows the spread of everything switched on together, and each group's. |
| **Implementations** | `/labs/cea/implementation/` | Every delivered opportunity, priced at what it cost, with a server-side what-if, filters and a By programme table; each opportunity has a page that links to CEA models set to it. |
| **Market size** | `/labs/cea/market/` | People in chosen places (default: everywhere with data) times each chosen model's cost per person per year, with the part where the model clears the bar. |

Cost data and Donor reports are Pulse pages under Network. Malaria PMC was retired on 13 Nov 2026.

## Agent tools

`cea_model_run` (the models page's engine, with a link to the page at the same inputs), `cea_opportunities` (any selection
of delivered opportunities with its roll-up; full tokens only), `cea_market_size` (sizing), and the baseline's own tools.

## Applying the models to delivered opportunities

`effectiveness/implementation.py` has no arithmetic of its own: it reads Pulse's data (approved works, costs) and hands
the rest to the core.

| Step | Where | Shared with |
|---|---|---|
| Where the visits were | `places.py`, on the shared placement core (`labs/admin_boundaries/placement.py`) | Pulse maps, the coverage check |
| The place's baseline | `baseline.py`, most local figure first, each with its source | the models page's country and place inputs |
| The model at that place | `result.evaluate`, which runs `engine` | the models page |
| Re-pricing at what it cost | `result.reprice`, the one formula | `evaluate`, and the what-if |
| A programme, partner or country | `implementation.rollup` | the tiles, the programme table, the JSON, `cea_opportunities` |
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

Nothing runs the check on a schedule yet: a scheduled run needs a credential in CI.

## Left to do

- Saved scenarios: a record of places, models and inputs that a proposal can link to (Market size state is already in the link).
- Price measles, vitamin A, nets and water from a model if the team builds one.

## See also

- `docs/ON_DEMAND_COMPUTE.md`: how to run an expensive external model (a simulation) from Labs. The malaria PMC explorer was the one consumer; it is retired and kept at the tag `archive/pmc-emod`.
