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
| `u5mr`, `imr` (fallback)                       | UN IGME via UNICEF SDMX                     | national, inherited             |
| `tfr` (fallback)                               | World Bank WDI                              | national, inherited             |
| `pop_total`, `pop_u5`, `pop_f_15_49`           | HDX HAPI — the fast path                    | ADM1, ~46 African countries     |
| `pop_total`, `pop_u1`, `pop_u5`, `pop_f_15_49` | WorldPop hosted zonal stats                 | any polygon, universal          |
| boundaries                                     | geoBoundaries (via `labs.admin_boundaries`) | ADM0 + ADM1                     |

Only WorldPop supplies `pop_u1`, so only WorldPop can feed the cohort births
method. HAPI's finest infant band is 0–4.

UN WPP would be the natural fertility fallback but now returns **401** on its
data endpoints; its indicator and location metadata are still open, the values
are not.

**No IHME.** Its non-commercial agreement excludes for-profit entities _and
their employees_, and forbids re-hosting. Nobody should register a
healthdata.org account on a dimagi.com address.

## Births are derived, not measured

WorldPop publishes no births product, so:

```
births = population aged 0-1 / (1 - infant mortality rate / 1000)
```

The under-1 band is one birth cohort less the infants who died; dividing
survivorship back out recovers births.

Where `pop_u1` is unavailable — any boundary covered only by HAPI — the
derivation falls back to the **fertility method**, `women 15–49 × TFR / 35`,
rather than leaving the place with no births at all. Which method produced a
value is recorded in `method` and `extra.method_key`, because the two are not
equally good and a reader should be able to tell them apart.

The fertility estimate is _also_ stored independently as
`births_fertility_check` for every boundary that can support it — kept as its
own measure so nothing can mistake the cross-check for the headline, and so
disagreement between the two stays visible.

## Running an ingest

Order matters: births derive from mortality and population.

```bash
make manage CMD="load_africa_boundaries"                    # ADM0 + ADM1, all of Africa
make manage CMD="load_indicators --stage mortality"         # DHS + IGME, minutes
make manage CMD="load_indicators --stage fertility"          # DHS + World Bank
make manage CMD="load_indicators --stage population"        # HAPI (fast) then WorldPop (slow)
make manage CMD="load_indicators --stage births"            # derived, seconds
```

Useful flags: `--iso NGA,KEN` to scope, `--missing-only` to resume the
population stage, `--source hapi|worldpop` to run one of them, `--limit N` for a
trial run.

The population stage runs HAPI first, then WorldPop. **HAPI covers most of the
continent in under a minute; WorldPop takes hours** — it queues tasks
server-side, so raising `--workers` buys nothing. Both write incrementally and
are safe to interrupt and resume, so the useful pattern is to take HAPI now and
let WorldPop backfill `pop_u1` in the background.

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

**There is a daily quota, and it is not documented.** Exceeding it returns
`429 "Your application is sending too many requests per day"` and everything
fails until it resets. Repeated restarts of a backfill are what exhaust it —
each restart re-submits work, and a three-retry loop across hundreds of pieces
turns one refusal into a thousand. The loader now treats a 429 as terminal:
it stops the run, keeps what was written, and says to resume with
`--missing-only` later. Restart a WorldPop ingest sparingly.

**More workers is not faster.** WorldPop queues our tasks server-side: a single
task takes ~15s whether one or eight are in flight, and pushing harder produced
read timeouts rather than throughput. This is why HAPI exists in the pipeline.

## Resolution and method

| method                   | resolution  | countries | depth                                  |
| ------------------------ | ----------- | --------- | -------------------------------------- |
| `subnational_igme`       | subnational | 25 / 55   | **ADM2 in 11 countries** — the default |
| `subnational_relevelled` | subnational | 41 / 55   | ADM1, survey pattern scaled to today   |
| `subnational_survey`     | subnational | 41 / 55   | ADM1, the survey as measured           |
| `national_igme`          | national    | 54 / 55   | one number per country                 |

