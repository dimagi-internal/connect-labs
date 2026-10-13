"""The screens a seeded clone is seen through (demo/clone_pages.py): Supply tabs and home pages."""

import pytest

from connect_labs.labs.synthetic import registry
from connect_labs.labs.synthetic.models import SyntheticOpportunity
from connect_labs.scope_config.models import ScopeConfig
from connect_labs.supply_chain.demo import clone_pages
from connect_labs.workflow.page_mode import is_page
from connect_labs.workflow.supply_sources import declaration_problems
from connect_labs.workflow.templates import TEMPLATES


@pytest.fixture
def clone(db):
    opp = SyntheticOpportunity.objects.create(
        opportunity_id=SyntheticOpportunity.next_labs_only_opp_id(),
        labs_only=True,
        enabled=True,
        label="Clone of the RUTF opportunity (test)",
        org_name="Test org",
        program_name="Test program",
        gdrive_folder_id="",
    )
    registry.invalidate_cache()
    yield opp
    registry.invalidate_cache()


def _layer(namespace, scope_type, key):
    row = ScopeConfig.objects.filter(scope_type=scope_type, scope_key=str(key), namespace=namespace).first()
    return row.data if row else {}


def _definition(workflow_id, **scope):
    with clone_pages._access(**scope) as access:
        return access.get_definition(workflow_id)


def test_the_tabs_and_the_program_home_are_made_once_and_follow_their_templates(clone):
    opp = clone.opportunity_id
    first = clone_pages.ensure_pages(program_id=opp, opportunity_id=opp)
    again = clone_pages.ensure_pages(program_id=opp, opportunity_id=opp)

    # A reseed keeps every id the Settings and anyone's bookmarks point at.
    assert {k: v["workflow"] for k, v in again["tabs"].items()} == {k: v["workflow"] for k, v in first["tabs"].items()}
    assert again["program_home"]["workflow"] == first["program_home"]["workflow"]
    assert {v["action"] for v in first["tabs"].values()} == {"created"}
    assert {v["action"] for v in again["tabs"].values()} == {"refreshed"}

    tabs = _layer("supply", "program", opp)["tabs"]
    assert tabs["supply_chain:workers"]["slug"] == "stock-review" and tabs["forecast"]["slug"] == "forecast"
    for key, template in (("supply_chain:workers", "supply_stock_review"), ("forecast", "supply_stock_forecast")):
        fill = tabs[key]["fill"]
        assert fill == {"workflow": first["tabs"][key]["workflow"], "opportunity_id": opp}
        definition = _definition(fill["workflow"], opportunity_id=opp)
        assert definition.data["render_source"] == {"template": template}
        assert definition.opportunity_ids == [opp]
    review = _definition(tabs["supply_chain:workers"]["fill"]["workflow"], opportunity_id=opp)
    assert review.data["config"]["course_size"] == 168  # the protocol's course, as the forecast reads it

    home = _layer("labs", "program", opp)["home"]["fill"]
    assert home == {"workflow": first["program_home"]["workflow"], "program_id": opp}
    page = _definition(home["workflow"], program_id=opp)
    assert is_page(page) and page.data["render_source"] == {"template": "supply_program_home"}
    assert page.data["config"]["agent"] == {"share": True}
    assert page.opportunity_ids == [opp]
    assert first["program_home"]["url"] == f"/labs/p/programme/{opp}/"


def test_a_forked_or_hidden_tab_is_put_back(clone):
    opp = clone.opportunity_id
    made = clone_pages.ensure_pages(program_id=opp, opportunity_id=opp)
    review_id = made["tabs"]["supply_chain:workers"]["workflow"]
    with clone_pages._access(opportunity_id=opp) as access:
        definition = access.get_definition(review_id)
        access.update_definition(review_id, {**definition.data, "render_source": None})
    from connect_labs.scope_config.scopes import Scope

    clone_pages._set("supply", Scope.of("program", opp), {"tabs": {"forecast": {"hidden": True}}})

    clone_pages.ensure_pages(program_id=opp, opportunity_id=opp)
    assert _definition(review_id, opportunity_id=opp).data["render_source"] == {"template": "supply_stock_review"}
    assert "hidden" not in _layer("supply", "program", opp)["tabs"]["forecast"]


def test_the_organisation_gets_a_home_only_when_it_has_none(clone):
    opp = clone.opportunity_id
    first = clone_pages.ensure_pages(program_id=opp, opportunity_id=opp)
    assert first["organization_home"]["action"] == "created"
    slug = clone.labs_org.slug
    fill = _layer("labs", "organization", slug)["home"]["fill"]
    assert fill == {"workflow": first["organization_home"]["workflow"], "program_id": opp}
    assert is_page(_definition(fill["workflow"], program_id=opp))
    assert first["organization_home"]["url"] == f"/labs/p/org/{slug}/"

    again = clone_pages.ensure_pages(program_id=opp, opportunity_id=opp)
    assert again["organization_home"] == {
        "workflow": fill["workflow"],
        "action": "kept",
        "url": f"/labs/p/org/{slug}/",
    }


def test_a_real_program_is_refused(db):
    with pytest.raises(ValueError, match="refusing"):
        clone_pages.ensure_pages(program_id=263, opportunity_id=2230)


def test_the_program_home_is_a_page_reading_only_declared_supply_sources():
    template = TEMPLATES["supply_program_home"]
    assert template["kind"] == "page" and template["follow_template"] is True
    assert declaration_problems(template["definition"]["supply_sources"]) == []
    assert template["definition"]["page"]["slug"] != "home"  # the organisation's page_blank home is "home"
    # Off unless an instance turns it on (workflow/agent_sharing.py): the seeded demo's does.
    assert "agent" not in template["definition"]["config"]
    # What the page says is "program"; `programme` is only the forecast's own data key.
    assert "programme" not in template["render_code"].replace("forecast.programme", "")
