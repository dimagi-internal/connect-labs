"""The tab row is grouped (Buying / Distribution and stock / Setup), and each tab's own page says what it is for.

THIS REPOSITORY IS PUBLIC. Every name here is invented.
"""

import html as html_lib

import pytest
from django.template import Context, Template

from connect_labs.supply_chain.navigation import (
    GLOSSARY,
    GROUP_OF_TAB,
    PAGE_PURPOSE,
    SUPPLY_TABS,
    TAB_GROUPS,
    page_purpose,
    tab_groups,
)
from connect_labs.supply_chain.tests import test_workflow_views as pinned

client_in_programme = pinned.client_in_programme
da = pinned.da

TAB_NAMES = [name for name, _ in SUPPLY_TABS]


def test_every_tab_is_in_exactly_one_group():
    assert len(TAB_NAMES) == len(set(TAB_NAMES))
    assert set(GROUP_OF_TAB) == set(TAB_NAMES)


def test_the_groups_are_the_ones_the_feedback_asked_for():
    assert [g for g, _ in TAB_GROUPS] == ["", "Buying", "Distribution and stock", "Setup"]
    by_group = {g: [label for _, label in tabs] for g, tabs in TAB_GROUPS}
    assert by_group[""] == ["Overview"]
    assert by_group["Buying"] == ["Sourcing", "Orders", "Suppliers"]
    assert {"Catalogue", "Organisations", "Alerts", "Team"} <= set(by_group["Setup"])
    assert {"Network", "Map", "Distribution", "Stock", "Where it went"} <= set(by_group["Distribution and stock"])


def test_every_tab_says_what_it_is_for():
    assert not sorted(set(TAB_NAMES) - set(PAGE_PURPOSE)), "a tab with no one-line purpose"
    assert not sorted(set(PAGE_PURPOSE) - set(TAB_NAMES))


def test_grouping_keeps_the_order_and_puts_a_pinned_tab_with_its_neighbour():
    tabs = [
        {"label": "A", "group": ""},
        {"label": "B", "group": "Buying"},
        {"label": "Pinned", "group": "Buying"},
        {"label": "C", "group": "Setup"},
    ]
    assert [(g["label"], [t["label"] for t in g["tabs"]]) for g in tab_groups(tabs)] == [
        ("", ["A"]),
        ("Buying", ["B", "Pinned"]),
        ("Setup", ["C"]),
    ]


def test_only_the_page_in_view_supplies_the_purpose_line():
    assert page_purpose([{"label": "A"}, {"label": "B", "purpose": "What B is for."}]) == "What B is for."
    assert page_purpose([{"label": "A"}]) == ""


def test_a_term_explains_itself_on_hover_and_an_unknown_word_is_left_plain():
    html = Template('{% load supply_chain_extras %}{% term "trade item" %}|{% term "nonsense" %}').render(Context())
    assert html_lib.unescape(html).count('title="' + GLOSSARY["trade item"]) == 1
    assert html.endswith("|nonsense")


@pytest.mark.django_db
def test_the_header_draws_each_group_under_its_name(client_in_programme):
    from django.urls import reverse

    body = client_in_programme.get(reverse("supply_chain:catalogue")).content.decode()
    assert body.count('data-testid="supply-tab-group"') == len(TAB_GROUPS)
    assert 'data-group="Buying"' in body and 'data-group="Distribution and stock"' in body
    assert 'data-testid="page-purpose"' in body and "What you buy." in body


@pytest.mark.django_db
def test_a_record_below_a_tab_does_not_repeat_the_purpose_line(client_in_programme):
    from django.urls import reverse

    body = client_in_programme.get(reverse("supply_chain:product_create")).content.decode()
    assert 'data-testid="page-purpose"' not in body