IGME's own small-area model is preferred wherever it reaches; the re-levelled
survey is the fallback for the countries it does not cover. That inverted an
earlier decision — see `sources/igme_subnational.py` for why our own arithmetic
should not have been the primary method.

### IGME's admin levels are not ours

`ADMIN_LEVEL` in the IGME cube is its own tier numbering and the mapping to
geoBoundaries differs per country. Madagascar's IGME "level 2" is its 22
regions, which geoBoundaries calls ADM1 — matched against ADM2 it scored 1 of 22. So the loader tries each combination and keeps whichever actually matches,
and **refuses any country under a 75% match rate** rather than shipping a
half-matched map. Uganda is currently refused at 41%: its IGME districts have no
counterpart in geoBoundaries' county-level ADM2, so it falls back to the survey
path until a district boundary set is loaded.

`measures.py` says what an indicator _is_; `methods.py` says how a value for it
was _produced_, which is the thing that varies by country. A method declares its
**resolution** (national or subnational), which stored **sources** can satisfy
it, and the **caveat** a reader should hold in mind.

| method                   | resolution  | countries | notes                                        |
| ------------------------ | ----------- | --------- | -------------------------------------------- |
| `national_igme`          | national    | 54 / 55   | one number per country, current, everywhere  |
| `subnational_relevelled` | subnational | 41 / 55   | survey pattern scaled to today — the default |
| `subnational_survey`     | subnational | 41 / 55   | the survey as measured, carrying its date    |

**Methods never fall back to each other.** If you ask for subnational and a
country cannot answer, it is reported as unsupported, not quietly answered at
national level — comparing a Nigerian state against the whole of Chad is not a
comparison. `availability.py` computes who can answer what from the stored data
rather than a hand-kept list, so it cannot drift.

The three disagree, and that is the point. At an 80-per-1,000 threshold:
national selects 10 countries; the re-levelled subnational view finds qualifying
_regions_ in 19; the raw survey view finds them in 27, because it is reading
older and higher numbers.

Adding a method is an entry in the registry plus, if it needs one, a loader.
Selection and rollup code does not change — it asks the registry which sources
to prefer and at what level to work.

## Targeting on something other than mortality

The indicator is a choice, not a constant. `measures.TARGETABLE` lists the rates
you can threshold on; availability is computed per _(method, indicator)_ pair,
so IGME's model answers for mortality and DHS answers for child health without
either pretending to cover the other.

**21 targetable indicators**, 44 measures in all, ~35,000 values:

| group        | indicators                                                                     | countries |
| ------------ | ------------------------------------------------------------------------------ | --------- |
| mortality    | `u5mr`, `nmr`                                                                  | 54 / 23   |
| diarrhoea    | `diarrhoea_prevalence`, `ors_coverage`, `diarrhoea_untreated`, `zinc_coverage` | 33–41     |
| malaria      | `malaria_prevalence`, `malaria_treatment`, `itn_use_children`                  | 24–33     |
| nutrition    | `stunting`, `wasting`, `vitamin_a_coverage`, `exclusive_breastfeeding`         | 16–39     |
| immunisation | `measles_vaccination`, `dpt3_vaccination`, `full_immunisation`                 | 39        |
| respiratory  | `ari_prevalence`, `ari_antibiotics`                                            | 30–39     |
| maternal     | `skilled_birth_attendance`, `anc4`                                             | 38–41     |
| household    | `improved_water`, `improved_sanitation`                                        | 38        |

Adding another is an entry in `dhs.INDICATORS` plus a measure — the loader,
availability, rollup and UI are all indicator-agnostic.

**Some measures are worse when low.** ORS coverage and exclusive breastfeeding
are in `LOWER_IS_WORSE`, and selection inverts for them — thresholding _above_ a
coverage figure would pick the places already doing well. Each targetable
measure also carries its own slider range, because leaving an 80-per-1,000
mortality threshold in place when switching to a percentage selects almost
nothing and looks like missing data.

### Unreached populations, derived generically

A measure with `coverage_of` set declares the count it applies to, and
`derive.load_coverage_gaps` turns every one of them into an unreached
population:

```
unreached = denominator x (1 - coverage)
```

