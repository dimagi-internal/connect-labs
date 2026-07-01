"""Pure-validation tests for the authored PAR manifests (no DB, no engine).

These assert that the three checked-in PAR manifest files
(``envs/manifests/par-northern.yaml``, ``envs/manifests/par-southern.yaml``,
``envs/program-admin-report.yaml``) parse against their pydantic schemas and stay
internally consistent with each other and with the source demo config:

- every ``flw_persona`` carries a real ``display_name`` (never its raw id),
- every ``anomaly``/``coaching_arc`` references a real persona id (enforced by the
  ``Manifest`` model's own reference validator — we assert it does NOT raise),
- the env's two ``opp_data`` manifest paths resolve to files that exist,
- the env timeline + weekly_runs ``missed_week_idxs`` match ``demo_config.json``.

No Django DB is touched, but importing the schema modules pulls in Django, so the
GIS env vars (GDAL/GEOS) must be set to collect this module on macOS.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from connect_labs.labs.synthetic.ensure.env_manifest import EnvManifest
from connect_labs.labs.synthetic.generator.fixtures.manifest import Manifest

# envs/ dir holding the composite env manifest; manifests/ holds the per-opp ones.
ENVS_DIR = Path(__file__).resolve().parents[2] / "envs"
ENV_FILE = ENVS_DIR / "program-admin-report.yaml"
NORTHERN_FILE = ENVS_DIR / "manifests" / "par-northern.yaml"
SOUTHERN_FILE = ENVS_DIR / "manifests" / "par-southern.yaml"

DEMO_CONFIG = (
    Path(__file__).resolve().parents[5] / "scripts" / "walkthroughs" / "program-admin-report" / "demo_config.json"
)


@pytest.fixture(scope="module")
def northern() -> Manifest:
    return Manifest.from_yaml(NORTHERN_FILE.read_text())


@pytest.fixture(scope="module")
def southern() -> Manifest:
    return Manifest.from_yaml(SOUTHERN_FILE.read_text())


@pytest.fixture(scope="module")
def env() -> EnvManifest:
    return EnvManifest.from_yaml(ENV_FILE.read_text())


@pytest.fixture(scope="module")
def demo_config() -> dict:
    return json.loads(DEMO_CONFIG.read_text())


# ---------------------------------------------------------------------- #
# Per-opp manifests parse
# ---------------------------------------------------------------------- #


def test_northern_parses(northern: Manifest):
    assert northern.opportunity_id == 10000
    assert northern.opportunity_name == "Northern Cluster"


def test_southern_parses(southern: Manifest):
    assert southern.opportunity_id == 10001
    assert southern.opportunity_name == "Southern Cluster"


# ---------------------------------------------------------------------- #
# Every persona has a real display name (not its id)
# ---------------------------------------------------------------------- #


@pytest.mark.parametrize("manifest_fixture", ["northern", "southern"])
def test_every_persona_has_real_display_name(manifest_fixture, request):
    manifest: Manifest = request.getfixturevalue(manifest_fixture)
    for persona in manifest.flw_personas:
        assert persona.display_name, f"{persona.id} has no display_name"
        assert persona.display_name.strip(), f"{persona.id} display_name is blank"
        assert persona.display_name != persona.id, f"{persona.id} display_name == id"


# ---------------------------------------------------------------------- #
# References resolve (Manifest's own validator would have raised if not)
# ---------------------------------------------------------------------- #


@pytest.mark.parametrize("manifest_fixture", ["northern", "southern"])
def test_anomaly_and_arc_references_resolve(manifest_fixture, request):
    manifest: Manifest = request.getfixturevalue(manifest_fixture)
    persona_ids = {p.id for p in manifest.flw_personas}
    # The Manifest model's _check_references validator raises on any dangling
    # ref; reaching this point means it passed. Re-assert explicitly so the test
    # documents the contract and fails loudly if the model ever loosens.
    for anomaly in manifest.anomalies:
        assert set(anomaly.flw_ids) <= persona_ids, f"anomaly {anomaly.id} dangles"
    for arc in manifest.coaching_arcs:
        assert arc.flw_id in persona_ids, f"coaching_arc for {arc.flw_id} dangles"


def test_manifest_validator_rejects_dangling_reference():
    """Sanity-check that the reuse above is meaningful: the validator DOES raise."""
    from connect_labs.labs.synthetic.generator.fixtures.manifest import ManifestValidationError

    bad = NORTHERN_FILE.read_text().replace("flw_ids: [hawa_n]", "flw_ids: [ghost_flw]")
    with pytest.raises(ManifestValidationError):
        Manifest.from_yaml(bad)


# ---------------------------------------------------------------------- #
# Coaching arcs target flagged FLWs, never the network-manager stand-in
# ---------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "manifest_fixture,nm_id",
    [("northern", "amani_nm"), ("southern", "kwame_nm")],
)
def test_network_manager_is_first_persona_and_uncoached(manifest_fixture, nm_id, request):
    manifest: Manifest = request.getfixturevalue(manifest_fixture)
    # NM must be the FIRST persona: weekly_runs uses flw_personas[0] for flag
    # attribution and tasks uses the first non-arc-FLW persona as creator, so a
    # first-position NM is what makes both render the real manager name.
    assert manifest.flw_personas[0].id == nm_id
    # And no coaching arc may target the NM (it would steal the creator stand-in).
    arc_flws = {arc.flw_id for arc in manifest.coaching_arcs}
    assert nm_id not in arc_flws


# ---------------------------------------------------------------------- #
# Env manifest parses + cross-checks against demo_config.json
# ---------------------------------------------------------------------- #


def test_env_parses(env: EnvManifest):
    assert env.env == "program-admin-report"
    assert [r.kind for r in env.resources] == [
        "opp_data",
        "opp_data",
        "weekly_runs",
        "run_audits",
        "tasks",
        "rollup",
    ]


def test_env_opp_data_manifests_exist(env: EnvManifest):
    opp_data = [r for r in env.resources if r.kind == "opp_data"]
    assert {r.opportunity_id for r in opp_data} == {10000, 10001}
    for r in opp_data:
        resolved = (ENV_FILE.parent / r.manifest).resolve()
        assert resolved.exists(), f"opp_data manifest {r.manifest} missing at {resolved}"
        # And it actually parses as a Manifest.
        Manifest.from_yaml(resolved.read_text())


def test_env_timeline_matches_demo_config(env: EnvManifest, demo_config: dict):
    assert env.timeline.completed_weeks == 4
    assert env.timeline.completed_weeks == demo_config["completed_weeks"]
    assert env.timeline.include_current_week is True


def test_env_missed_week_idxs_match_demo_config(env: EnvManifest, demo_config: dict):
    weekly = next(r for r in env.resources if r.kind == "weekly_runs")
    expected = {opp["opportunity_id"]: opp.get("missed_week_idxs", []) for opp in demo_config["opps"]}
    assert weekly.missed_week_idxs == expected


def test_env_rollup_and_weekly_cover_both_opps(env: EnvManifest):
    weekly = next(r for r in env.resources if r.kind == "weekly_runs")
    rollup = next(r for r in env.resources if r.kind == "rollup")
    assert set(weekly.opportunity_ids) == {10000, 10001}
    assert set(rollup.opportunity_ids) == {10000, 10001}
    assert weekly.template == "chc_nutrition_analysis"
    assert rollup.template == "program_admin_report"
    assert weekly.current_week is not None and weekly.current_week.reset is True
