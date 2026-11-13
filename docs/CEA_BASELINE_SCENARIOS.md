# Baseline, cost-effectiveness and scenarios

Three components, built in layers: each may use the one before it and none may use the one after.
`connect_labs/impact/tests/test_component_boundaries.py` enforces the direction and lists today's exceptions.

| | **Baseline** | **Cost-effectiveness (CEA)** | **Scenarios** |
|---|---|---|---|
| Answers | What do the indicators say about a place? Filter by indicator, place, method. | What does a model say a programme buys, on a place's baseline? Change inputs, test sensitivity. | A chosen set of places, a model and costs, for a proposal or market sizing. |
| Uses | nothing above it | the baseline | the baseline and the CEA |
| Data model | `IndicatorValue`, `AdminBoundary`, `Source`, the measure registry (`measures.py`), the unit bases (`bases.py`), `Resolved` | the vendored model specs (`impact/data/*.json`), `valuation`, the place layer (`impact/baseline.py`, `places.py`) | the intervention presets (`scenarios/interventions.py`), priced from the models where one exists |
| UI | **Health indicators**, `/labs/targeting/` | **CEA models** (`/labs/cea/models/`) and **Implementations** (`/labs/cea/implementation/`) | **Market size**, `/labs/cea/market/` |
| Agent tools | `targeting_indicators`, `_select`, `_methodology`, `_admin_levels`, `_compare_criteria`, `_research*` | `cea_model_run` (the Models page's engine), `cea_opportunities` | `targeting_scenario` (sizing) |

## The cost-effectiveness component

One evaluator, reached three ways, so a number is the same wherever it appears.

- **The core** (`impact/engine.py`, `valuation.py`, `result.py`, `explore.py`) is pure Python with no Django and no database. `engine` runs a model's own formulas from its spec; `valuation` is GiveWell's value of a death, the benchmark and the bar, read from the specs; `explore` adds run, one-way sensitivity (`tornado`), a two-input `grid`, the model's scenarios and country figures. The boundary test keeps it pure.
- **Binding to places**: `impact/baseline.py` resolves each model's location inputs from the most local figure the baseline holds (district, else state, else the model's country figure) and records where each came from. `impact/places.py` places a delivered opportunity's visits in areas.
- **For agents**: `cea_model_run` (`mcp/tools/cea.py`) calls `explore` directly and returns the Models page's link with the same inputs set, so what an agent computes is what the page shows.
- **Four tabs, one menu group** (Cost-Effectiveness in the rail, the same four tabs on each page): **Health indicators** (the baseline, `/labs/targeting/`); **CEA models** (`/labs/cea/models/`, `impact/views.py`): pick a model and change any input in one table whose inputs are grouped by the four that decide an answer (baseline, counterfactual, effectiveness, cost, then other assumptions: `impact/groups.py`), each with a switch for whether it is in the sensitivity, and see the spread of everything switched on together; **Implementations** (`/labs/cea/implementation/`, `pulse/cea.py`): every delivered opportunity, priced at what it cost; **Market size** (`/labs/cea/market/`, `scenarios/market.py`): people in chosen places (default: everywhere with data) times each chosen model's cost per person per year, with the part where the model clears the bar. Cost data and Donor reports are Pulse pages under Network.

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

The Implementation tab's what-if, filters, ranking and roll-up run on the server (`pulse/cea.py` `view()`, served as
JSON at `cea:implementation_view`); the page only sends the knobs and draws what comes back. Re-pricing is `reprice`
everywhere, with a test that the server agrees with the formulas the page used to carry.

### Where the models come from, and how they stay current

The three models (CHC, MBW, KC) are Google Sheets their owner edits by hand; each is built from a builder in `connect-cea-models`, which exports a `spec.json` Labs vendors. Labs reads no Sheet at run time.

1. **Check** (`manage.py impact_check_sheets`): compares what each live Sheet's Model tab *computes*, cell by cell, with what the vendored spec computes, and names every difference. Exits 1 on drift. It checks results, not formulas or notes, so a text-only edit does not show.
2. **Refresh** (`manage.py impact_vendor --from <connect-cea-models checkout>`): copies the specs, records the commit, and re-derives the per-country summary. The vendored commit and version show on every Models page.
3. When the check reports drift, someone with the repo runs the refresh and opens a PR; the tests in `impact/tests` pin the Sheets' headline numbers, so a model change is seen in review.

Nothing runs the check on a schedule yet: a scheduled run needs a credential in CI.

## Where each stands today

**Baseline: built.** `labs/indicators` holds the indicator system, the unit bases (`bases.py`: which count a unit of cost resolves to) and the Baseline explorer.

**Cost-effectiveness: built.**

**Scenarios: market sizing, as its own package, with a page (Market size).** `scenarios/` holds the intervention presets and the sizing views (`/labs/scenarios/api/scenario/`, `/api/interventions/`; the old `/labs/targeting/` addresses redirect). A preset that follows a model takes its price from that model (`model_price`): KMC from KC ($60), the two ORS presets from CHC ($1.80, which covers ORS, zinc and the vitamin A capsule). The old panel is hidden on the Health indicators page (its script is dead code, to be removed). Not built: saved scenarios (a record of places, model and costs for a proposal), and a scenario that combines sizing with a model's multiple for interventions that have one.

## Duplication found, and what is left

| Duplicate | Status |
|---|---|
| GiveWell value of a death, benchmark and bar typed in each engine | **Fixed**: one `valuation` module read from the specs |
| ORS cost-effectiveness chain (GiveWell's grant, a separate calculation) | **Removed**: which components count for CHC is a CHC model decision |
| The delivered ranking and the ORS tool disagreeing on what a death is worth | **Fixed**: one valuation, the CHC model's (118.05 units) |
| Re-pricing arithmetic in Python and again in the Implementation tab's JavaScript | **Fixed**: one `reprice`, the page draws the server's answer |
| Default prices: sizing presets (ORS $2.50, KMC $60) against the models' own inputs | **Fixed** for presets that follow a model (test pins it); measles, vitamin A, nets and water have no model |
| ISO to country-name maps | **Not a duplicate**: `labs/indicators/africa.py` is the full African set keyed by alpha-3; `pulse/normalize.py` `COUNTRY_NAMES` is a short display list keyed by alpha-2 and includes India |

## Order of work

1. **Saved scenarios**: a record of places, models and inputs that a proposal can link to (Market size state is already in the link). Remove the hidden costing script from the Health indicators page.
2. Price measles, vitamin A, nets and water from a model if the team builds one.