So `measles_vaccination_gap`, `improved_water_gap` and ten others exist without
a line of bespoke code each. `LOWER_IS_WORSE` is computed from the same field,
so a coverage measure cannot be added and then forget to invert its selection.

A selection carries only the gap belonging to the chosen indicator — resolving
all of them on every query to display one would be a dozen lookups per boundary.
A missing coverage reading yields no row at all: assuming zero coverage would
invent an unreached population the size of the whole denominator.

### Uncertainty is shown, not discarded

DHS publishes confidence bounds for some indicators — malaria prevalence and the
mortality rates among them. We were storing them in `ci_low`/`ci_high` and
displaying nothing. The table now prints the interval and marks a row amber when
it **spans the threshold**: at a 20% malaria cut, Haut-Katanga reads 20.5%
against an interval of 8.0–33.0, so its inclusion is not distinguishable from
chance. 59 of 144 rows in that selection are in that position. The CSV carries
`Confidence interval` and `Within uncertainty of threshold`.

### The Malaria Atlas Project was rejected, deliberately

MAP was on the acquisition list and is **not** ingested. Its ready-to-use
admin-level tables (annual parasite incidence, confirmed cases) effectively stop
around 2015 — 768 admin-1 rows for 2014, zero by 2020. Its current products
(August 2025) are rasters and survey points, which would need zonal statistics
to reach admin level.

Taking a 2015 model over DHS's measured malaria prevalence — 24 countries, with
published confidence bounds — would repeat exactly the staleness criticism made
of IHME's frozen 5 km layers. The MAP raster path is worth doing later; the
stale admin tables are not.

### The ORS gap

`ors_gap_children` = `pop_u5 × diarrhoea_prevalence × (1 − ors_coverage)` — the
under-5s with diarrhoea who are not getting ORS. Deliberately a **point
prevalence** on DHS's two-week recall, not an annual figure: annualising needs
an episode-frequency assumption (commonly ~3 per child-year) the survey does not
supply, and burying that in a headline would make a modelled number look
measured. Where a region has prevalence but no ORS reading, no gap is produced
at all rather than assuming zero coverage.

At 15% prevalence this finds **6.35M children** across 183 regions in 36
countries.

## Burden, not just rate

`expected_deaths` = `u5mr × births ÷ 1000`. Targeting on rate alone excluded
Oromia — the third-largest concentration of under-5 deaths in Africa — because
its rate is 60 while its birth cohort is enormous. Rate answers "where is it
worst per child"; burden answers "where are the children we could reach".

## Old surveys are re-levelled to the present

A third of Africa's subnational mortality comes from surveys eight or more years
old, and mortality has fallen a long way since. Taken raw, those surveys put
countries in the high-mortality bracket on numbers that stopped being true a
decade ago — Eritrea's 2002 regions read 111–154 against a national rate of 34
today; Eswatini 106 from 2006 against 45 today. Nine countries were being
selected at an 80-per-1,000 threshold whose _current_ national rate is already
below it.

`sources/calibrate.py` keeps the survey's subnational pattern and re-levels it:

```
factor   = IGME national (latest) / IGME national (survey year)
adjusted = survey region value x factor
```

Both ends of the ratio are IGME's own annual series, so the factor is a pure
trend carrying no difference of method between IGME and DHS. It runs uniformly —
a 2024 survey gets a factor of ~1.00 — and works both ways: Zimbabwe scales
_up_, because its rate rose.

**The assumption:** that relative differences between regions persisted while
the level moved. It weakens the older the survey. So the raw survey row is kept
beside the adjusted one, every adjusted value records its factor and endpoints,
and the UI shows the _survey_ year (amber past 8 years) rather than the year the
arithmetic targeted. Surveys predating the IGME series (Tunisia 1988) get no
factor and keep their raw value.

At an 80 threshold this moves the selection from 202 regions in 29 countries to
156 in 21.

**Then raked to the control total.** Re-levelling scales every region by one
factor, so it preserves whatever bias the survey's regional pattern carries: the
births-weighted regional mean did not reproduce the national figure it had been
levelled to, and eight countries were out by more than 20% (Uganda +37%). A
second per-country scaling closes that by construction — the pattern comes from
the survey, the level from the control total. Divergence is now 2 of 24
countries above 20%.

