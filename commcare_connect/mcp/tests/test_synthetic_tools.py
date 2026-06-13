"""Tests for the synthetic MCP tools (Phase 3, Plan A)."""

import pytest

import commcare_connect.mcp.tools.synthetic  # noqa: F401 — trigger @register side effects
from commcare_connect.labs.synthetic.models import SyntheticOpportunity
from commcare_connect.mcp.tool_registry import MCPToolError, get_tool


@pytest.fixture(autouse=True)
def _allow_opp_access(monkeypatch):
    """By default, every test sees all opps as accessible.

    Tests that exercise the permission denial path override this with
    monkeypatch.setattr(syn, "_require_opportunity_access", _raise_403).
    """
    from commcare_connect.mcp.tools import synthetic as syn

    monkeypatch.setattr(syn, "_require_opportunity_access", lambda user, opportunity_id: None)


@pytest.mark.django_db
def test_synthetic_register_creates_row(user):
    tool = get_tool("synthetic_register")
    result = tool.handler(
        user=user,
        opportunity_id=4242,
        gdrive_folder_id="folder-x",
        enabled=True,
        label="My Demo",
    )
    assert result["opportunity_id"] == 4242
    assert result["enabled"] is True
    row = SyntheticOpportunity.objects.get(opportunity_id=4242)
    assert row.gdrive_folder_id == "folder-x"
    assert row.label == "My Demo"


@pytest.mark.django_db
def test_synthetic_register_updates_existing_row(user):
    SyntheticOpportunity.objects.create(opportunity_id=4242, gdrive_folder_id="old", enabled=False)
    tool = get_tool("synthetic_register")
    tool.handler(
        user=user,
        opportunity_id=4242,
        gdrive_folder_id="new",
        enabled=True,
        label=None,
    )
    row = SyntheticOpportunity.objects.get(opportunity_id=4242)
    assert row.gdrive_folder_id == "new"
    assert row.enabled is True


@pytest.mark.django_db
def test_synthetic_disable_clears_enabled_flag(user):
    SyntheticOpportunity.objects.create(opportunity_id=4242, gdrive_folder_id="x", enabled=True)
    tool = get_tool("synthetic_disable")
    result = tool.handler(user=user, opportunity_id=4242)
    assert result["enabled"] is False
    row = SyntheticOpportunity.objects.get(opportunity_id=4242)
    assert row.enabled is False
    # folder retained
    assert row.gdrive_folder_id == "x"


@pytest.mark.django_db
def test_synthetic_disable_404s_on_missing_row(user):
    tool = get_tool("synthetic_disable")
    with pytest.raises(MCPToolError) as exc:
        tool.handler(user=user, opportunity_id=99999)
    assert exc.value.code == "NOT_FOUND"


@pytest.mark.django_db
def test_synthetic_generate_from_manifest_creates_folder_and_row(user, monkeypatch):
    """Tool wires manifest -> engine -> uploader and returns folder_id + counts."""
    from commcare_connect.mcp.tools import synthetic as syn_tools

    manifest_yaml = (
        "opportunity_id: 4242\n"
        "opportunity_name: Demo\n"
        "random_seed: 7\n"
        "timeline:\n"
        "  start_date: 2026-02-01\n"
        "  end_date: 2026-02-14\n"
        "  weeks: 2\n"
        "  visit_cadence_per_week_per_flw: { mean: 2, stddev: 0 }\n"
        "flw_personas:\n"
        "  - id: a\n"
        "    archetype: steady\n"
        "    accuracy_distribution: { mean: 0.9, stddev: 0 }\n"
        "    completeness_distribution: { mean: 0.95, stddev: 0 }\n"
        "    flag_rate: 0\n"
        "beneficiary_cohorts:\n"
        "  - id: primary\n"
        "    size: 5\n"
        "    field_distributions: {}\n"
        "    progression: flat\n"
        "anomalies: []\n"
        "kpi_config:\n"
        "  - kpi: accuracy\n"
        "    field_path: form.weight_kg\n"
        "    aggregation: validated_rate\n"
        "    threshold_underperform: 0.75\n"
        "coaching_arcs: []\n"
    )

    class _FakeDrive:
        def create_folder(self, name, parent_id):
            return f"folder-{name}"

        def upload_file(self, fid, fname, content):
            return f"file-{fname}"

    monkeypatch.setattr(syn_tools, "DriveClient", lambda: _FakeDrive())
    monkeypatch.setattr(
        syn_tools,
        "_load_opportunity_detail",
        lambda opp_id, user: {"id": opp_id, "name": "X", "payment_units": [], "deliver_units": []},
    )
    monkeypatch.setattr(
        syn_tools,
        "_load_form_schema_for_opp",
        lambda opp_id, user: __import__(
            "commcare_connect.labs.synthetic.generator.fixtures.schema_loader",
            fromlist=["FormSchema"],
        ).FormSchema(questions=[]),
    )

    from django.test import override_settings

    with override_settings(LABS_SYNTHETIC_GDRIVE_PARENT_FOLDER_ID="p"):
        tool = get_tool("synthetic_generate_from_manifest")
        result = tool.handler(user=user, opportunity_id=4242, manifest_yaml=manifest_yaml)

    assert result["folder_id"].startswith("folder-")
    assert result["folder_url"] == f"https://drive.google.com/drive/folders/{result['folder_id']}"
    assert "user_visits" in result["record_counts"]
    assert SyntheticOpportunity.objects.get(opportunity_id=4242).enabled is True


