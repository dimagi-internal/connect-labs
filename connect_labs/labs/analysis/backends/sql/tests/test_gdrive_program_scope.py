"""PROGRAM-scoped Google Drive sources (gdrive_fetcher, "Program scope").

The Connect Interviews dashboard reads Drive files that cover a whole program -- every
cohort's raw interview answers. Two rules follow, and these pin both:

- WHO: only members of the organization that OWNS the program may read it.
  Membership in one of the program's opportunities is NOT enough -- those are
  partner network organizations who must not see other cohorts' answers. When the
  caller's org tree cannot be read, the source is not read.
- HOW OFTEN: the source is read and aggregated ONCE for the program, under the
  program's own cache key -- not once per opportunity, which would multiply every
  count by the number of opportunities.
"""

import pytest

from connect_labs.labs.analysis.backends.sql import gdrive_fetcher as gf
from connect_labs.labs.analysis.backends.sql.models import ComputedEntityCache, RawVisitCache
from connect_labs.labs.analysis.backends.sql.tests.test_gdrive_fetcher import (  # noqa: F401 - fixtures
    OPP,
    PARTNER,
    STAFF,
    allowed_root,
    drive,
)
from connect_labs.labs.analysis.config import AnalysisPipelineConfig, CacheStage, DataSourceConfig, FieldComputation
from connect_labs.labs.analysis.pipeline import AnalysisPipeline

PROGRAM = 121
OWNER_ORG = "dimagi-interviews"
TARGET = {"type": "gdrive", "folder_id": "folderA", "file_pattern": "answers_scored_*.csv"}

# The caller's Connect org tree (/export/opp_org_program_list/ shape): `programs` holds
# only programs whose owning organization the caller belongs to, naming its slug.
OWNER_TREE = {
    "organizations": [{"id": 1, "slug": OWNER_ORG, "name": "Dimagi Interviews"}],
    "programs": [{"id": PROGRAM, "name": "Connect Interviews - Nigeria", "organization": OWNER_ORG}],
    "opportunities": [{"id": OPP, "program": PROGRAM}, {"id": 1252, "program": PROGRAM}],
}
# A partner network org: it holds an opportunity IN the program, but not the program.
OPPORTUNITY_ONLY_TREE = {
    "organizations": [{"id": 2, "slug": "partner-llo", "name": "Partner"}],
    "programs": [],
    "opportunities": [{"id": OPP, "program": PROGRAM, "organization": "partner-llo"}],
}
STRANGER_TREE = {"organizations": [{"id": 3, "slug": "elsewhere"}], "programs": [], "opportunities": []}


def _program_source(pipeline_id=7, **overrides):
    return DataSourceConfig(
        **gf.authorize_gdrive_source({**TARGET, **overrides}, None, STAFF, pipeline_id, program_id=PROGRAM)
    )


@pytest.fixture
def tree(monkeypatch):
    """Set the caller's org tree; returns a setter."""
    current = {"tree": OWNER_TREE}
    monkeypatch.setattr(gf, "_caller_org_tree", lambda request, token: current["tree"])

    def set_tree(value):
        current["tree"] = value

    return set_tree


# --------------------------------------------------------------------------- the stamp


def test_a_program_stamp_names_the_program_and_verifies_for_it():
    src = _program_source()
    assert src.authorization["program_id"] == PROGRAM and "opportunity_id" not in src.authorization
    gf.verify_gdrive_authorization(src, None, 7, program_id=PROGRAM)
    assert gf.gdrive_program_id(src) == PROGRAM


def test_a_program_stamp_is_bound_to_its_program_and_pipeline():
    src = _program_source()
    with pytest.raises(gf.GDriveSourceError, match="program 122"):
        gf.verify_gdrive_authorization(src, None, 7, program_id=122)
    with pytest.raises(gf.GDriveSourceError, match="not authorized"):
        gf.verify_gdrive_authorization(src, None, 8, program_id=PROGRAM)


def test_an_opportunity_stamp_never_passes_for_a_program_of_the_same_number_or_back():
    opp_stamped = DataSourceConfig(**gf.authorize_gdrive_source(TARGET, PROGRAM, STAFF, 7))
    with pytest.raises(gf.GDriveSourceError):
        gf.verify_gdrive_authorization(opp_stamped, None, 7, program_id=PROGRAM)
    with pytest.raises(gf.GDriveSourceError):
        gf.verify_gdrive_authorization(_program_source(), PROGRAM, 7)


