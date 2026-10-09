"""Connect PMC schedule sweep on EMOD — prototype for the IDM talk demo.

Compares PMC delivery configurations in one perennial, seasonally-peaked
setting (southern-Nigeria-like). Outcome: clinical malaria cases in children
3-24 months and under 5, and SP doses delivered, over 2 intervention years after burn-in.
"""

import json
import os
import pathlib
import sys
from functools import partial

# EMOD, emodpy and the tutorials' manifest are imported where they are used, so the pure helpers (outcomes, the
# drug and age-bin constants) import without them.

BURN_IN_DAYS = 2 * 365
INTERVENTION_DAYS = 2 * 365
SIM_DAYS = BURN_IN_DAYS + INTERVENTION_DAYS
POP = int(os.environ.get("PMC_POP", 10000))
# 6e7 (PfPR 2-5y about 44%) is what the committed grid was run with; the live model uses the same.
LARVAL_CAPACITY = float(os.environ.get("PMC_LARVAL", 6e7))
SEEDS = [int(s) for s in os.environ.get("PMC_SEEDS", "0,1").split(",")]

# Southern-Nigeria-like: year-round transmission, peak in the long rains
# (roughly Apr-Oct). Values are relative habitat by day of year.
HABITAT = {
    "Times": [0, 30, 60, 91, 122, 152, 182, 213, 243, 274, 304, 334, 365],
    "Values": [1.0, 0.8, 1.0, 2.0, 4.0, 6.0, 6.0, 5.0, 6.0, 5.0, 3.0, 1.5, 1.0],
}
SEASON_START = 91  # high season begins ~1 April
AGE_BINS = [0.25, 2, 5, 115]  # summary-report age bins, years (see BIN_* below)
SEASON_MONTHS = 6

