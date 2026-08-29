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
        "indicator": "",
        "topic": "rural-urban-definition",
        "summary": (
            "Rural/urban is definition-dependent and the definition dominates the answer: "
            "DEGURBA calls 17% of Rwanda's villages rural against a national figure near 72%."
        ),
        "body": """
There is no neutral answer to "how many rural people are there". DEGURBA
(GHS-SMOD, endorsed by the UN Statistical Commission in 2020) is the best
available *comparable* definition, which is a different property from being the
right one for a given country.

Applied to Rwanda's 14,815 villages it classes 2,553 of them (17%) as rural,
holding 2,616,526 people (20% of the population). Rwanda's own national figure
is near 72%. Neither is wrong: Rwanda's population density clears DEGURBA's
urban threshold across most of the country, so a definition built for global
comparability reads Rwanda as mostly urban while a definition built for Rwanda
does not.

**Always state which definition produced the number.** A rural figure quoted
without its definition is not a fact about the world.

For reference, the village-size statistics that came out of the same work:
Rwandan villages average 871 people, median 676. A tempting shortcut — take the
rural population and divide by an average village size — was tested against the
register and came out 18% high (3,004 predicted against 2,553 actual). Usable
as an order-of-magnitude estimate if the error is stated; not usable as a count.
""".strip(),
        "checks": [],
        "alternatives": [
            {
                "name": "DEGURBA / GHS-SMOD",
                "url": "https://human-settlement.emergency.copernicus.eu/degurba.php",
                "licence": "CC BY 4.0",
                "verdict": "adopted",
                "why": (
                    "The only definition comparable across countries, and UN-endorsed. Comparable is not the same as "
                    "correct for one country."
                ),
            },
            {
                "name": "National statistical office definitions",
                "url": "",
                "licence": "varies",
                "verdict": "candidate",
                "why": (
                    "Right for single-country work, useless for a continental comparison — the thresholds differ by "
                    "country."
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
            "The ADM2 population backfill has hit WorldPop's own limit, not a quota: the "
            "remaining units are ones its stats service refuses outright."
        ),
        "body": """
WorldPop's hosted zonal-statistics service answers a polygon with an age-sex
pyramid, which is what gives us population without local raster work. It has an
undocumented daily quota, and for a long time that was the constraint on the
ADM2 backfill.

It is no longer the constraint. Re-running `--missing-only` against the
outstanding ADM2 units returns `IndexError` and "No recorded population in this
area" for essentially all of them — the service refusing those specific
geometries, not throttling us. Waiting for a quota reset will not finish this.

Reaching the last few hundred units needs a different mechanism: HDX HAPI
(already a registered source, `--source hapi`), or deriving the unit's share
from its parent, which is honest only if it is labelled as derived.

Do not read a *shortfall* in `coverage` on those units as a transient state that
another run will fix.
""".strip(),
        "checks": [
            {"kind": "source", "indicator": "pop_total", "source": "worldpop", "expected": True},
            {"kind": "coverage", "indicator": "pop_total", "level": 2, "expected": 1200},
        ],
        "alternatives": [
            {
                "name": "WorldPop hosted stats API",
                "url": "https://api.worldpop.org/v1/services/stats",
                "licence": "CC BY 4.0",
                "verdict": "adopted",
                "why": (
                    "Age-sex structure for a polygon with no local raster work. Has a daily quota and refuses some "
                    "geometries."
                ),
            },
            {
                "name": "HDX HAPI",
                "url": "https://hapi.humdata.org/",
                "licence": "varies by dataset, generally open",
                "verdict": "candidate",
                "why": "Already wired as a source. The obvious next move for units WorldPop refuses.",
            },
        ],
        "scanned_now": True,
    },
    {
        "indicator": "",
        "topic": "travel-time-to-healthcare",
        "summary": (
            "MAP hosts the Weiss et al. accessibility surfaces and they are a strong fit for "
            "CHW targeting, but they are NOT loaded — an area-mean travel time is close to "
            "meaningless and we have no population grid outside malaria-endemic cells."
        ),
        "body": """
MAP's GeoServer also hosts the Weiss et al. accessibility surfaces:
motorized and walking-only travel time to healthcare, global, 2020. "People more
than two hours' walk from a health facility" is exactly the evidence a CHW
deployment proposal argues from, so this is the most attractive unloaded dataset
we know of.

It has deliberately **not** been loaded, and the reason is aggregation, not
access. The meaningful statistic is not a district's average travel time — an
area-weighted mean over a large district is dominated by uninhabited land and
can be badly wrong in both directions. What a proposal needs is *the population
living beyond a threshold*, which requires a population raster on the same grid.

The trick used for malaria rates does not extend here: the population grid is
recovered from MAP's incidence count and rate, so it only exists where there is
malaria. An accessibility surface needs population everywhere.

**To unblock this**, we need a real population raster — WorldPop's 1 km global
mosaic or GHS-POP — clipped to Africa and read on the same cells. That is a
bounded piece of work and it would also let every other MAP rate be weighted
without the count/rate trick. Worth doing; not done.
""".strip(),
        "checks": [],
        "alternatives": [
            {
                "name": "MAP / Weiss et al. travel-time-to-healthcare",
                "url": "https://data.malariaatlas.org/maps",
                "licence": "CC BY 3.0 Unported",
                "verdict": "candidate",
                "why": (
                    "Blocked on a population raster, not on access. Loading it without one would produce a "
                    "defensible-looking but weak number."
                ),
            },
            {
                "name": "WorldPop 1 km global mosaic (raster download)",
                "url": "https://hub.worldpop.org/geodata/listing?id=64",
                "licence": "CC BY 4.0",
                "verdict": "candidate",
                "why": (
                    "Would unblock travel time and let every MAP rate be population-weighted directly. ~260 MB for "
                    "Africa at 1 km."
                ),
            },
        ],
        "scanned_now": True,
    },
)