A consequence worth knowing: for a country with a single region, raking pins
that region to the national figure exactly. Correct, but it means the "regional"
estimate carries no regional information there.

**Validated by hold-out.** For every region with two surveys, the earlier one
was re-levelled forward and scored against what the later survey found — 590
pairs across 35 countries. Median absolute error **21.7%**, against **34.5%** for
assuming nothing changed; better in 28 of 35 countries, worse in 7. Good enough
to rank places, not to cost a programme.

## Every value links back to its source

`source_url` rides on each row beside `license_code`, so the table, the CSV and
METHODOLOGY.md can all point a reader at the thing the number came from:

| source              | link target                                                               |
| ------------------- | ------------------------------------------------------------------------- |
| DHS                 | the survey's own page, e.g. `survey-display-609.cfm` for Nigeria DHS 2024 |
| UN IGME             | `childmortality.org/data?refArea=<ISO3>`                                  |
| World Bank          | the WDI indicator page, deep-linked to the country                        |
| WorldPop / HDX HAPI | the product / dataset page                                                |
| derived             | none — the formula is in `method`, since we computed it                   |

The DHS link needs the survey's internal `SurveyNum`, which the value API does
not return, so the loader fetches `/surveys` once and indexes it. That index is
also what turns `NG2024DHS` into "Nigeria DHS 2024" — the raw id told a reader
nothing and led nowhere.

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
  merged names like `Zinder/Diffa`, against the 8 regions DHS reports. It also
  misspells Niger's Dosso region as "Dossa" — aliased in `sources/base.py`.
- **Births coverage trails population coverage.** A boundary needs either
  `pop_u1` (WorldPop) or `pop_f_15_49` + `tfr` to get a births estimate. Until
  the WorldPop backfill finishes, continental birth totals are an undercount,
  and the map shows how many features actually carry a figure.

## Costing: a unit price and a unit of measure

The arithmetic is trivial once two things are fixed, and neither can be inferred
from the data: **what one unit costs**, and **what a unit is**. Which unit
applies is a property of the programme — KMC is priced per newborn, a bednet per
child, a water connection per household, a treatment per case of disease — so
the basis is chosen, not guessed.

| basis             | resolves to              |
| ----------------- | ------------------------ |
| per birth         | `births`                 |
| per child under 5 | `pop_u5`                 |
| per person        | `pop_total`              |
| per household     | `households`             |
| per case          | depends on the indicator |

**"Per case" is contextual and that is the point.** Targeting diarrhoea, a case
is a child with untreated diarrhoea; targeting measles, an unvaccinated child;
targeting mortality, an expected death. Where an indicator has no coverage
figure to imply untreated cases — stunting, for instance — the basis is
**declined** rather than approximated.

Named interventions are presets for a basis, a starting price and the indicator
they belong to; selecting one moves the indicator too, since costing ORS while
targeting mortality would be the right arithmetic on the wrong quantity.

The same threshold under five bases, at $10 a unit:

```
per person       294,869,836   $2.9B
per household     36,971,372   $370M
per child u5      49,370,529   $494M
per birth         11,071,356   $111M
per case           1,377,332   $13.8M
```

Every scenario reports how much of the selection actually had a count, so an
incomplete one gives a floor and says so.

### Households are derived, and the division of labour matters

```
households = total population / mean household size
```

The **population** comes from WorldPop or a national statistics office via HDX
HAPI. Only the **ratio** comes from DHS. That distinction is worth stating
because it is the thing most likely to be misread: a household survey samples
tens of thousands of households and cannot count a population, but estimating an
average is exactly what it is built for. No source counts households
subnationally across Africa, so this is derived rather than measured.

## What this is not, yet## What this is not, yet

Intervention costing (`eligible_cases × unit_cost`) and AI-assisted querying are
deliberately out of scope for the first build. The schema supports both —
an intervention is a formula over indicator codes — but the primitives came
first.
