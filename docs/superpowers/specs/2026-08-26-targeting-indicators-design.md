# Targeting indicators: population and burden primitives

**Status:** design approved 2026-08-26, implementation in progress
**Branch:** `emdash/targeting-frt31`

## Purpose

Answer questions of the form *"how much of intervention X could high-need Africa
absorb, and what is that based on?"* — starting with under-5 mortality and
births, and generalising to malaria, vitamin A, ORS and beyond.

The v1 acceptance test, stated by Jon:

> See a map, select a threshold for under-5 mortality, see the estimated number
> of births per year in the geographies above that threshold, and download a
> table of those areas aggregated up to the highest unit that makes sense, with
> the supporting documentation.

## Scope decisions

- **Geography:** every African country at ADM0 and ADM1. ADM1 is where
  subnational under-5 mortality actually exists in public data; going deeper
  would mean inheriting the same rate down to wards, which adds no information.
- **No IHME.** See the source research: the IHME non-commercial agreement
  excludes for-profit entities and their employees, and forbids re-hosting.
  Every source below permits commercial use except WHO GHO, which is fenced.
- **Not KMC-shaped.** KMC is one worked example. The primitives are
  denominators, rates, and a composition rule; interventions are configuration.

## Data model

New app `connect_labs/labs/indicators/`, depending on `labs.admin_boundaries`
for geometry.

### One fact table

```python
class IndicatorValue(models.Model):
    indicator     = CharField(db_index=True)   # 'u5mr' | 'pop_u5' | 'births' | 'pop_f_15_49'
    boundary      = FK(AdminBoundary)
    iso_code      = CharField(db_index=True)   # denormalised: continent queries stay one hop
    admin_level   = PositiveSmallIntegerField(db_index=True)
    year          = IntegerField(db_index=True)
    value         = FloatField()
    ci_low        = FloatField(null=True)
    ci_high       = FloatField(null=True)
    source        = CharField(db_index=True)   # 'dhs' | 'igme' | 'worldpop' | 'hapi' | 'derived'
    source_ref    = CharField()                # 'NG2024DHS' | 'WorldPop wpgpas 2020'
    license_code  = CharField(db_index=True)   # 'cc-by-4.0' | 'cc-by-nc-sa-3.0-igo'
    method        = TextField(blank=True)      # how a derived value was computed
    retrieved_at  = DateTimeField()
    extra         = JSONField(default=dict)
```

Unique on `(indicator, boundary, year, source)` — deliberately **not** unique
without `source`. Competing estimates coexist as rows and are chosen at query
time, exactly as `AdminBoundary.extra.populations` already holds eight parallel
population figures per Nigerian ward.

### The measure registry carries aggregation semantics

`measures.py` declares each indicator once. This is the file that keeps
continental rollups correct.

| measure | kind | rolls up by | weighted by | inherits down |
|---|---|---|---|---|
| `pop_total`, `pop_u5`, `pop_u1`, `pop_f_15_49`, `births` | count | sum | — | no |
| `u5mr`, `imr` | rate per 1,000 live births | weighted mean | `births` | yes |
| `tfr` | rate | weighted mean | `pop_f_15_49` | yes |

Counts sum. Rates must never sum — they take a weighted mean, and the weight is
declared per measure because it differs: weighting U5MR by total population
instead of births biases a continental figure toward places with few children.

### Inheritance is resolved, never stored

A boundary with no row for an inheriting measure walks up `parent_boundary_id`.
`resolve()` returns the value plus `inherited_from`, so the provenance panel gets
"measured at national level, applied to Kano State" from the same object that
carried the number. Nothing is written downward, so re-ingesting cannot fan out
stale copies.

## Sources

All verified live on 2026-08-26. None require credentials.

| indicator | source | resolution | licence |
|---|---|---|---|
| `u5mr`, `imr`, `tfr` | DHS API (`api.dhsprogram.com`) | ADM1, survey years | open API |
| `u5mr` fallback | UN IGME via UNICEF SDMX | national, annual | open |
| `pop_*` | WorldPop `wpgpas` zonal stats API | any polygon, server-side | CC BY 4.0 |
| `pop_*` alternative | HDX HAPI baseline population | ADM1, age–sex banded | open |
| boundaries | geoBoundaries ADM0/ADM1 | — | CC BY 4.0 |

WorldPop's hosted zonal-stats service does the raster work remotely: submit a
polygon, poll a task, receive an age–sex pyramid. No rasters are downloaded.

### Births are derived, two independent ways

WorldPop publishes no births product, so `births` is computed and marked
`source='derived'` with the formula in `method`:

1. **Infant-cohort method (primary).** `births = pop_u1 / (1 - imr)`. The 0–1
   age band is one birth cohort minus infant deaths; dividing it back out
   recovers births. Uses the WorldPop call already required for `pop_u5`.
2. **Fertility method (cross-check).** `births = pop_f_15_49 × GFR`, with the
   general fertility rate derived from DHS subnational TFR.

Both are stored. Divergence between them is a data-quality signal worth
surfacing rather than hiding.

## Selection and rollup

The threshold query is the core primitive:

```python
select_above(indicator='u5mr', threshold=80, year=2024)
```

**Coarsest-sensible-unit rule.** For each country: if *every* ADM1 unit clears
the threshold, emit one country row. Otherwise emit the qualifying ADM1 units
individually. This is what stops a table of 700 regions when the honest answer
is "these 14 countries, entirely".

Counts are summed over the selection; rates are re-weighted, never summed.

## Surface

`/labs/targeting/`

- Choropleth over ADM0/ADM1, painted by U5MR.
- Threshold control, in both per-1,000 and percent (Jon's "8%" is 80 per 1,000).
- Headline: estimated annual births in the selection, with the count of areas
  and countries it spans.
- Table of the selection at coarsest sensible unit.
- CSV download: the table plus a methodology block naming every source, its
  vintage, its licence, and the formula for any derived column.
- Methodology panel answering "what is this based on" inline.

## Testing

- `measures` registry: every measure has a valid aggregation rule; rates declare
  a weight.
- `resolve()`: inheritance walks up correctly and reports `inherited_from`.
- Rollup: counts sum, rates weight, a rate is never summed.
- Coarsest-unit rule: all-above emits country; partial emits ADM1s.
- Source loaders: parse fixtures into expected rows; no network in tests.

## Out of scope for v1

Intervention/cost modelling (`eligible_cases × unit_cost`) and AI-assisted
querying. The schema supports both — interventions compose indicator codes —
but the first build stops at the primitives plus the threshold surface, per
"build all the primitives, then we'll come up with different ways to think about
the UI and AI-supported access."