@pytest.mark.django_db
def test_synthetic_generate_rejects_invalid_manifest(user):
    tool = get_tool("synthetic_generate_from_manifest")
    with pytest.raises(MCPToolError) as exc:
        tool.handler(user=user, opportunity_id=1, manifest_yaml="not: valid: yaml: at all: :")
    assert exc.value.code == "INVALID_SCHEMA"


def test_all_phase6_tools_are_registered():
    """All five tools added in Phase 6 are present in the registry by name."""
    from commcare_connect.mcp.tool_registry import list_tools

    names = {t["name"] for t in list_tools()}
    expected = {
        "synthetic_register",
        "synthetic_disable",
        "synthetic_generate_from_manifest",
        "task_create_synthetic",
        "workflow_save_snapshot",
    }
    assert expected.issubset(names), f"missing tools: {expected - names}"


# -----------------------------------------------------------------------------
# Permission-denial tests (Plan A C3 fix)
# -----------------------------------------------------------------------------


def _raise_403(user, opportunity_id):
    raise MCPToolError("PERMISSION_DENIED", "stubbed")


@pytest.mark.django_db
def test_synthetic_register_denies_inaccessible_opp(user, monkeypatch):
    from commcare_connect.mcp.tools import synthetic as syn

    monkeypatch.setattr(syn, "_require_opportunity_access", _raise_403)
    tool = get_tool("synthetic_register")
    with pytest.raises(MCPToolError) as exc:
        tool.handler(user=user, opportunity_id=9999, gdrive_folder_id="f")
    assert exc.value.code == "PERMISSION_DENIED"
    # The DB row is never created when access is denied.
    assert not SyntheticOpportunity.objects.filter(opportunity_id=9999).exists()


@pytest.mark.django_db
def test_synthetic_disable_denies_inaccessible_opp(user, monkeypatch):
    from commcare_connect.mcp.tools import synthetic as syn

    SyntheticOpportunity.objects.create(opportunity_id=9999, gdrive_folder_id="x", enabled=True)
    monkeypatch.setattr(syn, "_require_opportunity_access", _raise_403)
    tool = get_tool("synthetic_disable")
    with pytest.raises(MCPToolError) as exc:
        tool.handler(user=user, opportunity_id=9999)
    assert exc.value.code == "PERMISSION_DENIED"
    # The row's enabled flag is unchanged.
    assert SyntheticOpportunity.objects.get(opportunity_id=9999).enabled is True


@pytest.mark.django_db
def test_synthetic_generate_denies_inaccessible_opp(user, monkeypatch):
    from commcare_connect.mcp.tools import synthetic as syn

    monkeypatch.setattr(syn, "_require_opportunity_access", _raise_403)
    tool = get_tool("synthetic_generate_from_manifest")
    with pytest.raises(MCPToolError) as exc:
        tool.handler(user=user, opportunity_id=9999, manifest_yaml="opportunity_id: 9999\n")
    assert exc.value.code == "PERMISSION_DENIED"


@pytest.mark.django_db
def test_accessible_opp_ids_uses_user_token(user, monkeypatch):
    """The User-callable helper hits fetch_user_organization_data with the user's token."""
    from commcare_connect.mcp.tools import synthetic as syn

    captured = {}

    def _fake_token(u):
        captured["user"] = u
        return "tok-abc"

    def _fake_fetch(token):
        captured["token"] = token
        return {"opportunities": [{"id": 1}, {"id": 2}, {"id": 3}]}

    # Patch the symbols in the module where they're used.
    monkeypatch.setattr(syn, "require_connect_token", _fake_token)
    # fetch_user_organization_data is imported lazily inside the helper.
    import commcare_connect.labs.integrations.connect.oauth as oauth_mod

    monkeypatch.setattr(oauth_mod, "fetch_user_organization_data", _fake_fetch)

    accessible = syn._accessible_opp_ids_for_user(user)
    assert accessible == {1, 2, 3}
    assert captured["token"] == "tok-abc"
    assert captured["user"] is user


@pytest.mark.django_db
def test_accessible_opp_ids_empty_set_when_no_token(user, monkeypatch):
    """Helper returns empty set when require_connect_token raises."""
    from commcare_connect.mcp.tools import synthetic as syn

    def _raise(u):
        raise MCPToolError("PERMISSION_DENIED", "no token")

    monkeypatch.setattr(syn, "require_connect_token", _raise)
    assert syn._accessible_opp_ids_for_user(user) == set()


# -----------------------------------------------------------------------------
# Labs-only synthetic tools (clone + create)
# -----------------------------------------------------------------------------


