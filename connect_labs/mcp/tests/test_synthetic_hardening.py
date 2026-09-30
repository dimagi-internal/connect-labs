"""Who may write generated data where, and which opps count as generated afterwards.

Every tool here takes an opportunity or program id from the caller. Labs-only opps
have no production permission check behind them, so these tools are the gate: a
caller must not be able to write onto a real Connect opp, onto another tenant's
labs-only opp, or into another tenant's program (a program is a read scope).
"""

import pytest

import connect_labs.mcp.tools.synthetic  # noqa: F401 — trigger @register side effects
from connect_labs.labs.synthetic import clone_from_prod
from connect_labs.labs.synthetic.bundle import write_bundle
from connect_labs.labs.synthetic.models import SyntheticOpportunity
from connect_labs.labs.synthetic.provenance import is_generated, mark_generated
from connect_labs.mcp.tool_registry import MCPToolError, get_tool

pytestmark = pytest.mark.django_db


class _FakeDrive:
    def __init__(self):
        self.created = []

    def create_folder(self, name, parent_id):
        self.created.append(name)
        return f"gen-{len(self.created)}"

    def upload_file(self, folder_id, filename, content):
        pass


@pytest.fixture
def partner(user):
    """A non-Dimagi caller: the labs-only access model applies to them in full."""
    user.email = "pat@partner.org"
    user.save()
    return user


@pytest.fixture
def drive(monkeypatch, settings):
    settings.LABS_SYNTHETIC_GDRIVE_PARENT_FOLDER_ID = "parent"
    fake = _FakeDrive()
    monkeypatch.setattr(connect_labs.mcp.tools.synthetic, "DriveClient", lambda: fake)
    return fake


def _bundle(tmp_path, source=523):
    manifest_yaml = (
        f"opportunity_id: {source}\n"
        "opportunity_name: KMC\n"
        "random_seed: 42\n"
        "timeline: {start_date: 2026-05-04, end_date: 2026-06-01, weeks: 4,"
        " visit_cadence_per_week_per_flw: {mean: 5, stddev: 1}}\n"
        "flw_personas: [{id: a, archetype: steady,"
        " accuracy_distribution: {mean: 0.8, stddev: 0.05},"
        " completeness_distribution: {mean: 0.8, stddev: 0.05}, flag_rate: 0.1}]\n"
        "beneficiary_cohorts: [{id: primary, size: 5, progression: flat,"
        ' field_distributions: {"form.w": {distribution: normal, mean: 12.0, stddev: 2.0}}}]\n'
        "kpi_config: [{kpi: a, field_path: form.w, aggregation: mean, threshold_underperform: 1.0}]\n"
    )
    return str(
        write_bundle(
            tmp_path,
            source,
            manifest_yaml=manifest_yaml,
            app_structure={"learn_app": None, "deliver_app": {"modules": []}},
            opportunity={"id": source, "name": "KMC"},
        )
    )


def _other_tenants_opp(opportunity_id, **extra):
    return SyntheticOpportunity.objects.create(
        opportunity_id=opportunity_id,
        gdrive_folder_id="theirs",
        labs_only=True,
        allowed_domains=["@other.org"],
        **extra,
    )


# --------------------------------------------------------------- synthetic_generate_opp


def test_generate_refuses_a_real_opportunity_as_target(partner, drive, tmp_path):
    with pytest.raises(MCPToolError) as exc:
        get_tool("synthetic_generate_opp").handler(
            partner, bundle_dir=_bundle(tmp_path), program_id=20_000, target_opportunity_id=4242
        )
    assert exc.value.code == "PERMISSION_DENIED"
    assert not SyntheticOpportunity.objects.filter(opportunity_id=4242).exists()
    assert drive.created == []


def test_generate_refuses_another_tenants_labs_only_opp_as_target(partner, drive, tmp_path):
    _other_tenants_opp(20_001)
    with pytest.raises(MCPToolError) as exc:
        get_tool("synthetic_generate_opp").handler(
            partner, bundle_dir=_bundle(tmp_path), program_id=20_500, target_opportunity_id=20_001
        )
    assert exc.value.code == "PERMISSION_DENIED"
    assert SyntheticOpportunity.objects.get(opportunity_id=20_001).gdrive_folder_id == "theirs"
    assert drive.created == []


def test_generate_refuses_another_tenants_program(partner, drive, tmp_path):
    """Filing an opp you own under their program would open their program's records to you."""
    _other_tenants_opp(20_002, program_id=20_600)
    with pytest.raises(MCPToolError) as exc:
        get_tool("synthetic_generate_opp").handler(partner, bundle_dir=_bundle(tmp_path), program_id=20_600)
    assert exc.value.code == "PERMISSION_DENIED"
    assert drive.created == []


