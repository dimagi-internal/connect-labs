"""Connect PMC schedule sweep on EMOD — prototype for the IDM talk demo.

Compares PMC delivery configurations in one perennial, seasonally-peaked
setting (southern-Nigeria-like). Outcome: clinical malaria cases in children
3-24 months, and SP doses delivered, over 2 intervention years after burn-in.
"""

import json
import os
import pathlib
import sys
from functools import partial

import manifest
from emodpy.campaign.common import RepetitionConfig
from emodpy.emod_task import EMODTask
from idmtools.builders import SimulationBuilder
from idmtools.core.platform_factory import Platform
from idmtools.entities.experiment import Experiment

BURN_IN_DAYS = 2 * 365
INTERVENTION_DAYS = 2 * 365
SIM_DAYS = BURN_IN_DAYS + INTERVENTION_DAYS
POP = int(os.environ.get("PMC_POP", 10000))
LARVAL_CAPACITY = float(os.environ.get("PMC_LARVAL", 3e8))
SEEDS = [int(s) for s in os.environ.get("PMC_SEEDS", "0,1").split(",")]

# Southern-Nigeria-like: year-round transmission, peak in the long rains
# (roughly Apr-Oct). Values are relative habitat by day of year.
HABITAT = {
    "Times": [0, 30, 60, 91, 122, 152, 182, 213, 243, 274, 304, 334, 365],
    "Values": [1.0, 0.8, 1.0, 2.0, 4.0, 6.0, 6.0, 5.0, 6.0, 5.0, 3.0, 1.5, 1.0],
}
SEASON_START = 91  # high season begins ~1 April
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


def build_config(config):
    import emodpy_malaria.malaria_config as malaria_config
    from emodpy_malaria.utils.emod_enum import HabitatType

    config = malaria_config.set_team_defaults(config, manifest)
    malaria_config.add_species(config, manifest, ["gambiae", "funestus"])
    config.parameters.Run_Number = 0
    config.parameters.Simulation_Duration = SIM_DAYS
    habitat = malaria_config.VectorHabitat(
        habitat_type=HabitatType.LINEAR_SPLINE,
        max_larval_capacity=LARVAL_CAPACITY,
        capacity_distribution_number_of_years=1,
        capacity_distribution_over_time=HABITAT,
    )
    for species in ["gambiae", "funestus"]:
        malaria_config.set_species_param(config, species, "Habitats", habitat, overwrite=True)
    config.parameters.x_Base_Population = manifest.x_Base_Population_scale
    return config


def build_demographics():
    from emodpy_malaria.demographics import MalariaDemographics as Demographics
    from emodpy_malaria.utils.distributions import UniformDistribution
    from emodpy_malaria.utils.emod_enum import BirthRateDependence

    demog = Demographics.from_template_node(lat=7.25, lon=5.2, pop=POP, name="SW_Nigeria_like")
    demog.set_birth_rate(37, birth_rate_dependence=BirthRateDependence.POPULATION_DEP_RATE)
    demog.set_age_distribution(UniformDistribution(0, 60))
    demog.set_initial_prevalence_distribution(UniformDistribution(0.1, 0.3))
    return demog