# Each scenario: list of (start_offset_days, interval_days, repetitions, age_min, age_max, coverage)
EPI_AGES_Y = [10 / 52, 14 / 52, 0.75, 1.25]  # 10wk, 14wk, 9mo, 15mo contacts
SCENARIOS = {
    "none": [],
    "epi_linked": [(0, 30, INTERVENTION_DAYS // 30, a, a + 30 / 365, 0.25) for a in EPI_AGES_Y],
    "connect_quarterly_3_24": [(0, 91, INTERVENTION_DAYS // 91, 0.25, 2.0, 0.85)],
    "connect_bimonthly_3_24": [(0, 61, INTERVENTION_DAYS // 61, 0.25, 2.0, 0.85)],
    "connect_monthly_in_season_3_24": [(y * 365 + SEASON_START, 30, SEASON_MONTHS, 0.25, 2.0, 0.85) for y in range(2)],
    "connect_quarterly_12_24": [(0, 91, INTERVENTION_DAYS // 91, 1.0, 2.0, 0.85)],
}


def default_setting():
    """The CLI's setting, read from the PMC_* environment (see the module constants)."""
    return {
        "name": "SW_Nigeria_like",
        "larval_capacity": LARVAL_CAPACITY,
        "habitat_times": list(HABITAT["Times"]),
        "habitat_values": list(HABITAT["Values"]),
        "pop": POP,
        "case_mgmt": 0.5,  # clinical-case treatment coverage; severe cases get case_mgmt + 0.2
        "net_coverage": 0.5,
    }


def build_config(config, setting=None, duration_days=SIM_DAYS, serialization=None):
    """`serialization` is None, ("write", [timestep, ...]) or ("read", path, filename)."""
    import emodpy_malaria.malaria_config as malaria_config
    import manifest
    from emodpy_malaria.utils.serialization import configure_serialization_read, configure_serialization_write

    setting = setting or default_setting()
    config = malaria_config.set_team_defaults(config, manifest)
    malaria_config.add_species(config, manifest, ["gambiae", "funestus"])
    config.parameters.Run_Number = 0
    config.parameters.Simulation_Duration = duration_days
    set_habitats(config, setting)
    config.parameters.x_Base_Population = manifest.x_Base_Population_scale
    if serialization and serialization[0] == "write":
        configure_serialization_write(config, time_steps=list(serialization[1]), mask_node_write=0)
    elif serialization and serialization[0] == "read":
        configure_serialization_read(config, path=serialization[1], filenames=[serialization[2]])
    return config


def set_habitats(config, setting):
    """Give both species the setting's seasonal habitat, scaled to its larval capacity (replacing any habitat).

    The only larval-capacity-dependent part of the config, so a calibration sweep can re-point a built config
    at another capacity and get exactly what build_config would have built for it.
    """
    import emodpy_malaria.malaria_config as malaria_config
    from emodpy_malaria.utils.emod_enum import HabitatType

    habitat = malaria_config.VectorHabitat(
        habitat_type=HabitatType.LINEAR_SPLINE,
        max_larval_capacity=setting["larval_capacity"],
        capacity_distribution_number_of_years=1,
        capacity_distribution_over_time={"Times": setting["habitat_times"], "Values": setting["habitat_values"]},
    )
    for species in ["gambiae", "funestus"]:
        malaria_config.set_species_param(config, species, "Habitats", habitat, overwrite=True)
    return config


def build_demographics(setting=None):
    from emodpy_malaria.demographics import MalariaDemographics as Demographics
    from emodpy_malaria.utils.distributions import UniformDistribution
    from emodpy_malaria.utils.emod_enum import BirthRateDependence

    setting = setting or default_setting()
    demog = Demographics.from_template_node(lat=7.25, lon=5.2, pop=setting["pop"], name=setting["name"])
    demog.set_birth_rate(37, birth_rate_dependence=BirthRateDependence.POPULATION_DEP_RATE)
    demog.set_age_distribution(UniformDistribution(0, 60))
    demog.set_initial_prevalence_distribution(UniformDistribution(0.1, 0.3))
    return demog


# Chemoprevention drugs a schedule can give, as EMOD drug entries from emodpy-malaria's malaria_drug_params.csv
# (loaded by set_team_defaults). "SP" is PMC's sulfadoxine-pyrimethamine: the combined SulfadoxinePyrimethamine
# entry the committed grid ran. "SPAQ" is seasonal malaria chemoprevention: that same SP entry plus Amodiaquine,
# whose table entry is a 3-dose course a day apart (Drug_Fulltreatment_Doses 3, Drug_Dose_Interval 1). So PMC and
# SMC differ only by the amodiaquine. (emodpy's own SMC code "SPA" uses separate Sulfadoxine and Pyrimethamine
# entries, which would also change the SP part.)
DRUG_ENTRIES = {
    "SP": ["SulfadoxinePyrimethamine"],
    "SPAQ": ["SulfadoxinePyrimethamine", "Amodiaquine"],
}
DRUGS = tuple(DRUG_ENTRIES)


def chemoprevention_drugs(campaign, drug="SP"):
    from emodpy_malaria.campaign.individual_intervention import AntimalarialDrug

    if drug not in DRUG_ENTRIES:
        raise ValueError(f"unknown drug {drug!r}; expected one of {DRUGS}")
    return [AntimalarialDrug(campaign, drug_type=entry) for entry in DRUG_ENTRIES[drug]]


def build_campaign(campaign, setting=None, rounds=(), burn_in_days=BURN_IN_DAYS, start_shift=0, drug="SP"):
    """Background care + nets, then the PMC `rounds` as (offset, interval, reps, age_min_y, age_max_y, coverage),
    each round giving `drug` (see DRUGS).

    A round starts at burn_in_days + offset - start_shift. A run picking up a serialized population passes
    start_shift=burn_in_days so its clock starts at the end of the burn-in.
    """
    setting = setting or default_setting()
    case_mgmt = setting["case_mgmt"]
    severe_mgmt = min(1.0, case_mgmt + 0.2)
    import emodpy_malaria.campaign.waning_config as waning
    import manifest
    from emodpy.campaign.common import RepetitionConfig
    from emodpy.campaign.individual_intervention import BroadcastEvent
    from emodpy_malaria.campaign.common import TargetDemographicsConfig as TDC
    from emodpy_malaria.campaign.distributor import add_intervention_scheduled, add_intervention_triggered
    from emodpy_malaria.campaign.individual_intervention import AntimalarialDrug, SimpleBednet

    campaign.set_schema(manifest.schema_path)
    # Background: case management + nets, identical across scenarios.
    add_intervention_triggered(
        campaign,
        intervention_list=[
            AntimalarialDrug(campaign, drug_type="Artemether"),
            AntimalarialDrug(campaign, drug_type="Lumefantrine"),
        ],
        triggers_list=["NewClinicalCase"],
        start_day=1,
        target_demographics_config=TDC(demographic_coverage=case_mgmt),
    )
    add_intervention_triggered(
        campaign,
        intervention_list=[
            AntimalarialDrug(campaign, drug_type="Artemether"),
            AntimalarialDrug(campaign, drug_type="Lumefantrine"),
        ],
        triggers_list=["NewSevereCase"],
        start_day=1,
        target_demographics_config=TDC(demographic_coverage=severe_mgmt),
    )
    bednet = SimpleBednet(
        campaign,
        repelling_config=waning.Exponential(initial_effect=0.3, decay_time_constant=400),
        blocking_config=waning.Exponential(initial_effect=0.9, decay_time_constant=730),
        killing_config=waning.Exponential(initial_effect=0.6, decay_time_constant=1460),
    )
    net_interval = 3 * 365
    net_start = 5
    while net_start - start_shift < 1:  # keep the 3-year net cycle when the clock is shifted
        net_start += net_interval
    add_intervention_scheduled(
        campaign,
        intervention_list=[bednet],
        start_day=net_start - start_shift,
        repetition_config=RepetitionConfig(infinite_repetitions=True, timesteps_between_repetitions=net_interval),
        target_demographics_config=TDC(demographic_coverage=setting["net_coverage"]),
    )

    # Declare PMC_Dose in every scenario (no-PMC included) so the event counter
    # validates: a broadcast aimed at an age nobody reaches.
    add_intervention_scheduled(
        campaign,
        intervention_list=[BroadcastEvent(campaign, broadcast_event="PMC_Dose")],
        start_day=1,
        target_demographics_config=TDC(target_age_min=150, target_age_max=151),
    )
    for offset, interval, reps, age_min, age_max, cov in rounds:
        add_intervention_scheduled(
            campaign,
            intervention_list=chemoprevention_drugs(campaign, drug)
            + [BroadcastEvent(campaign, broadcast_event="PMC_Dose")],
            start_day=burn_in_days + offset - start_shift,
            repetition_config=RepetitionConfig(
                number_repetitions=int(reps), timesteps_between_repetitions=int(interval)
            ),
            target_demographics_config=TDC(demographic_coverage=cov, target_age_min=age_min, target_age_max=age_max),
        )
    return campaign


def build_reports(reporters, report_start=BURN_IN_DAYS, report_end=SIM_DAYS, n_years=2):
    from emodpy.reporters.base import ReportFilter as BaseFilter
    from emodpy.reporters.common import ReportEventCounter
    from emodpy_malaria.reporters.reporters import InsetChart, MalariaSummaryReport, ReportFilter

    reporters.add(
        MalariaSummaryReport(
            reporters,
            reporting_interval=365,
            age_bins=AGE_BINS,
            max_number_reports=n_years,
            pretty_format=True,
            report_filter=ReportFilter(start_day=report_start, end_day=report_end, filename_suffix="annual"),
        )
    )
    reporters.add(
        ReportEventCounter(
            reporters, event_list=["PMC_Dose"], report_filter=BaseFilter(start_day=report_start, end_day=report_end)
        )
    )
    reporters.add(InsetChart(reporters))
    return reporters


def survey_window(burnin_days, survey_doy):
    """Simulation days [start, end) of days-of-year survey_doy = (first, last), 1-based, in the burn-in's last year."""
    last_year = burnin_days - 365
    return last_year + survey_doy[0] - 1, last_year + survey_doy[1]


def build_burnin_reports(reporters, burnin_days, survey_doy):
    """A burn-in's reports: build_reports' annual set, plus one summary report averaged over the survey window
    (MalariaSummaryReport_survey.json), which calibration compares with a survey's measured prevalence.
    Reports are outputs only; they do not change the serialized population.
    """
    from emodpy_malaria.reporters.reporters import MalariaSummaryReport, ReportFilter

    build_reports(reporters, report_start=0, report_end=burnin_days, n_years=burnin_days // 365)
    start, end = survey_window(burnin_days, survey_doy)
    reporters.add(
        MalariaSummaryReport(
            reporters,
            reporting_interval=end - start,
            age_bins=AGE_BINS,
            max_number_reports=1,
            pretty_format=True,
            report_filter=ReportFilter(start_day=start, end_day=end, filename_suffix="survey"),
        )
    )
    return reporters


def scenario_setter(setting=None, schedules=None, drugs=None, **campaign_kwargs):
    """A sweep definition (simulation, value) that gives each simulation the campaign for schedule `value`,
    giving drugs[value] (default "SP").

    (A closure rather than a partial: idmtools counts a partial's bound keywords as missing arguments.)
    """
    schedules = SCENARIOS if schedules is None else schedules
    drugs = drugs or {}

    def set_scenario(simulation, value):
        drug = drugs.get(value, "SP")
        simulation.task.create_campaign_from_callback(
            partial(build_campaign, setting=setting, rounds=schedules[value], drug=drug, **campaign_kwargs)
        )
        return {"scenario": value, "drug": drug}

    return set_scenario


def set_seed(simulation, value):
    simulation.task.config.parameters.Run_Number = value
    return {"Run_Number": value}


# build_reports' age bins [0.25, 2, 5, 115] give four bins: 0 = (0, 0.25], 1 = (0.25, 2], 2 = (2, 5], 3 = (5, 115].
BIN_3_24M = 1
BIN_2_5Y = 2
BINS_U5 = (0, 1, 2)


def outcomes(msr, ec):
    """One run's outcomes from its MalariaSummaryReport_annual and ReportEventCounter JSON.

    Cases are summed over the reported years; children and PfPR are averaged over them. The under-5 fields
    (bins 0-2, i.e. 0-5 years) sit alongside the 3-24-month ones.
    """
    data = msr["DataByTimeAndAgeBins"]
    inc = data["Annual Clinical Incidence by Age Bin"]
    pop = data["Average Population by Age Bin"]
    pfpr = data["PfPR by Age Bin"]
    years = range(len(inc))
    return {
        "cases_3_24m": sum(inc[y][BIN_3_24M] * pop[y][BIN_3_24M] for y in years),
        "kids_3_24m": sum(pop[y][BIN_3_24M] for y in years) / len(pop),
        "cases_u5": sum(inc[y][b] * pop[y][b] for y in years for b in BINS_U5),
        "kids_u5": sum(pop[y][b] for y in years for b in BINS_U5) / len(pop),
        "pfpr_2_5y": sum(pfpr[y][BIN_2_5Y] for y in years) / len(pfpr),
        "doses": sum(ec["Channels"]["PMC_Dose"]["Data"]),
    }


def last_year_pfpr_2_5y(msr):
    """PfPR 2-5y over the last reported year (a burn-in's year 2)."""
    return msr["DataByTimeAndAgeBins"]["PfPR by Age Bin"][-1][BIN_2_5Y]


def summarise(experiment, platform, out):
    from idmtools.analysis.analyze_manager import AnalyzeManager
    from idmtools.analysis.download_analyzer import DownloadAnalyzer

    files = ["output/MalariaSummaryReport_annual.json", "output/ReportEventCounter.json", "output/InsetChart.json"]
    mgr = AnalyzeManager(platform=platform, analyzers=[DownloadAnalyzer(filenames=files, output_path=out)])
    mgr.add_item(experiment)
    mgr.analyze()
    rows = []
    for sim in experiment.simulations:
        d = os.path.join(out, str(sim.id))
        with open(os.path.join(d, "MalariaSummaryReport_annual.json")) as f:
            msr = json.load(f)
        with open(os.path.join(d, "ReportEventCounter.json")) as f:
            ec = json.load(f)
        rows.append(
            dict(
                scenario=sim.tags["scenario"],
                drug=sim.tags.get("drug", "SP"),
                seed=sim.tags["Run_Number"],
                **outcomes(msr, ec),
            )
        )
    with open(os.path.join(out, "summary.json"), "w") as f:
        json.dump(rows, f, indent=1)
    return rows


def main():
    import manifest
    from emodpy.emod_task import EMODTask
    from idmtools.builders import SimulationBuilder
    from idmtools.core.platform_factory import Platform
    from idmtools.entities.experiment import Experiment

    platform = Platform(
        "Container", job_directory=manifest.job_dir, docker_image=manifest.plat_image, sym_link=False, max_job=6
    )
    setting = default_setting()
    task = EMODTask.from_defaults(
        eradication_path=manifest.eradication_path,
        schema_path=manifest.schema_path,
        config_builder=partial(build_config, setting=setting),
        campaign_builder=partial(build_campaign, setting=setting),
        demographics_builder=partial(build_demographics, setting),
        report_builder=build_reports,
    )
    builder = SimulationBuilder()
    scenarios = sys.argv[1].split(",") if len(sys.argv) > 1 else list(SCENARIOS)
    builder.add_sweep_definition(scenario_setter(setting), scenarios)
    builder.add_sweep_definition(set_seed, SEEDS)
    exp = Experiment.from_builder(builder, task, name="connect_pmc_sweep")
    exp.run(wait_until_done=True, platform=platform)
    if not exp.succeeded:
        print("FAILED", exp.id)
        sys.exit(1)
    rows = summarise(exp, platform, "pmc_results")
    print(json.dumps(rows, indent=1))


if __name__ == "__main__":
    import emod_malaria.bootstrap as dtk
    import manifest

    dtk.setup(pathlib.Path(manifest.eradication_path).parent)
    main()