def test_generate_refuses_a_program_id_below_the_labs_only_floor(partner, drive, tmp_path):
    with pytest.raises(MCPToolError):
        get_tool("synthetic_generate_opp").handler(partner, bundle_dir=_bundle(tmp_path), program_id=25)
    assert drive.created == []


def test_fresh_generate_will_not_overwrite_a_twin_the_caller_cannot_see(partner, drive, tmp_path):
    """The twin is found by SOURCE id, so the caller never named it — it may be anyone's."""
    _other_tenants_opp(20_003, cloned_from_opportunity_id=523)
    with pytest.raises(MCPToolError) as exc:
        get_tool("synthetic_generate_opp").handler(
            partner, bundle_dir=_bundle(tmp_path), program_id=20_700, fresh=True
        )
    assert exc.value.code == "PERMISSION_DENIED"
    assert SyntheticOpportunity.objects.get(opportunity_id=20_003).gdrive_folder_id == "theirs"
    assert drive.created == []


def test_generate_onto_a_new_target_is_owned_by_the_caller_and_generated(partner, drive, tmp_path):
    out = get_tool("synthetic_generate_opp").handler(
        partner, bundle_dir=_bundle(tmp_path), program_id=20_800, target_opportunity_id=20_801
    )
    row = SyntheticOpportunity.objects.get(opportunity_id=out["opportunity_id"])
    assert row.opportunity_id == 20_801
    assert row.created_by_id == partner.id
    assert row.is_accessible_to(partner)
    assert is_generated(row)


def test_bulk_fresh_skips_a_twin_the_caller_cannot_see(partner, drive, tmp_path):
    _other_tenants_opp(20_004, cloned_from_opportunity_id=523)
    root = tmp_path / "root"
    root.mkdir()
    _bundle(root, 523)
    out = get_tool("synthetic_generate_opps_bulk").handler(partner, bundle_root=str(root), fresh=True)
    assert out["results"] == []
    assert SyntheticOpportunity.objects.get(opportunity_id=20_004).gdrive_folder_id == "theirs"


def test_clone_generate_refuses_another_tenants_program(partner, drive):
    _other_tenants_opp(20_005, program_id=20_900)
    spec = "opportunity_ids: [523]\nbundle_root: /nowhere\nprogram_id: 20900\n"
    with pytest.raises(MCPToolError) as exc:
        get_tool("synthetic_clone_generate").handler(partner, spec_yaml=spec)
    assert exc.value.code == "PERMISSION_DENIED"


# ------------------------------------------------------------ synthetic_create_labs_only


def test_create_labs_only_refuses_another_tenants_program(partner):
    _other_tenants_opp(20_006, program_id=21_000)
    with pytest.raises(MCPToolError) as exc:
        get_tool("synthetic_create_labs_only").handler(
            partner, label="mine", gdrive_folder_id="f", program_id=21_000, allowed_domains=["@partner.org"]
        )
    assert exc.value.code == "PERMISSION_DENIED"
    assert not SyntheticOpportunity.objects.filter(label="mine").exists()


def test_create_labs_only_never_marks_the_callers_folder_generated(user):
    out = get_tool("synthetic_create_labs_only").handler(user, label="x", gdrive_folder_id="a-dump")
    row = SyntheticOpportunity.objects.get(opportunity_id=out["opportunity_id"])
    assert row.generated_folder_id is None
    assert not is_generated(row)


# -------------------------------------------------- tools that point an opp at a folder


def _generated_opp(opportunity_id, user, folder="gen-folder", **extra):
    SyntheticOpportunity.objects.create(
        opportunity_id=opportunity_id, gdrive_folder_id=folder, labs_only=True, created_by=user, **extra
    )
    mark_generated(opportunity_id, folder)
    assert is_generated(SyntheticOpportunity.objects.get(opportunity_id=opportunity_id))


def test_register_onto_a_new_folder_unmarks_the_opp(user):
    _generated_opp(21_100, user)
    get_tool("synthetic_register").handler(user, opportunity_id=21_100, gdrive_folder_id="a-prod-dump")
    assert not is_generated(SyntheticOpportunity.objects.get(opportunity_id=21_100))


