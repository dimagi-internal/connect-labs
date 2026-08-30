"""The research notes we already hold, as data.

Kept in the repository rather than only in the database for two reasons. It is
reviewable — a claim about what a licence permits or why a method was rejected
deserves the same scrutiny as code. And it is restorable: the database is
rebuilt from a snapshot, and knowledge that only ever lived in a row would be
lost every time somebody reset an environment.

``manage.py seed_targeting_research`` writes these in. Notes edited through the
MCP live only in the database; that is deliberate, because a session recording
what it just found should not need a deploy. The seeds are the floor, not the
ceiling.

Every note carries checks. See ``research.py`` for why: a stored conclusion that
cannot be re-tested is folklore, and folklore is worse than no note at all
because it reads with the same confidence as a fact.
"""

from __future__ import annotations

#: Written as of the MAP 202508 load. Where a check names a figure, that figure
#: was true when the note was written and the check exists to say when it stops
#: being true — not to assert it forever.
NOTES: tuple[dict, ...] = (
    {
        "indicator": "malaria_cases",
        "topic": "which-source",
        "summary": (
            "MAP's modelled surfaces are the only source giving malaria as a COUNT, "
            "subnationally, everywhere in Africa — and its national totals sit within "
            "2% of the WHO World Malaria Report."
        ),
        "body": """
Malaria was our worst-covered family until the Malaria Atlas Project surfaces
were loaded: DHS answered prevalence for 288 of 778 ADM1 units and nothing at
all below that. MAP answers every unit at every level, because it publishes a
continuous 5 km surface rather than survey points, and it publishes annually to
2024 against DHS fieldwork that is often a decade old.

**The thing that made this worth doing is the counts.** Almost every indicator
we hold is a rate, and a rate cannot answer "how many cases would we be
treating" — the per-case cost basis has to be refused. MAP's incidence and
mortality layers are counts per cell, so they sum exactly up the hierarchy and
carry a per-case cost. Malaria is currently the only family where that is true.

**How it is aggregated.** Counts are summed over the cells whose centre falls
inside the unit. Rates are a *population-weighted* mean, and the weight is not
borrowed from WorldPop — it is recovered from MAP's own layers. Their incidence
count and incidence rate are the same quantity per cell, once per year and once
per person per year, so `count / rate` is the population MAP modelled against,
on exactly the grid being aggregated. Area-weighting was rejected: it lets empty
desert vote as loudly as a city, and for a large ADM1 that is a real distortion.

**How well it holds up.** Nigeria reads 70.4M cases against the WHO World
Malaria Report's ~68M, and Africa reads 241.9M against WHO's ~246M — inside 2%.
Deaths run higher than WHO (Africa 646k against ~569k, Nigeria 221k against
~194k, so roughly 13%). That gap is a genuine disagreement between two modelling
groups, not a bug in our aggregation, and it should be stated rather than
smoothed. Independently, the ADM1 sum equals the ADM0 total exactly, which is
the check that the polygon handling is right.

**What would change the mind here.** A WHO subnational product with counts, or
a MAP release whose national totals stop tracking WHO. Neither exists today.
""".strip(),
        "checks": [
            {"kind": "source", "indicator": "malaria_cases", "source": "map", "expected": True},
            {"kind": "coverage", "indicator": "malaria_cases", "level": 1, "expected": 776},
            {"kind": "coverage", "indicator": "malaria_cases", "level": 2, "expected": 1518},
            {
                "kind": "value",
                "indicator": "malaria_cases",
                "iso": "NGA",
                "level": 0,
                "expected": 70415796,
                "tolerance": 0.05,
            },
            {"kind": "measure", "code": "malaria_cases", "expected": {"kind": "count", "family": "burden"}},
        ],
        "alternatives": [
            {
                "name": "Malaria Atlas Project (MAP) 202508",
                "url": "https://data.malariaatlas.org/maps",
                "licence": "CC BY 3.0 Unported — commercial use and redistribution permitted with attribution",
                "verdict": "adopted",
                "why": "Only source with subnational counts across all of Africa, annual to 2024, openly licensed.",
            },
            {
                "name": "WHO World Malaria Report / GHO",
                "url": "https://ghoapi.azureedge.net/api/",
                "licence": "CC BY-NC-SA 3.0 IGO — non-commercial",
                "verdict": "rejected",
                "why": (
                    "National only, so it cannot target anything, and its non-commercial licence would bar "
                    "it from a commercially-framed deliverable. Retained as the external cross-check MAP is "
                    "validated against, which is the right role for it."
                ),
            },
            {
                "name": "DHS malaria prevalence",
                "url": "https://api.dhsprogram.com/",
                "licence": "open with registration",
                "verdict": "candidate",
                "why": (
                    "A measurement rather than a model, so it stays the default for prevalence. But it is "
                    "prevalence only — no counts — and covers 288 of 778 ADM1 units."
                ),
            },
            {
                "name": "IHME Global Burden of Disease",
                "url": "https://vizhub.healthdata.org/gbd-results/",
                "licence": (
                    "non-commercial agreement; excludes for-profit entities AND their employees; forbids re-hosting"
                ),
                "verdict": "rejected",
                "why": (
                    "Licence, not quality — it may well be better. Nobody should register a healthdata.org "
                    "account on a dimagi.com address. See the cross-cutting 'licensing' note."
                ),
            },
        ],
        "scanned_now": True,
    },
    {
        "indicator": "malaria_prevalence",
        "topic": "age-band-mismatch",
        "summary": (
            "Two sources answer prevalence on different age bands — DHS 6-59 months by RDT, "
            "MAP 2-10 years modelled — so the unit is deliberately vague and source_ref decides."
        ),
        "body": """
`malaria_prevalence` is the one indicator where two sources answer the same
question with different denominators. DHS measures children 6-59 months by rapid
diagnostic test. MAP models PfPR2-10, the proportion of 2-10 year-olds carrying
detectable parasites. These are not the same quantity and merging them silently
under a unit reading "% of children 6-59 months" would be exactly the quiet
inconsistency a reviewer catches.

The resolution was to soften the measure's unit to "% of children" and let
`source_ref` on every row name which definition produced it. Fragmenting into
two indicators was rejected: it splits the UI and forces a user to know the
distinction before they can ask the question, when the system's whole design is
to let sources disagree in the open and be chosen at query time.

DHS stays the default because a measurement beats a model where both exist. MAP
covers roughly twice as many ADM1 units, so the methodology's "other methods
considered" section should be read for this indicator in particular.

Nigeria reads ~36% on DHS 2018 and 24.1% on MAP 2024. Most of that gap is a real
decline over six years; some is the age band. Do not present it as one number.
""".strip(),
        "checks": [
            {"kind": "source", "indicator": "malaria_prevalence", "source": "map", "expected": True},
            {"kind": "source", "indicator": "malaria_prevalence", "source": "dhs", "expected": True},
            {"kind": "coverage", "indicator": "malaria_prevalence", "level": 1, "expected": 563},
            {"kind": "measure", "code": "malaria_prevalence", "expected": {"kind": "rate", "family": "burden"}},
        ],
        "alternatives": [],
        "scanned_now": True,
    },
    {
        "indicator": "",
        "topic": "village-level-geography",
        "summary": (
            "Village counts are only available where a government has already drawn them — "
            "three African countries. Detecting them from building footprints was tested "
            "against Rwanda's register and failed decisively."
        ),
        "body": """
"How many villages?" is the most-asked question this system cannot answer, and
the reason is not a missing dataset — it is that most countries have never drawn
one. Of 55 African countries, three reach village level: Rwanda (14,815
*umudugudu*), Madagascar (17,465 *fokontany*) and Burundi (2,615 *collines*).
Twenty-one stop at ward or district; twenty-five stop at district. Check
`targeting_admin_levels` before promising a count.

**Detection from building footprints does not substitute.** This was tested
properly, not assumed. All 5,578,654 Overture buildings inside Rwanda were
clipped and clustered, and the result compared against the official register:

  * Median nearest-neighbour spacing is 10 m, so there is no natural gap between
    "inside a village" and "between villages" for a density threshold to find.
  * No DBSCAN radius reproduces the register. Nyaruguru has 332 villages;
    sweeping the radius produced anywhere from 68 to 2,403 clusters.
  * In Gasabo, a 120 m radius put 228,753 of 330,054 buildings — 69% of the
    district — into a single cluster. Rwanda's settlement is continuous enough
    that clustering finds the district, not the village.

The honest reframing, where no register exists, is **"how many delivery units of
~N households"**. That is well-posed, computable from population and mean
household size, and it is what a programme actually budgets against. Say that
instead of producing a village count nobody can check.

**What would change this.** A national village register published as boundaries
for another country, or an OSM/Overture *place* layer with settlement polygons
rather than points. Neither is available at continental scale today.
""".strip(),
        "checks": [],
        "alternatives": [
            {
                "name": "Official village registers (RWA, MDG, BDI)",
                "url": "https://www.geoboundaries.org/",
                "licence": "varies, generally open",
                "verdict": "adopted",
                "why": "Where they exist they are ground truth. They exist for three countries.",
            },
            {
                "name": "Overture Maps building footprints",
                "url": "https://docs.overturemaps.org/",
                "licence": "ODbL / CDLA-Permissive depending on theme",
                "verdict": "rejected",
                "why": (
                    "Tested against Rwanda's register and failed: cluster count varies 68-2,403 for one "
                    "district depending only on the radius. Building footprints locate buildings, not "
                    "settlements. Still valuable for microplanning within a known area."
                ),
            },
        ],
        "scanned_now": True,
    },
    {
        "indicator": "share_rural",
        "topic": "which-definition",
        "summary": (
            "DEGURBA is loaded and is the only rural definition comparable between "
            "countries -- but the definition dominates the answer, so it is never quoted "
            "without naming it."
        ),
        "body": """
There is no neutral answer to "how many rural people are there". DEGURBA
(GHS-SMOD R2023A, endorsed by the UN Statistical Commission in 2020) is loaded
here because it is the best available *comparable* definition, which is a
different property from being the right one for a given country.

**The definition dominates the answer.** Applied to Rwanda's villages it classes
17% of them as rural; by population it reads 23.6%, or about 3.0 million people.
Rwanda's own national figure is near 72%. Neither is wrong: Rwanda's density
clears DEGURBA's urban threshold across most of the country, so a definition
built for global comparability reads Rwanda as mostly urban while a definition
built for Rwanda does not. Nigeria comes out at 54.8% (112.8 million), against a
World Bank figure nearer 48% — the same effect, much smaller.

**Egypt is the extreme case and the one to quote when explaining this.** DEGURBA
reads it as 12.0% rural; Egypt's own statistics say roughly 57%. Both describe
the same country. Egypt's population is packed along the Nile at densities that
clear DEGURBA's urban threshold almost everywhere, so a definition built on
density calls a farming village urban. Nothing is broken; the definition is
answering a different question.

Africa as a whole comes out at 745.2 million rural, about 55% — close to the
World Bank's ~56% for sub-Saharan Africa, which is the reassuring part: the
continental figure is unremarkable and the divergence is concentrated in
countries whose settlement pattern is unusual. The most rural are South Sudan
(98.3%), Chad (91.0%) and Somalia (90.8%); the least are Egypt, Rwanda,
Mauritius and South Africa.

So: **always state which definition produced the number.** A rural figure quoted
without its definition is not a fact about the world. Every value this loader
writes says so in its method text.

**Counted over people, not over land.** The classification is a grid, so counting
rural *cells* would answer a question about area. Both DEGURBA and WorldPop are
read on the same 30 arc-second cells and the share is of population.

**Rural is classes 11, 12 and 13 only.** Water is class 10 and is deliberately
excluded — it is the class most likely to be swept in by a "not urban" test, and
including it would inflate every coastal and lakeside district.

**Sampled by coordinate.** Both grids are 30 arc-second but published on
different extents, so index alignment is a coincidence, not a fact.

For reference from the village work: Rwandan villages average 871 people, median
676. A tempting shortcut — rural population divided by an average village size —
tested 18% high against the register (3,004 predicted against 2,553 actual).
Usable as an order of magnitude if the error is stated; not usable as a count.
See [[village-level-geography]].
""".strip(),
        "checks": [
            {"kind": "source", "indicator": "share_rural", "source": "ghsl", "expected": True},
            {"kind": "measure", "code": "pop_rural", "expected": {"kind": "count"}},
            {
                "kind": "value",
                "indicator": "share_rural",
                "iso": "RWA",
                "level": 0,
                "expected": 23.6,
                "tolerance": 0.1,
            },
        ],
        "alternatives": [
            {
                "name": "DEGURBA / GHS-SMOD R2023A",
                "url": "https://human-settlement.emergency.copernicus.eu/degurba.php",
                "licence": "CC BY 4.0",
                "verdict": "adopted",
                "why": (
                    "The only definition comparable across countries, and UN-endorsed. Comparable is "
                    "not the same as correct for one country, which is why the definition is carried "
                    "on every value."
                ),
            },
            {
                "name": "National statistical office definitions",
                "url": "",
                "licence": "varies",
                "verdict": "rejected",
                "why": (
                    "Right for single-country work and useless for a continental comparison -- the "
                    "thresholds differ by country, sometimes sharply. Worth quoting alongside DEGURBA "
                    "when a proposal is about one country."
                ),
            },
        ],
        "scanned_now": True,
    },
    {
        "indicator": "",
        "topic": "licensing",
        "summary": (
            "IHME and WHO GHO are both excluded from commercially-framed deliverables on "
            "licence grounds — a constraint on us, not a judgement on the data."
        ),
        "body": """
Two well-known sources are excluded here for licence reasons, and the exclusion
should always be reported that way rather than implying they are worse.

**IHME (Global Burden of Disease).** Its non-commercial agreement excludes
for-profit entities *and their employees*, and forbids re-hosting. Practically:
nobody should register a healthdata.org account on a dimagi.com address. Its
estimates may well be better than what we use; we cannot use them.

**WHO GHO.** CC BY-NC-SA 3.0 IGO — non-commercial. It stays valuable as an
external cross-check (the figure we validate MAP's national totals against), but
a GHO number must not be carried into a commercially-framed deliverable.

The `License.NON_COMMERCIAL` set in `models.py` exists so that "may we put this
in a proposal?" is a query rather than somebody's memory. Anything landing in
that set must not reach a commercial surface.

Everything currently loaded is commercially usable: MAP is CC BY 3.0 Unported,
WorldPop and geoBoundaries are CC BY 4.0, DHS is open with registration, IGME is
published openly by UNICEF.
""".strip(),
        "checks": [],
        "alternatives": [],
        "scanned_now": True,
    },
    {
        "indicator": "pop_total",
        "topic": "worldpop-adm2-limit",
        "summary": (
            "WorldPop's stats API refuses some geometries outright, which stalled the ADM2 "
            "backfill at 1,005 of 1,518. Reading its raster directly finished it: 1,517."
        ),
        "body": """
WorldPop's hosted zonal-statistics service answers a polygon with an age-sex
pyramid, which is what gives us population without local raster work. It has an
undocumented daily quota, and for a long time that looked like the constraint on
the ADM2 backfill.

It was not. Re-running `--missing-only` against the outstanding units returns
`IndexError` and "No recorded population in this area" for essentially all of
them — the service declining those specific geometries, not throttling us. No
amount of waiting was ever going to finish it.

**The raster has those units regardless.** Downloading a country's 1 km grid and
summing it here sidesteps the service entirely: about 5 MB per country, computed
once and discarded. ADM1 went 754 -> 778 and ADM2 went 1,005 -> 1,517 of 1,518.

**The two WorldPop products are not the same number.** Across Nigeria's 37 states
the raster reads about 5% *below* the API, consistently, because the grid used
here is UN-adjusted (reconciled to UN World Population Prospects) and the API's
age-sex product is not. That is why both are loaded in full rather than the
raster filling only gaps: filling gaps alone would silently mix two calibrations
inside one continental total. The resolution order keeps the API first, so
nothing moved under a reader mid-argument, and the disagreement stays visible.

**Verification.** Nigeria's grid sums to 206,139,587 against the published
UN-adjusted figure of 206,139,589. Summing the same grid per ADM1 boundary and
adding those up loses 0.09% to cell-centre membership across 37 states — that
number is the check on the whole zonal pipeline, not just on this loader.

**The one unit still missing** is Cameroon's Sanaga-Maritime, and it is a
boundary problem rather than a data one: geoBoundaries gives it a 1.3 km sliver
of coast where a department of roughly 200,000 people should be. A `None` there
is the honest answer.
""".strip(),
        "checks": [
            {"kind": "source", "indicator": "pop_total", "source": "worldpop", "expected": True},
            {"kind": "source", "indicator": "pop_total", "source": "worldpop_raster", "expected": True},
            {"kind": "coverage", "indicator": "pop_total", "level": 1, "expected": 778},
            {"kind": "coverage", "indicator": "pop_total", "level": 2, "expected": 1517},
        ],
        "alternatives": [
            {
                "name": "WorldPop 1 km UN-adjusted raster",
                "url": "https://hub.worldpop.org/geodata/listing?id=75",
                "licence": "CC BY 4.0",
                "verdict": "adopted",
                "why": (
                    "Finishes what the API could not, is checkable against a published national "
                    "total, and is UN-adjusted so its country totals agree with the denominators "
                    "every other source here is calibrated against."
                ),
            },
            {
                "name": "WorldPop hosted stats API",
                "url": "https://api.worldpop.org/v1/services/stats",
                "licence": "CC BY 4.0",
                "verdict": "adopted",
                "why": (
                    "Still the source for the age bands (pop_u1, pop_u5, pop_f_15_49), which the "
                    "aggregate grid does not carry. Kept first in the resolution order."
                ),
            },
            {
                "name": "HDX HAPI",
                "url": "https://hapi.humdata.org/",
                "licence": "varies by dataset, generally open",
                "verdict": "candidate",
                "why": "No longer needed for the gap it was proposed for. Still wired as a source.",
            },
        ],
        "scanned_now": True,
    },
    {
        "indicator": "travel_time_healthcare",
        "topic": "which-source",
        "summary": (
            "Loaded, and it needed two rasters: MAP's travel-time surface answers "
            "nothing useful without a population grid to weight it by."
        ),
        "body": """
MAP hosts the Weiss et al. accessibility surfaces — minutes to the nearest
health facility, walking or motorised, globally at 1 km. "People more than two
hours' walk from care" is exactly the evidence a community-health deployment is
argued from, so this was the most attractive unloaded dataset we knew of.

It stayed unloaded for a while, and the reason was aggregation rather than
access. **A district's average travel time is close to meaningless.** An area
mean over a large unit is dominated by land nobody lives on: a district that is
nine-tenths desert and one-tenth town reads as remote when almost everyone in it
is a short walk from a clinic. What a programme argues from is the number of
people beyond a threshold, and that cannot be computed from the travel surface
alone.

Loading WorldPop's 1 km grid unblocked it. Three numbers now come out of the
pair, all weighted by people rather than by land:

    travel_time_healthcare   minutes, population-weighted
    share_beyond_2h          the proportion further than two hours on foot
    pop_beyond_2h            how many people that is -- a COUNT, so it sums
                             exactly and can carry a per-person cost

Two hours is the threshold the access literature settled on.

**The trap is co-registration.** Both grids are 30 arc-second, but they are
published on different extents, so cell (i, j) of one is not cell (i, j) of the
other. Every population cell centre is looked up in the travel surface *by
coordinate*. Assuming aligned indices would shift a whole country by some
fraction of a degree and produce numbers that are wrong everywhere and obviously
wrong nowhere. There is a test for exactly this.

**Does it look right?** Nigeria: 29.2 minutes on average, 2.9% of people
(6.0 million) beyond two hours. The worst states are Bayelsa at 18.6%, then
Borno, Rivers, Yobe and Taraba — the Niger Delta creeks, where travel is by
boat, and the sparse conflict-affected northeast. That is the correct geography,
which is the strongest evidence available that the surface is being read right.
Regional figures sum exactly to the national one.

**Still not loaded:** the motorised surface. It answers a different question
(referral and facility access rather than community reach) and would double the
indicator count for a question nobody has asked yet.

**Vintage.** One epoch, 2020. There is no time axis to subset and no newer
release to bump to, so unlike MAP's malaria layers this one does not refresh.
""".strip(),
        "checks": [
            {"kind": "source", "indicator": "travel_time_healthcare", "source": "map_worldpop", "expected": True},
            {"kind": "measure", "code": "pop_beyond_2h", "expected": {"kind": "count", "family": "burden"}},
            {"kind": "measure", "code": "share_beyond_2h", "expected": {"kind": "rate"}},
            {
                "kind": "value",
                "indicator": "share_beyond_2h",
                "iso": "NGA",
                "level": 0,
                "expected": 2.9,
                "tolerance": 0.1,
            },
        ],
        "alternatives": [
            {
                "name": "MAP / Weiss et al. walking-only travel time to healthcare",
                "url": "https://data.malariaatlas.org/maps",
                "licence": "CC BY 3.0 Unported",
                "verdict": "adopted",
                "why": "The only global facility-access surface. Useless without a population grid; fine with one.",
            },
            {
                "name": "MAP / Weiss et al. motorized travel time",
                "url": "https://data.malariaatlas.org/maps",
                "licence": "CC BY 3.0 Unported",
                "verdict": "candidate",
                "why": "Answers referral access rather than community reach. Load it when asked that question.",
            },
            {
                "name": "WorldPop 1 km UN-adjusted grid",
                "url": "https://hub.worldpop.org/geodata/listing?id=75",
                "licence": "CC BY 4.0",
                "verdict": "adopted",
                "why": "The weighting layer. Without it the travel surface can only be averaged over land.",
            },
        ],
        "scanned_now": True,
    },
)