def test_relabelling_an_opportunity_stamp_as_a_program_one_is_refused(tree):
    stamp = gf.authorize_gdrive_source(TARGET, OPP, STAFF, 7)["authorization"]
    forged = DataSourceConfig(**TARGET, authorization={**stamp, "opportunity_id": None, "program_id": PROGRAM})
    assert gf.gdrive_program_id(forged) == PROGRAM  # routes to the program gate...
    with pytest.raises(gf.GDriveSourceError, match="not authorized"):  # ...which refuses it
        gf.check_gdrive_access(forged, OPP, pipeline_id=7)


def test_only_staff_can_stamp_for_a_program():
    with pytest.raises(gf.GDriveSourceError, match="Dimagi staff"):
        gf.authorize_gdrive_source(TARGET, None, PARTNER, 7, program_id=PROGRAM)


def test_a_stamp_names_exactly_one_scope():
    with pytest.raises(gf.GDriveSourceError, match="exactly one"):
        gf.authorize_gdrive_source(TARGET, OPP, STAFF, 7, program_id=PROGRAM)
    with pytest.raises(gf.GDriveSourceError, match="exactly one"):
        gf.authorize_gdrive_source(TARGET, None, STAFF, 7)


def test_schema_save_in_a_program_scope_stamps_for_the_program_and_keeps_it():
    schema = {"data_source": dict(TARGET)}
    saved = gf.authorize_schema_drive_source(schema, None, STAFF, pipeline_id=7, program_id=PROGRAM)
    assert saved["data_source"]["authorization"]["program_id"] == PROGRAM
    # A partner re-saving the unchanged, stamped target keeps the program stamp.
    resaved = gf.authorize_schema_drive_source(saved, None, PARTNER, pipeline_id=7, program_id=PROGRAM)
    assert resaved is saved


# --------------------------------------------------------------------------- the gate


def test_a_member_of_the_owning_organization_may_read(tree):
    gf.check_gdrive_access(_program_source(), None, pipeline_id=7)
    gf.check_gdrive_access(_program_source(), OPP, pipeline_id=7)  # the opp asked for is irrelevant


def test_membership_in_one_of_the_programs_opportunities_is_not_enough(tree):
    tree(OPPORTUNITY_ONLY_TREE)
    with pytest.raises(gf.GDriveSourceError, match="manages program 121"):
        gf.check_gdrive_access(_program_source(), OPP, pipeline_id=7)


def test_a_non_member_is_refused(tree):
    tree(STRANGER_TREE)
    with pytest.raises(gf.GDriveSourceError, match="manages program 121"):
        gf.check_gdrive_access(_program_source(), None, pipeline_id=7)


def test_a_program_listed_under_an_organization_the_caller_is_not_in_is_refused(tree):
    tree({**OWNER_TREE, "organizations": [{"slug": "partner-llo"}]})
    with pytest.raises(gf.GDriveSourceError, match="manages program 121"):
        gf.check_gdrive_access(_program_source(), None, pipeline_id=7)


def test_an_unresolvable_caller_fails_closed(tree):
    tree(None)
    with pytest.raises(gf.GDriveSourceError, match="cannot confirm"):
        gf.check_gdrive_access(_program_source(), None, pipeline_id=7)


def test_an_opportunity_stamp_still_needs_opportunity_membership(drive):  # noqa: F811
    src = DataSourceConfig(**gf.authorize_gdrive_source(TARGET, OPP, STAFF, 7))
    gf.check_gdrive_access(src, OPP, pipeline_id=7)
    with pytest.raises(gf.GDriveSourceError, match="no opportunity was given"):
        gf.check_gdrive_access(src, None, pipeline_id=7)


def test_ownership_is_read_from_the_web_session_tree():
    from types import SimpleNamespace

    def request(org_data):
        return SimpleNamespace(
            user=SimpleNamespace(is_authenticated=True, view_synthetic_opps=False),
            session={"labs_oauth": {"organization_data": org_data}},
        )

    assert gf._caller_owns_program(request(OWNER_TREE), None, PROGRAM) is True
    assert gf._caller_owns_program(request(OPPORTUNITY_ONLY_TREE), None, PROGRAM) is False
    assert gf._caller_owns_program(None, None, PROGRAM) is None


