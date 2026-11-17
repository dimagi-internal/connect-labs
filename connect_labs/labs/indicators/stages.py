"""The loader stages, once: their order, what each needs, and why.

A rebuild from source is these stages run in this order, so the order is declared here and nowhere else.
``load_indicators`` takes its ``--stage`` choices from ``NAMES`` and ``indicators_bootstrap`` runs ``STAGES``; the
table in BOOTSTRAP.md is rendered from them (``markdown_table``) and a test fails when it drifts. Before this,
bootstrap kept its own list of seven stages while ``load_indicators`` ran seventeen, so a "rebuild everything"
quietly left out every stage added after the list was written.

Adding a source or a stage: add its ``Stage`` here (after what it ``needs``), write ``_stage_<name>`` on the
``load_indicators`` command, and the tests in ``tests/test_stages.py`` say what is still missing (the method, the
BOOTSTRAP table, a source no stage writes). Every stage is idempotent: values upsert on
``(indicator, boundary, year, source)``, so re-running one repairs rather than duplicates.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Stage:
    name: str
    #: What it loads, and why it sits where it does.
    why: str
    #: Stages whose output this one reads; each must come earlier in ``STAGES``.
    needs: tuple[str, ...] = ()
    #: Hours of fetching, or rasters. A complete rebuild runs it; a quick refresh can leave it out.
    heavy: bool = False


STAGES: tuple[Stage, ...] = (
    Stage("mortality", "DHS surveys, the IGME national series, and IGME's subnational model"),
    Stage("calibrate", "re-levels the surveys to the IGME series, so it needs both", ("mortality",)),
    Stage("fertility", "DHS TFR plus the World Bank national fallback"),
    Stage("population", "HAPI first (minutes), then WorldPop (hours, quota-limited)", heavy=True),
    Stage("conflict", "UCDP reported conflict deaths per territory, and the rate on population", ("population",)),
    Stage(
        "births",
        "the UN's national births, then ours derived from population and mortality, and the audit between them",
        ("population", "mortality", "fertility"),
    ),
    Stage(
        "lbw",
        "UNICEF-WHO low birthweight and DHS place of delivery; the low-birthweight count needs births",
        ("births",),
    ),
    Stage(
        "child_health",
        "diarrhoea, nutrition, immunisation, WASH and the gaps they imply (a gap needs a population)",
        ("population", "births"),
    ),
    Stage("national_survey", "the survey's own country figure for every survey indicator the policy admits it for"),
    Stage("poverty", "World Bank GSAP poverty by region, joined to boundaries by name"),
    Stage("malaria", "MAP's modelled surfaces aggregated onto our boundaries", heavy=True),
    Stage(
        "population_raster",
        "population summed from WorldPop's own grid, for the units its service refuses",
        heavy=True,
    ),
    Stage("population_agesex", "age bands summed from WorldPop's grids", ("population_raster",), heavy=True),
    Stage(
        "accessibility", "walking time to healthcare, weighted by where people are", ("population_raster",), heavy=True
    ),
    Stage("settlement", "rural and urban by DEGURBA, counted over people", ("population_raster",), heavy=True),
    Stage("unicef", "UNICEF's subnational SDMX warehouse: WASH and neonatal mortality where DHS never went"),
    Stage("rainfall", "CHIRPS rainfall seasonality (120 files to one climatology)", heavy=True),
)

NAMES: tuple[str, ...] = tuple(s.name for s in STAGES)
BY_NAME: dict[str, Stage] = {s.name: s for s in STAGES}

TABLE_START = "<!-- stages:start (rendered by stages.markdown_table; tests/test_stages.py checks it) -->"
TABLE_END = "<!-- stages:end -->"


def markdown_table() -> str:
    """The stages as the table BOOTSTRAP.md carries between its markers, in run order."""
    lines = ["| # | stage | needs | what and why |", "| - | ----- | ----- | ------------ |"]
    for i, s in enumerate(STAGES, 1):
        needs = ", ".join(s.needs) or "-"
        lines.append(f"| {i} | `{s.name}`{' (heavy)' if s.heavy else ''} | {needs} | {s.why} |")
    return "\n".join(lines)
