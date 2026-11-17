"""Related tabs share one menu entry (navigation.FAMILIES), and a page keeps its explanations behind an (i).

THIS REPOSITORY IS PUBLIC. Placeholder names only.
"""

from django.template import Context, Template

from connect_labs.supply_chain.navigation import FAMILIES, FAMILY_OF, SUPPLY_TABS, TAB_GROUPS, rail_entries


def _tab(name, active=False):
    return {"url": f"/{name}/", "label": name, "active": active, "family": FAMILY_OF.get(name, "")}


def test_every_family_member_is_a_real_tab_of_one_group_and_sits_together():
    from connect_labs.supply_chain.navigation import COMPACT_ORDER, _compact_order

    names = [name for name, _ in _compact_order(SUPPLY_TABS, {n: i for i, n in enumerate(COMPACT_ORDER)})]
    assert sorted(names) == sorted(name for name, _ in SUPPLY_TABS)
    group_of = {name: group for group, tabs in TAB_GROUPS for name, _ in tabs}
    for family, members in FAMILIES:
        assert all(m in names for m in members), family
        assert len({group_of[m] for m in members}) == 1, f"{family} spans groups"
        at = [names.index(m) for m in members]
        assert at == list(range(at[0], at[0] + len(members))), f"{family} is not together in the compact menu"


def test_a_family_folds_its_tabs_and_is_open_where_you_are():
    tabs = [
        _tab("supply_chain:distribution"),
        _tab("supply_chain:stock", active=True),
        _tab("supply_chain:flow"),
        _tab("supply_chain:issues"),
        _tab("supply_chain:tests"),
    ]
    entries = rail_entries(tabs)
    assert [(e["family"], [t["label"] for t in e["tabs"]], e["active"]) for e in entries] == [
        ("Distribution", ["supply_chain:distribution", "supply_chain:stock", "supply_chain:flow"], True),
        ("", ["supply_chain:issues"], False),
        ("", ["supply_chain:tests"], False),
    ]


def test_issues_and_tests_stay_entries_of_their_own():
    assert "supply_chain:issues" not in FAMILY_OF and "supply_chain:tests" not in FAMILY_OF


def test_a_family_showing_one_page_is_that_pages_own_entry():
    entries = rail_entries([_tab("supply_chain:workers")])  # Plan turned off
    assert [(e["family"], e["tabs"], e["active"]) for e in entries] == [("", [_tab("supply_chain:workers")], False)]


def test_a_family_entry_wears_its_icon_and_adds_up_what_needs_attention():
    from connect_labs.supply_chain.navigation import family_tabs

    tabs = [{**_tab("supply_chain:distribution"), "badge": 2}, {**_tab("supply_chain:stock", active=True), "badge": 1}]
    (entry,) = rail_entries(tabs)
    assert entry["icon"] == "fa-truck" and entry["badge"] == 3
    assert [t["label"] for t in family_tabs(tabs)] == ["supply_chain:distribution", "supply_chain:stock"]
    assert family_tabs([_tab("supply_chain:issues", active=True)]) == []


def test_a_pinned_tab_with_no_family_stands_alone_beside_one():
    pin = {"url": "/views/desk/", "label": "Supply desk", "active": False, "family": ""}
    entries = rail_entries([_tab("supply_chain:stock"), _tab("supply_chain:flow"), pin])
    assert [e["family"] for e in entries] == ["Distribution", ""]


def test_the_info_tag_keeps_its_text_behind_an_i():
    html = Template(
        '{% load supply_chain_extras %}{% info "About the plan" %}<p>How it works.</p>{% endinfo %}'
    ).render(Context())
    assert 'data-testid="supply-info"' in html and 'aria-label="About the plan"' in html
    assert "fa-circle-info" in html and "<p>How it works.</p>" in html
    assert Template("{% load supply_chain_extras %}{% info %} {% endinfo %}").render(Context()) == ""


