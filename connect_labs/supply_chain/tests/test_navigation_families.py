"""Related tabs share one menu entry (navigation.FAMILIES), and a page keeps its explanations behind an (i).

THIS REPOSITORY IS PUBLIC. Placeholder names only.
"""

from django.template import Context, Template

from connect_labs.supply_chain.navigation import FAMILIES, FAMILY_OF, SUPPLY_TABS, TAB_GROUPS, rail_entries


def _tab(name, active=False):
    return {"url": f"/{name}/", "label": name, "active": active, "family": FAMILY_OF.get(name, "")}


def test_every_family_member_is_a_real_tab_of_one_group_and_sits_together():
    names = [name for name, _ in SUPPLY_TABS]
    group_of = {name: group for group, tabs in TAB_GROUPS for name, _ in tabs}
    for family, members in FAMILIES:
        assert all(m in names for m in members), family
        assert len({group_of[m] for m in members}) == 1, f"{family} spans groups"
        at = [names.index(m) for m in members]
        assert at == list(range(at[0], at[0] + len(members))), f"{family} is not contiguous in SUPPLY_TABS"


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
