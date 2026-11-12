# Baseline, cost-effectiveness and scenarios

Three components, built in layers: each may use the one before it and none may use the one after.
`connect_labs/impact/tests/test_component_boundaries.py` enforces the direction and lists today's exceptions.

| | **Baseline** | **Cost-effectiveness (CEA)** | **Scenarios** |
|---|---|---|---|
| Answers | What do the indicators say about a place? Filter by indicator, place, method. | What does a model say a programme buys, on a place's baseline? Change inputs, test sensitivity. | A chosen set of places, a model and costs, for a proposal or market sizing. |
| Uses | nothing above it | the baseline | the baseline and the CEA |
| Data model | `IndicatorValue`, `AdminBoundary`, `Source`, the measure registry (`measures.py`), `Resolved` | the vendored model specs (`impact/data/*.json`), `valuation`, the place layer (`impact/baseline.py`, `places.py`) | not built (see below) |
| UI | `/labs/targeting/`, the Baseline explorer | `/labs/cea/`: Models, Implementation, Targeting tabs | not built |
| Agent tools | `targeting_indicators`, `_select`, `_methodology`, `_admin_levels`, `_compare_criteria`, `_research*` | `cea_model_run` (the Models page's engine), `targeting_pmc_*` | `targeting_scenario` (sizing) |

## The cost-effectiveness component

One evaluator, reached three ways, so a number is the same wherever it appears.

- **The core** (`impact/engine.py`, `valuation.py`, `result.py`, `explore.py`) is pure Python with no Django and no database. `engine` runs a model's own formulas from its spec; `valuation` is GiveWell's value of a death, the benchmark and the bar, read from the specs; `explore` adds run, one-way sensitivity (`tornado`), a two-input `grid`, the model's scenarios and country figures. The boundary test keeps it pure.
- **Binding to places**: `impact/baseline.py` resolves each model's location inputs from the most local figure the baseline holds (district, else state, else the model's country figure) and records where each came from. `impact/places.py` places a delivered opportunity's visits in areas.
- **For agents**: `cea_model_run` (`mcp/tools/cea.py`) calls `explore` directly and returns the Models page's link with the same inputs set, so what an agent computes is what the page shows.
- **Three views** (tabs of one explorer): **Models** (`/labs/cea/models/`, `impact/views.py`): pick a model, edit any input, see the chain and sensitivity. **Implementation** (`/labs/cea/implementation/`, `pulse/cea.py`): every delivered opportunity, priced at what it cost. **Targeting** (`/labs/cea/targeting/pmc/`): malaria PMC, deaths from the EMOD simulation, valued as the CHC model values a death.

### Applying the models to existing programmes and opportunities

Implementation is the baseline and the models applied to what Connect delivered, and it has no arithmetic of
its own. `pulse/cea.py` reads Pulse's own data (approved works, costs) and hands the rest to `impact`:

| Step | Where | Shared with |
|---|---|---|
| Where the visits were | `impact/places.py`, on the shared placement core (`labs/admin_boundaries/placement.py`) | Pulse maps, the coverage check |
| The place's baseline | `impact/baseline.py`, most local figure first, each with its source | the Models explorer's country and place inputs |
| The model at that place | `impact/result.py` `evaluate`, which runs `impact/engine.py` | the Models explorer |
| Re-pricing at what it cost | `impact/result.py` `reprice`, the one formula | `evaluate`, and the Implementation what-if |
| A programme, partner or country | `pulse/cea.py` `rollup` | the page's tiles, the programme table, the JSON, `cea_opportunities` |
| Test the answer's sensitivity | `explore.delivery_overrides`: the opportunity's place inputs and a cost input chosen so the model's price equals the observed cost, linked from each opportunity page | the explorer gives the opportunity's own multiple (tested to 1e-6) |

Agents read it through `cea_opportunities` (any selection, with its roll-up and, for one opportunity, every step and its
explorer link). It is for full tokens only: it carries what each opportunity cost and how many people it reached.
The boundary test keeps `pulse/cea.py` off the baseline package.