# ---- only a programme that chose it -----------------------------------------------------------------


def test_the_compact_layout_is_a_programmes_own_choice_and_off_unless_chosen():
    from connect_labs.supply_chain.config import SUPPLY

    assert "compact_layout" not in SUPPLY.defaults
    assert SUPPLY.schema["properties"]["compact_layout"] == {"type": "boolean"}


def test_a_programme_without_it_keeps_the_usual_menu_and_pages(rf, monkeypatch):
    from connect_labs.supply_chain import compact_templates, navigation

    request = rf.get("/supply/stock/")
    request.labs_context = {}
    request._supply_compact = False
    request._supply_tab_config = {"supply_chain:tests": {"hidden": True}}
    request._supply_pins = []
    request.resolver_match = type("M", (), {"view_name": "supply_chain:stock", "kwargs": {}})()
    tabs = navigation.supply_tabs(request)
    assert [t["label"] for t in tabs] == [label for name, label in SUPPLY_TABS if name != "supply_chain:tests"]
    assert not any(t["family"] or t["icon"] or t.get("badge") for t in tabs)
    assert "Setup" in {t["group"] for t in tabs}

    response = type("R", (), {"template_name": "supply_chain/stock.html"})()
    assert compact_templates.CompactLayoutMiddleware(None).process_template_response(request, response) is response
    assert response.template_name == "supply_chain/stock.html"


def test_a_programme_with_it_gets_the_families_and_the_compact_pages(rf):
    from connect_labs.supply_chain import compact_templates, navigation

    request = rf.get("/supply/stock/")
    request.labs_context = {}
    request._supply_compact = True
    request._supply_badges = {}
    request._supply_tab_config = {"supply_chain:tests": {"hidden": True}}
    request._supply_pins = []
    request.resolver_match = type("M", (), {"view_name": "supply_chain:stock", "kwargs": {}})()
    tabs = navigation.supply_tabs(request)
    by_label = {t["label"]: t for t in tabs}
    assert by_label["Stock"]["family"] == "Distribution" and by_label["Stock"]["icon"]
    assert "Settings" in {t["group"] for t in tabs} and "Setup" not in {t["group"] for t in tabs}
    assert [t["label"] for t in navigation.family_tabs(tabs)] == ["Distribution", "Stock", "Where it went"]

    response = type("R", (), {"template_name": ["supply_chain/stock.html"]})()
    compact_templates.CompactLayoutMiddleware(None).process_template_response(request, response)
    assert response.template_name == ["supply_chain/compact/stock.html"]
    response = type("R", (), {"template_name": "supply_chain/start_here.html"})()  # a guide page: no compact copy
    compact_templates.CompactLayoutMiddleware(None).process_template_response(request, response)
    assert response.template_name == "supply_chain/start_here.html"


def test_every_compact_page_includes_and_extends_templates_that_exist():
    import re
    from pathlib import Path

    from django.template.loader import get_template

    root = Path(__file__).resolve().parents[2] / "templates" / "supply_chain" / "compact"
    pages = list(root.rglob("*.html"))
    assert pages
    for page in pages:
        get_template(f"supply_chain/compact/{page.relative_to(root).as_posix()}")
        for name in re.findall(r"""{%\s*(?:include|extends)\s+["']([^"']+)["']""", page.read_text(encoding="utf-8")):
            get_template(name)


def test_view_as_of_has_a_calendar_in_the_compact_layout_only(rf):
    from django.template.loader import render_to_string

    for compact in (True, False):
        request = rf.get("/supply/stock/")
        request._supply_compact = compact
        html = render_to_string(
            "supply_chain/_as_of_bar.html",
            {"supply_as_of_available": True, "as_of_part": "form", "request": request},
        )
        assert 'name="as_of"' in html
        assert ('data-testid="as-of-calendar"' in html) is compact
        assert ('type="date"' in html) is compact
