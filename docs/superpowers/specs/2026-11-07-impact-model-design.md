# One impact model: baseline, effectiveness, cost, and the lens that values them

*Status: design for review, 2026-11-07. No code has changed yet.*

## Why

Labs answers three questions about the same interventions:

- **TAM**: how much could be absorbed?
- **Targeting**: where should it go?
- **Cost-effectiveness**: what did, or would, the money buy?

Today each answer has its own code path:

| Path | Code |
|---|---|
| Absorption | `labs/indicators/interventions.py` |
| The ORS chain | `labs/indicators/cost_effectiveness.py` |
| The PMC simulator | `emod/mortality.py` |
| Precomputed multiples from `connect-cea-models`, re-priced in Python and again in page JavaScript | `pulse/cea.py` + `cea.html` |

The cost of that split:

- Three different calculations are labelled "× GiveWell's benchmark".
- The funging value disagrees between copies: 0.968 here, 0.93 in `connect-cea-models`, both citing CHAI Bauchi.
- Component values (ORS, vitamin A, deworming) are summed away before Labs sees them, so none of them can be toggled.

This design gives every one of those answers **one data model**. Every number in it is an input someone can see, trace and change.

## The model in one picture

```
            ┌──────────── Baseline ────────────┐
 population │ who: eligible people, by place    │  registry measures (provenance attached)
 risk       │ how likely the bad outcome is     │  registry / model / observed
 coverage   │ what they already get             │  registry
            └───────────────────────────────────┘
                              │
            ┌────────── Effectiveness ─────────┐
            │ pathways (ORS, vitamin A, ...),   │  each: outcome, effect size,
            │ each disaggregated by stratum     │  adjustments, evidence
            │ (dose, risk band), summed back up │
            └───────────────────────────────────┘
                              │  outcomes (deaths, income, ...)
            ┌────────────── Cost ──────────────┐
            │ components: delivery, commodity,  │  per unit basis (person / course
            │ supervision, platform, overhead   │  / visit); modelled or observed
            └───────────────────────────────────┘
                              │
            ┌────────── Valuation lens ────────┐
            │ moral weights per outcome, bar,   │  GiveWell by default; Coefficient
            │ benchmark, leverage method, ...   │  Giving or a custom lens alongside
            └───────────────────────────────────┘
                              │
                      Result (per pathway × stratum, then totals)
```