def test_ownership_is_read_from_a_celery_token(monkeypatch):
    from types import SimpleNamespace

    from connect_labs.labs.integrations.connect import oauth

    monkeypatch.setattr(oauth, "fetch_user_organization_data", lambda token, **kw: OWNER_TREE)
    monkeypatch.setattr("connect_labs.labs.integrations.ocs.ocs_tokens.current_mcp_caller", lambda: None)
    request = SimpleNamespace(user=None, session={"labs_oauth": {"access_token": "job-tok"}})
    assert gf._caller_owns_program(request, None, PROGRAM) is True
    monkeypatch.setattr(oauth, "fetch_user_organization_data", lambda token, **kw: None)
    assert gf._caller_owns_program(request, None, PROGRAM) is None


# --------------------------------------------------------------------------- read once


def _config(pipeline_id, stage=CacheStage.VISIT_LEVEL, field_name="state"):
    return AnalysisPipelineConfig(
        grouping_key="username",
        fields=[FieldComputation(name=field_name, path="row.state", aggregation="first")],
        terminal_stage=stage,
        linking_field=field_name if stage == CacheStage.ENTITY else "entity_id",
        data_source=_program_source(pipeline_id),
        pipeline_id=pipeline_id,
    )


def _rows(config, opp):
    events = list(AnalysisPipeline(access_token="tok").stream_analysis(config, opp))
    assert events[-1][0] == "result", events[-1]
    return events[-1][1].rows


def _downloads(fake):
    return [c for c in fake.calls if c[0] == "download"]


@pytest.mark.django_db
@pytest.mark.parametrize("stage", [CacheStage.VISIT_LEVEL, CacheStage.ENTITY])
def test_a_program_source_is_read_and_aggregated_once_whichever_opportunity_asks(drive, tree, stage):  # noqa: F811
    config = _config(7, stage=stage, field_name="state_key" if stage == CacheStage.ENTITY else "state")
    drive.calls.clear()
    per_opp = [_rows(config, opp) for opp in (OPP, 1252, 1253, None)]

    assert len(_downloads(drive)) == 2, "Drive was read more than once (two files, once)"
    assert all(len(rows) == 3 for rows in per_opp), [len(r) for r in per_opp]
    # Stored once, under the program's key -- never under an opportunity.
    assert set(RawVisitCache.objects.values_list("opportunity_id", flat=True)) == {gf.program_cache_scope(PROGRAM)}
    assert RawVisitCache.objects.count() == 3
    if stage == CacheStage.ENTITY:
        assert {r.state_key for r in per_opp[0]} == {"Kebbi", "Borno", "NA"}
        assert set(ComputedEntityCache.objects.values_list("opportunity_id", flat=True)) == {-PROGRAM}


@pytest.mark.django_db
def test_a_warm_program_cache_is_no_permission_to_read_it(drive, tree):  # noqa: F811
    config = _config(7)
    _rows(config, None)
    tree(OPPORTUNITY_ONLY_TREE)
    events = list(AnalysisPipeline(access_token="tok").stream_analysis(config, OPP))
    assert events[-1][0] == "error" and "manages program 121" in events[-1][1]["message"]
    with pytest.raises(gf.GDriveSourceError):
        AnalysisPipeline(access_token="tok").get_cached_result_only(config, OPP)


def test_data_scope_maps_only_program_drive_sources():
    assert AnalysisPipeline.data_scope(_config(7), OPP) == -PROGRAM
    assert AnalysisPipeline.data_scope(_config(7), None) == -PROGRAM
    assert AnalysisPipeline.data_scope(_config(7), -PROGRAM) == -PROGRAM  # idempotent
    opp_stamped = AnalysisPipelineConfig(
        grouping_key="username", data_source=DataSourceConfig(**gf.authorize_gdrive_source(TARGET, OPP, STAFF, 7))
    )
    assert AnalysisPipeline.data_scope(opp_stamped, OPP) == OPP
    assert AnalysisPipeline.data_scope(AnalysisPipelineConfig(grouping_key="username"), OPP) == OPP