def test_register_does_not_take_over_someone_elses_row(user):
    """The creator is an access grant, so re-registering must not reassign it."""
    creator = type(user).objects.create_user(username="creator", password="p", email="c@dimagi.com")
    SyntheticOpportunity.objects.create(
        opportunity_id=21_150, gdrive_folder_id="f", labs_only=True, created_by=creator
    )
    user.email = "ops@dimagi.com"
    user.save()
    get_tool("synthetic_register").handler(user, opportunity_id=21_150, gdrive_folder_id="g")
    assert SyntheticOpportunity.objects.get(opportunity_id=21_150).created_by_id == creator.id


def test_repoint_by_source_unmarks_the_opp(user):
    _generated_opp(21_200, user, cloned_from_opportunity_id=777)
    get_tool("synthetic_repoint_by_source").handler(user, source_opportunity_id=777, gdrive_folder_id="elsewhere")
    assert not is_generated(SyntheticOpportunity.objects.get(opportunity_id=21_200))


def test_the_labs_only_edit_form_unmarks_the_opp_when_its_folder_changes(user):
    from connect_labs.labs.synthetic.forms import LabsOnlySyntheticOpportunityForm

    _generated_opp(21_300, user)
    row = SyntheticOpportunity.objects.get(opportunity_id=21_300)
    form = LabsOnlySyntheticOpportunityForm(
        data={"label": "x", "gdrive_folder_id": "someone-elses", "enabled": True, "allowed_domains_input": ""},
        instance=row,
    )
    assert form.is_valid(), form.errors
    form.save()
    assert not is_generated(SyntheticOpportunity.objects.get(opportunity_id=21_300))


# ------------------------------------------------------------ synthetic_clone_to_labs_only


def test_a_clone_of_a_generated_opp_is_generated(user):
    _generated_opp(21_400, user)
    out = get_tool("synthetic_clone_to_labs_only").handler(user, source_opportunity_id=21_400)
    assert is_generated(SyntheticOpportunity.objects.get(opportunity_id=out["opportunity_id"]))


def test_a_clone_of_an_unmarked_opp_is_not_generated(user):
    SyntheticOpportunity.objects.create(
        opportunity_id=21_500, gdrive_folder_id="a-dump", labs_only=True, created_by=user
    )
    out = get_tool("synthetic_clone_to_labs_only").handler(user, source_opportunity_id=21_500)
    assert not is_generated(SyntheticOpportunity.objects.get(opportunity_id=out["opportunity_id"]))


def test_clone_to_labs_only_refuses_a_source_the_caller_cannot_see(partner):
    _other_tenants_opp(21_600)
    with pytest.raises(MCPToolError) as exc:
        get_tool("synthetic_clone_to_labs_only").handler(partner, source_opportunity_id=21_600)
    assert exc.value.code == "PERMISSION_DENIED"
    assert SyntheticOpportunity.objects.filter(gdrive_folder_id="theirs").count() == 1


def test_clone_to_labs_only_refuses_a_real_backed_source_without_connect_access(partner):
    """No Connect token means no confirmed membership, so the gate refuses."""
    SyntheticOpportunity.objects.create(opportunity_id=814, gdrive_folder_id="real-backed", labs_only=False)
    with pytest.raises(MCPToolError) as exc:
        get_tool("synthetic_clone_to_labs_only").handler(partner, source_opportunity_id=814)
    assert exc.value.code == "PERMISSION_DENIED"


def test_profile_from_prod_is_registered_as_the_request_facing_tool():
    """The decorator used to sit on the request-less inner function, whose signature
    has no ``user`` — so the tool raised TypeError on every call."""
    assert (
        get_tool("synthetic_profile_from_prod").handler is connect_labs.mcp.tools.synthetic.synthetic_profile_from_prod
    )


def test_generate_from_bundle_asks_the_authorizer_before_writing_over_an_existing_twin(user, tmp_path, settings):
    settings.LABS_SYNTHETIC_GDRIVE_PARENT_FOLDER_ID = "parent"
    SyntheticOpportunity.objects.create(
        opportunity_id=21_700, gdrive_folder_id="old", labs_only=True, cloned_from_opportunity_id=523
    )
    asked = []

    def refuse(opp_id):
        asked.append(opp_id)
        raise PermissionError("no")

    with pytest.raises(PermissionError):
        clone_from_prod.generate_opp_from_bundle(
            _bundle(tmp_path),
            drive=_FakeDrive(),
            program_id=21_800,
            program_name="P",
            org_name="O",
            fresh=True,
            authorize=refuse,
        )
    assert asked == [21_700]
    assert SyntheticOpportunity.objects.get(opportunity_id=21_700).gdrive_folder_id == "old"