@pytest.mark.django_db
def test_synthetic_create_labs_only_allocates_opp_id(user):
    """Creating a labs-only opp auto-allocates from the reserved 10_000+ range."""
    from commcare_connect.labs.synthetic.models import LABS_ONLY_OPP_ID_FLOOR

    tool = get_tool("synthetic_create_labs_only")
    result = tool.handler(
        user=user,
        label="CHC demo",
        gdrive_folder_id="folder-abc",
        org_name="Acme",
        program_name="AcmeProg",
        allowed_domains=["@dimagi.com"],
    )
    assert result["opportunity_id"] == LABS_ONLY_OPP_ID_FLOOR
    assert result["labs_only"] is True
    assert result["allowed_domains"] == ["@dimagi.com"]
    row = SyntheticOpportunity.objects.get(opportunity_id=result["opportunity_id"])
    assert row.labs_only is True
    assert row.gdrive_folder_id == "folder-abc"


@pytest.mark.django_db
def test_synthetic_create_labs_only_defaults_allowed_domains(user):
    """Default allowed_domains is ['@dimagi.com'] when not specified."""
    tool = get_tool("synthetic_create_labs_only")
    result = tool.handler(
        user=user,
        label="X",
        gdrive_folder_id="folder-x",
    )
    assert result["allowed_domains"] == ["@dimagi.com"]


@pytest.mark.django_db
def test_synthetic_clone_to_labs_only_reuses_gdrive_folder(user):
    """Cloning a real-backed opp creates a labs-only opp sharing the gdrive_folder_id."""
    source = SyntheticOpportunity.objects.create(
        opportunity_id=814,
        gdrive_folder_id="folder-814",
        label="Source",
        labs_only=False,
    )

    tool = get_tool("synthetic_clone_to_labs_only")
    result = tool.handler(user=user, source_opportunity_id=814)

    assert result["source_opportunity_id"] == 814
    assert result["opportunity_id"] >= 10_000
    assert result["gdrive_folder_id"] == "folder-814"
    assert result["labs_only"] is True
    # Default broad allowlist so ace@dimagi-ai.com can see clones.
    assert "@dimagi.com" in result["allowed_domains"]
    assert "@dimagi-ai.com" in result["allowed_domains"]

    new_row = SyntheticOpportunity.objects.get(opportunity_id=result["opportunity_id"])
    assert new_row.labs_only is True
    assert new_row.gdrive_folder_id == source.gdrive_folder_id


@pytest.mark.django_db
def test_synthetic_clone_to_labs_only_404s_on_missing_source(user):
    tool = get_tool("synthetic_clone_to_labs_only")
    with pytest.raises(MCPToolError) as exc:
        tool.handler(user=user, source_opportunity_id=999_999)
    assert exc.value.code == "NOT_FOUND"


@pytest.mark.django_db
def test_synthetic_clone_to_labs_only_does_not_require_source_connect_access(user, monkeypatch):
    """Cloning any registered SyntheticOpportunity is allowed for any MCP caller.

    Once an opp is in the SyntheticOpportunity registry it's already a
    labs-controlled fixture. Cloning grants no new data access — just a
    second view onto the same GDrive folder. The previous Connect-access
    check defeated the "make this easy to do again" goal for users who
    lack Connect membership on the source (e.g. ACE).
    """
    SyntheticOpportunity.objects.create(opportunity_id=814, gdrive_folder_id="folder-814", labs_only=False)

    # Even when Connect access check would deny, clone still succeeds.
    from commcare_connect.mcp.tools import synthetic as syn

    monkeypatch.setattr(syn, "_require_opportunity_access", _raise_403)

    tool = get_tool("synthetic_clone_to_labs_only")
    result = tool.handler(user=user, source_opportunity_id=814)
    assert result["labs_only"] is True
    assert result["gdrive_folder_id"] == "folder-814"


@pytest.mark.django_db
def test_synthetic_clone_to_labs_only_works_on_invisible_labs_only_source(user):
    """A labs-only source the caller can't see directly is still cloneable.

    Same rationale: it's already a labs-controlled fixture. allowed_domains
    on the source controls who sees the SOURCE in labs_context, not who
    can clone it.
    """
    user.email = "bob@external.com"
    user.view_synthetic_opps = False
    user.save()

    SyntheticOpportunity.objects.create(
        opportunity_id=10_000,
        gdrive_folder_id="folder-src",
        labs_only=True,
        allowed_domains=["@dimagi.com"],
    )

    tool = get_tool("synthetic_clone_to_labs_only")
    result = tool.handler(user=user, source_opportunity_id=10_000)
    assert result["labs_only"] is True
    assert result["gdrive_folder_id"] == "folder-src"


# -----------------------------------------------------------------------------
# synthetic_set_my_visibility
# -----------------------------------------------------------------------------


@pytest.mark.django_db
def test_synthetic_set_my_visibility_flips_user_flag(user):
    user.view_synthetic_opps = False
    user.save()

    tool = get_tool("synthetic_set_my_visibility")
    result = tool.handler(user=user, enabled=True)

    assert result["view_synthetic_opps"] is True
    user.refresh_from_db()
    assert user.view_synthetic_opps is True

    result = tool.handler(user=user, enabled=False)
    assert result["view_synthetic_opps"] is False
    user.refresh_from_db()
    assert user.view_synthetic_opps is False