Open: the Implementation what-if still recomputes in the page's JavaScript (`compute()` in `cea.html`), which repeats
`reprice` plus the what-if knobs. It moves behind the server so the page only draws what the API returns.

### Where the models come from, and how they stay current

The three models (CHC, MBW, KC) are Google Sheets their owner edits by hand; each is built from a builder in `connect-cea-models`, which exports a `spec.json` Labs vendors. Labs reads no Sheet at run time.

1. **Check** (`manage.py impact_check_sheets`): compares what each live Sheet's Model tab *computes*, cell by cell, with what the vendored spec computes, and names every difference. Exits 1 on drift. It checks results, not formulas or notes, so a text-only edit does not show.
2. **Refresh** (`manage.py impact_vendor --from <connect-cea-models checkout>`): copies the specs, records the commit, and re-derives the per-country summary. The vendored commit and version show on every Models page.
3. When the check reports drift, someone with the repo runs the refresh and opens a PR; the tests in `impact/tests` pin the Sheets' headline numbers, so a model change is seen in review.

Nothing runs the check on a schedule yet: a scheduled run needs a credential in CI.

## Where each stands today

**Baseline: built, with two other components inside it.** `labs/indicators` holds the indicator system and the Baseline explorer, and also:
- **Market sizing**: `interventions.py` (unit-price presets), `ScenarioView` / `InterventionsView`, the "What could this absorb?" panel, `costing.js`, and the `targeting_scenario` tool. This is scenarios functionality.
- **PMC**: `pmc.py`, `emod/*`, the PMC views, `PmcModelRun`, the PMC page and script, and the `targeting_pmc_*` tools. This is cost-effectiveness functionality. It is the reason three baseline files import `impact.valuation`, which the boundary test lists.

**Cost-effectiveness: built**, except that the PMC half lives in the baseline package.

**Scenarios: not built as a component.** What exists is sizing embedded in the Baseline page, and the ACE `build-targeting-model` skill assembling a Sheet outside Labs.

## Duplication found, and what is left

| Duplicate | Status |
|---|---|
| GiveWell value of a death, benchmark and bar typed in each engine | **Fixed**: one `valuation` module read from the specs |
| ORS cost-effectiveness chain (GiveWell's grant, a separate calculation) | **Removed**: which components count for CHC is a CHC model decision |
| The delivered ranking, PMC and the ORS tool disagreeing on what a death is worth | **Fixed**: PMC now uses the CHC valuation (118.05 units) |
| Re-pricing arithmetic (`multiple x model price / actual price`) in Python (`impact/result.py`) and again in the Implementation tab's JavaScript (`cea.html`) | Open |
| Default prices: sizing presets (ORS $2.50, KMC $60) against the models' own inputs (CHC $1.80, KC $60, MBW $10). ORS disagrees. | Open: read a preset's price from its model's `cost` input where a model exists |
| ISO to country-name maps in `pulse/normalize.py` and `labs/indicators/africa.py` (and the vendored summary) | Open |
| Area baselines resolved separately in PMC (`registry_burden`, `emod/states.py`, `emod/runner.py`) and in `impact/baseline.py` | Open: PMC should read baselines through `impact.baseline` |

## Order of work

1. **Move PMC into the cost-effectiveness component** (about 2,400 lines of code, 2,500 of tests, `PmcModelRun`, two Celery tasks, one beat schedule entry, about 60 import sites). Keep the Celery task names (`connect_labs.labs.indicators.emod.tasks.*`) with an explicit `name=` so queued work survives the deploy. Then delete the `BASELINE_IMPORTS_CEA` ledger.
2. **Build the scenarios component**, move sizing into it, and take the "What could this absorb?" panel off the Baseline page, so the Baseline explorer only looks at indicators and filters. Then delete the `SIZING_IMPORTERS` ledger.
3. Close the open duplicates above.