def build_campaign(campaign, scenario="none"):
    import emodpy_malaria.campaign.waning_config as waning
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
        target_demographics_config=TDC(demographic_coverage=0.5),
    )
    add_intervention_triggered(
        campaign,
        intervention_list=[
            AntimalarialDrug(campaign, drug_type="Artemether"),
            AntimalarialDrug(campaign, drug_type="Lumefantrine"),
        ],
        triggers_list=["NewSevereCase"],
        start_day=1,
        target_demographics_config=TDC(demographic_coverage=0.7),
    )
    bednet = SimpleBednet(
        campaign,
        repelling_config=waning.Exponential(initial_effect=0.3, decay_time_constant=400),
        blocking_config=waning.Exponential(initial_effect=0.9, decay_time_constant=730),
        killing_config=waning.Exponential(initial_effect=0.6, decay_time_constant=1460),
    )
    add_intervention_scheduled(
        campaign,
        intervention_list=[bednet],
        start_day=5,
        repetition_config=RepetitionConfig(infinite_repetitions=True, timesteps_between_repetitions=3 * 365),
        target_demographics_config=TDC(demographic_coverage=0.5),
    )

    # Declare PMC_Dose in every scenario (no-PMC included) so the event counter
    # validates: a broadcast aimed at an age nobody reaches.
    add_intervention_scheduled(
        campaign,
        intervention_list=[BroadcastEvent(campaign, broadcast_event="PMC_Dose")],
        start_day=1,
        target_demographics_config=TDC(target_age_min=150, target_age_max=151),
    )
    for offset, interval, reps, age_min, age_max, cov in SCENARIOS[scenario]:
        add_intervention_scheduled(
            campaign,
            intervention_list=[
                AntimalarialDrug(campaign, drug_type="SulfadoxinePyrimethamine"),
                BroadcastEvent(campaign, broadcast_event="PMC_Dose"),
            ],
            start_day=BURN_IN_DAYS + offset,
            repetition_config=RepetitionConfig(number_repetitions=reps, timesteps_between_repetitions=interval),
            target_demographics_config=TDC(demographic_coverage=cov, target_age_min=age_min, target_age_max=age_max),
        )
    return campaign


def build_reports(reporters):
    from emodpy.reporters.base import ReportFilter as BaseFilter
    from emodpy.reporters.common import ReportEventCounter
    from emodpy_malaria.reporters.reporters import InsetChart, MalariaSummaryReport, ReportFilter

    reporters.add(
        MalariaSummaryReport(
            reporters,
            reporting_interval=365,
            age_bins=[0.25, 2, 5, 115],
            max_number_reports=2,
            pretty_format=True,
            report_filter=ReportFilter(start_day=BURN_IN_DAYS, end_day=SIM_DAYS, filename_suffix="annual"),
        )
    )
    reporters.add(
        ReportEventCounter(
            reporters, event_list=["PMC_Dose"], report_filter=BaseFilter(start_day=BURN_IN_DAYS, end_day=SIM_DAYS)
        )
    )
    reporters.add(InsetChart(reporters))
    return reporters


def set_scenario(simulation, value):
    simulation.task.create_campaign_from_callback(partial(build_campaign, scenario=value))
    return {"scenario": value}


def set_seed(simulation, value):
    simulation.task.config.parameters.Run_Number = value
    return {"Run_Number": value}


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
        inc = msr["DataByTimeAndAgeBins"]["Annual Clinical Incidence by Age Bin"]
        pop = msr["DataByTimeAndAgeBins"]["Average Population by Age Bin"]
        pfpr = msr["DataByTimeAndAgeBins"]["PfPR by Age Bin"]
        # age bin index 1 = (0.25, 2]
        cases = sum(inc[y][1] * pop[y][1] for y in range(len(inc)))
        doses = sum(ec["Channels"]["PMC_Dose"]["Data"])
        rows.append(
            {
                "scenario": sim.tags["scenario"],
                "seed": sim.tags["Run_Number"],
                "cases_3_24m": cases,
                "kids_3_24m": sum(p[1] for p in pop) / len(pop),
                "pfpr_2_5y": sum(p[2] for p in pfpr) / len(pfpr),
                "doses": doses,
            }
        )
    with open(os.path.join(out, "summary.json"), "w") as f:
        json.dump(rows, f, indent=1)
    return rows


def main():
    platform = Platform(
        "Container", job_directory=manifest.job_dir, docker_image=manifest.plat_image, sym_link=False, max_job=6
    )
    task = EMODTask.from_defaults(
        eradication_path=manifest.eradication_path,
        schema_path=manifest.schema_path,
        config_builder=build_config,
        campaign_builder=build_campaign,
        demographics_builder=build_demographics,
        report_builder=build_reports,
    )
    builder = SimulationBuilder()
    scenarios = sys.argv[1].split(",") if len(sys.argv) > 1 else list(SCENARIOS)
    builder.add_sweep_definition(set_scenario, scenarios)
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

    dtk.setup(pathlib.Path(manifest.eradication_path).parent)
    main()
