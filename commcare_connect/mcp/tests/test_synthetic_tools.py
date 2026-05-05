"""Tests for the synthetic MCP tools (Phase 3, Plan A)."""

import pytest

import commcare_connect.mcp.tools.synthetic  # noqa: F401 — trigger @register side effects
from commcare_connect.labs.synthetic.models import SyntheticOpportunity
from commcare_connect.mcp.tool_registry import get_tool


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
    SyntheticOpportunity.objects.create(
        opportunity_id=4242, gdrive_folder_id="old", enabled=False
    )
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
    SyntheticOpportunity.objects.create(
        opportunity_id=4242, gdrive_folder_id="x", enabled=True
    )
    tool = get_tool("synthetic_disable")
    result = tool.handler(user=user, opportunity_id=4242)
    assert result["enabled"] is False
    row = SyntheticOpportunity.objects.get(opportunity_id=4242)
    assert row.enabled is False
    # folder retained
    assert row.gdrive_folder_id == "x"


@pytest.mark.django_db
def test_synthetic_disable_404s_on_missing_row(user):
    from commcare_connect.mcp.tool_registry import MCPToolError
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
        def create_folder(self, name, parent_id): return f"folder-{name}"
        def upload_file(self, fid, fname, content): return f"file-{fname}"

    monkeypatch.setattr(syn_tools, "DriveClient", lambda: _FakeDrive())
    monkeypatch.setattr(
        syn_tools, "_load_opportunity_detail",
        lambda opp_id, user: {"id": opp_id, "name": "X", "payment_units": [], "deliver_units": []},
    )
    monkeypatch.setattr(
        syn_tools, "_load_form_schema_for_opp",
        lambda opp_id, user: __import__(
            "commcare_connect.labs.synthetic.generator.schema_loader",
            fromlist=["FormSchema"],
        ).FormSchema(questions=[]),
    )

    from django.test import override_settings
    with override_settings(LABS_SYNTHETIC_GDRIVE_PARENT_FOLDER_ID="p"):
        tool = get_tool("synthetic_generate_from_manifest")
        result = tool.handler(user=user, opportunity_id=4242, manifest_yaml=manifest_yaml)

    assert result["folder_id"].startswith("folder-")
    assert "user_visits" in result["record_counts"]
    assert SyntheticOpportunity.objects.get(opportunity_id=4242).enabled is True


@pytest.mark.django_db
def test_synthetic_generate_rejects_invalid_manifest(user):
    from commcare_connect.mcp.tool_registry import MCPToolError
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