Funging is not in the lens: it is a fact about who else funds this programme in this place, so each model carries its own (CHC 0.93, KC and MBW 1.0, as GiveWell's starting models do), and a lens or scenario can override it.

The CEA provides three blocks: **baseline, effectiveness and cost**. The **valuation lens** is separate on purpose. It holds the funder's judgements, not facts about the programme. GiveWell's lens is the default and is always labelled as GiveWell's. Another funder's lens is a sibling, never an edit to it.

## Every number is a Parameter

```python
Parameter(
    key="kc.effect.mortality_rr",        # stable, namespaced
    label="Reduction in newborn mortality",
    value=0.20, unit="fraction",
    block="effectiveness",              # baseline | effectiveness | cost | lens
    kind="evidence",                    # evidence | context | design | observed | funder
    source=Source(citation="Mazumder 2019 (30%) and OMWaNA 2024 (12%, ns)",
                  url=..., as_of="2026-05"),
    range=(0.12, 0.30),                 # what the evidence supports
    scope="global",                     # global | country | admin | programme | opportunity
    prominence="headline",              # headline | advanced, which inputs the UI shows first
)
```

`kind` answers "assumption, or something to play with?" without turning that into a rule. Every parameter can be overridden. `kind` and `prominence` only decide what a casual reader sees first:

| kind | meaning | example |
|---|---|---|
| evidence | from a study or meta-analysis | ORS mortality effect 0.595 |
| context | a fact about a place | newborn death rate, ORS coverage |
| design | a choice about the programme | campaigns per year, cost per case |
| observed | measured in Connect | people reached, visits completed, actual spend |
| funder | a funder's value judgement | moral weight 113, bar 6×, benchmark |

## Layers: where a value comes from

A scenario resolves each parameter through layers. The **last one that sets it wins**, and the result records **which layer** that was:

1. **Model default.** Taken from `connect-cea-models` or a Labs model module, with its source.
2. **Place.** Registry values for a country or admin area: mortality, coverage, population.
3. **Lens.** GiveWell (default), or another funder's set, or a custom one.
4. **Programme or opportunity.** Observed values from Connect (reached, dose mix, cost), or a programme's config.
5. **Scenario.** What a person typed into the UI, saved and shareable by URL.

So any figure on any page can answer "where did this come from?" with a value, a layer and a source. A changed figure is visibly a change, never a quiet edit.

## Effectiveness: pathways and strata

A model's effectiveness is a list of **pathways**. Each one is a causal route to a valued outcome:

```
Pathway(key="chc.ors", outcome="u5_death",
        affected = population × risk × (1 − existing coverage) × uptake gain,
        effect   = effect size × internal validity × external validity,
        result   = affected × effect)
```

Each pathway is evaluated per **stratum**, then summed:

```
deaths averted = Σ_pathway Σ_stratum  n(stratum) × risk(stratum) × effect(pathway, stratum)
```

Strata are how we disaggregate and then aggregate back up:

- **Dose:** completed course, partial course, enrolled only.
- **Risk band:** for example under 2,000 g against all low-birthweight babies.
- **Place.**

The design holds strata **from day one**, with one stratum ("everyone enrolled") until a model has evidence for more. Nothing in the KMC, MBW or CHC evidence gives a partial-dose effect today: both KMC trials report intention-to-treat, which already averages over non-adherence. So `effect(partial)` exists as a parameter that defaults to the full effect and is labelled *no evidence*. A finer split then becomes a parameter change, not a redesign.

Two rules keep this honest:

- **Observed outcomes calibrate; they don't score.** A baby who dies stops being visited, so completers against non-completers in Connect data is not a dose effect. Observed deaths among enrolled babies can calibrate `risk` at programme level, and are shown as calibration.
- **Pathways are additive only when the model says so.** Where two pathways overlap (vitamin A and ORS both prevent diarrhoea deaths), the overlap adjustment is a parameter of the second pathway, as `connect-cea-models` already does.

## Cost

Cost is a list of **components**, each on a stated **unit basis** (per person, per completed course, per visit):

| component | modelled default from | observed from |
|---|---|---|
| delivery (worker + organisation pay) | model | Pulse completed works |
| fixed costs | n/a | Connect invoices, Labs cost entries |
| commodities | model (e.g. vitamin A capsule) | Labs cost entries (new kind) |
| supervision, platform, overhead | model / design | Labs cost entries (new kinds) |

The model's own price says which components it includes. Comparing "actual against model" therefore compares like with like, and a component Connect doesn't observe is filled from the model, **labelled as filled**. Today's mismatch goes away: CHC's $1.84 includes commodities, Connect spend doesn't, and nothing currently says so.

## Results

```
Result:
  per pathway × stratum: people, deaths (and other outcomes), units of value
  totals: deaths averted, units of value, cost, cost per death,
          multiple of the lens's benchmark, clears the lens's bar
  provenance: every resolved Parameter with its layer and source
```

Every page renders a `Result`:

- **TAM:** population from the registry, cost per eligible unit.
- **Targeting:** the same, per area, ranked.
- **CE ranking:** observed population, dose and cost per opportunity.
- **A deep dive** such as PMC. Its effectiveness pathway's source is the EMOD simulator instead of a formula; the rest of the model is unchanged.

The three "×" figures become one, computed one way. A floor is labelled as a floor, for example "deaths only, no income effects".

## Where it lives

```
connect_labs/impact/
  parameters.py     Parameter, Source, layer resolution with provenance
  lenses/           givewell.py (the default, sourced), coefficient_giving.py ...
  models/           one module per intervention: chc, mbw, kc, ors, pmc_adapter
                    each = Parameters + pathways + cost components, no I/O
  vendor.py         reads connect-cea-models' spec and results, keeps components
  baseline.py       registry measures → baseline Parameters (place layer)
  observed.py       Pulse → observed Parameters (people, dose mix, cost)
  programmes.py     per-programme config: model, enrolment unit, course definition,
                    cost basis, beneficiary anchor
  result.py         evaluate(model, layers) → Result
```

- `pulse/cea.py`, `labs/indicators/cost_effectiveness.py`, `emod/mortality.py` and the targeting MCP tools become thin callers.
- The page's what-if stops re-implementing arithmetic. It sends parameter overrides and gets a `Result` back. For instant sliders, the server sends each pathway's linear coefficients and a test proves the browser total equals the server's.

## How the UI uses it (later; outlined here)

- **One inputs drawer** on every page, grouped by block: baseline, effectiveness, cost, lens. Headline parameters are shown; everything else sits under "all assumptions".
- Each input shows its value, source, range and layer, with a reset to default.
- **A lens switcher**: GiveWell, or another funder, or custom.
- **Scenarios** are saved and shared by URL. Anyone can open "Jon's KC assumptions" and see exactly what differs from default.

## Migration, in order

Each step ships on its own and changes no published figure unless it says so:

1. **`impact/parameters.py` + `lenses/givewell.py`.** Move the constants out of `cost_effectiveness.py`, sourced. The bar, benchmark and moral weights are then read by every path, and the hard-coded "6×" in the PMC page goes. The funging value has to be settled first, since it changes published figures.
2. **`vendor.py`.** Read each model's `spec.json` from `connect-cea-models` (its inputs, every Model row as a formula, scenarios and GiveWell checks; connect-cea-models#1) and evaluate the models' own formulas, instead of re-typing them. A test reproduces every column in the spec's `results`, and pins the commit.
3. **`models/` for CHC, MBW, KC and ORS as pathways, plus `result.py`.** A test reproduces each model's published multiple per country exactly before anything moves.
4. **`programmes.py` + `observed.py`.** Programme config and observed dose mix, building on `beneficiary_key` (#2940).
5. **Callers switch.** CE ranking first, then targeting and TAM, then the PMC adapter.
6. **Cost component kinds** in Labs cost entries.
7. **The inputs drawer, lens switcher and saved scenarios.**

## Decisions (Jon, 7 Nov 2026)

1. **Everything comes from the models.** `connect-cea-models` is the source; Labs evaluates the models' own exported formulas (connect-cea-models#1) and keeps no copy of a model constant. The repo is shared work with Shay and Neal, and Labs-side needs go there as PRs.
2. **Funging:** the model's own value, per model. Labs' separate 0.968 goes.
3. **KC baseline risk:** the model's 13% for now. The model deliberately does not scale it by a place's newborn death rate (SM, 30 Oct 2026); the "4× newborn death rate" rule in `planning/model_results.js` is planning-only, and the team is working on it.

## Still open

1. **Which other lenses to build first** (Coefficient Giving's?), and who owns their values?
