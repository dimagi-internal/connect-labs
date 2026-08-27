# Targeting indicators

Population and burden primitives for deciding **where to deploy Connect
interventions** — and for showing the working, so any number can be traced back
to a source, a year, and a licence.

Surface: `/labs/targeting/`

## The question it answers today

> Set a threshold for under-5 mortality. How many births a year happen in the
> places above it, and what is that based on?

Move the slider to 8% (80 per 1,000) and you get the qualifying areas across
Africa, the estimated annual births in them, and a download containing both the
table and the methodology that produced it.

## The one thing to understand before changing anything

**Counts and rates aggregate differently, and getting it wrong is silent.**

- **Counts** — population, births — _sum_ up the hierarchy.
- **Rates** — under-5 mortality, fertility — must _never_ be summed. They take a
  weighted mean, and the weight differs per measure. U5MR is weighted by
  **births**, not population: weighting by population would bias a continental
  figure toward places with few children.
- **Rates inherit downward**; counts never do. A country's U5MR is the best
  available estimate for a region with no survey of its own. A country's
  population is emphatically not its region's population.

All of this is declared once in [`measures.py`](measures.py) and applied in one
place ([`resolve.py`](resolve.py)). Adding an indicator is a registry entry plus
a loader — never new aggregation code.

## Layout

| file                  | role                                                       |
| --------------------- | ---------------------------------------------------------- |
| `measures.py`         | the registry: what each indicator is and how it aggregates |
| `models.py`           | `IndicatorValue` (one fact table) and `IngestRun`          |
| `resolve.py`          | resolution, inheritance, aggregation, threshold selection  |
| `export.py`           | CSV + METHODOLOGY.md, zipped together                      |
| `africa.py`           | the country set, stated explicitly rather than inferred    |
| `sources/`            | one loader per upstream; `base.py` is the only writer      |
| `views.py`, `urls.py` | the map page and its JSON APIs                             |

## Sources

None require credentials. All permit commercial use except WHO GHO, which is
not wired in — see "Licences" below.

| indicator                                      | source                                      | resolution                      |
| ---------------------------------------------- | ------------------------------------------- | ------------------------------- |
| `u5mr`, `imr`, `tfr`                           | DHS Program API                             | ADM1, latest survey per country |
| `u5mr`, `imr` (fallback)                       | UN IGME via UNICEF SDMX                     | national                        |
| `pop_total`, `pop_u1`, `pop_u5`, `pop_f_15_49` | WorldPop hosted zonal stats                 | any polygon                     |
| `pop_*` (alternative)                          | HDX HAPI                                    | ADM1, where covered             |
| boundaries                                     | geoBoundaries (via `labs.admin_boundaries`) | ADM0 + ADM1                     |

**No IHME.** Its non-commercial agreement excludes for-profit entities _and
their employees_, and forbids re-hosting. Nobody should register a
healthdata.org account on a dimagi.com address.

## Births are derived, not measured

WorldPop publishes no births product, so:

```
births = population aged 0-1 / (1 - infant mortality rate / 1000)
```

The under-1 band is one birth cohort less the infants who died; dividing
survivorship back out recovers births. An independent estimate from women of
childbearing age × TFR is stored alongside as `births_fertility_check` — kept
separate so nothing can mistake the cross-check for the headline, and so
disagreement between the two stays visible.

## Running an ingest

Order matters: births derive from mortality and population.

```bash
make manage CMD="load_africa_boundaries"                    # ADM0 + ADM1, all of Africa
make manage CMD="load_indicators --stage mortality"         # DHS + IGME, minutes
make manage CMD="load_indicators --stage fertility"
make manage CMD="load_indicators --stage population"        # WorldPop, ~1 hour for the continent
make manage CMD="load_indicators --stage births"            # derived, seconds
```

Useful flags: `--iso NGA,KEN` to scope, `--missing-only` to resume the
population stage, `--limit N` for a trial run.

The population stage is the slow one and the only one that touches a rate-limited
service. It writes incrementally and is safe to interrupt and resume.

### WorldPop's three sharp edges

Learned by running the whole continent; all are handled, none are obvious:

1. **The geometry goes in a POST body.** As a GET query string it returns 414.
2. **MultiPolygon is rejected** — "This operation supports only Polygons". Every
   `AdminBoundary` is a MultiPolygon, and `simplify()` only collapses
   single-part ones, so island-heavy coasts failed while their mainland
   neighbours succeeded.
3. **Area is capped at 100,000 km².** Most Sahelian regions and every ADM0 are
   over it.

The fix for all three is the same: decompose into disjoint pieces under the cap
and sum, which is exact because population is a count. Island chains that
shatter into dozens of specks keep the largest pieces covering 99.5% of the
area; the omitted share is written into the value's `method` rather than passed
off as complete.

More workers is not faster — it produces read timeouts. See `MAX_WORKERS`.

## Licences

`license_code` rides on every row rather than living in a README, so "may this
reach a commercially-framed deliverable?" is a query, not a memory:

```python
IndicatorValue.objects.filter(license_code__in=NON_COMMERCIAL)
```

The export names every licence behind a selection and says plainly when a
non-commercial source is present.

## Known gaps

- **Western Sahara (ESH)** has no geoBoundaries data, so it is absent entirely.
- **Mortality is ADM1 at best.** Regions without their own survey inherit the
  national figure; the UI and the export both say so per row. Nothing here
  implies sub-regional mortality.
- **Years are not aligned.** Mortality is the latest survey per country, which
  varies; population is WorldPop 2020. Both years are shown.
- **Some boundary sets are coarse.** geoBoundaries gives Niger 6 ADM1 units with
  merged names like `Zinder/Diffa`, against the 8 regions DHS reports.

## What this is not, yet

Intervention costing (`eligible_cases × unit_cost`) and AI-assisted querying are
deliberately out of scope for the first build. The schema supports both —
an intervention is a formula over indicator codes — but the primitives came
first.
