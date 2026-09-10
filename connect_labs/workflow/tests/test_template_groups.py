"""The template picker's groups: every template placed, every placement real.

The picker sections templates by what they produce, from ONE map
(TEMPLATE_GROUP_OF). These make the map complete and current: a new template
cannot fall into "other" unnoticed, and a renamed or deleted template cannot
leave a stale key behind that quietly places nothing.
"""

from connect_labs.workflow.templates import (
    TEMPLATE_GROUP_OF,
    TEMPLATE_GROUPS,
    TEMPLATES,
    list_templates,
    template_groups,
)


def _registered():
    return {k for k, t in TEMPLATES.items() if not t.get("deprecated")}


def test_every_registered_template_is_placed_in_a_group():
    missing = sorted(_registered() - set(TEMPLATE_GROUP_OF))
    assert not missing, f"add these to TEMPLATE_GROUP_OF in workflow/templates/__init__.py: {missing}"


def test_no_stale_keys_in_the_map():
    stale = sorted(set(TEMPLATE_GROUP_OF) - set(TEMPLATES))
    assert not stale, f"TEMPLATE_GROUP_OF names templates that no longer exist: {stale}"


def test_every_placement_is_a_known_group():
    known = {g["key"] for g in TEMPLATE_GROUPS}
    bad = {k: g for k, g in TEMPLATE_GROUP_OF.items() if g not in known}
    assert not bad, f"unknown group keys: {bad}"


def test_groups_are_distinct_and_labelled():
    keys = [g["key"] for g in TEMPLATE_GROUPS]
    assert len(keys) == len(set(keys))
    assert all(g["label"] and g["blurb"] for g in TEMPLATE_GROUPS)
    # Copies: reordering what template_groups() returns must not reorder the registry.
    template_groups().reverse()
    assert [g["key"] for g in TEMPLATE_GROUPS] == keys


def test_list_templates_carries_group_and_companion_of():
    rows = {t["key"]: t for t in list_templates()}
    assert rows["kmc_programme_metrics"]["group"] == "reports"
    assert rows["kmc_programme_metrics"]["companion_of"] == []
    # The worker review is created WITH the programme report, so the picker
    # shows it as a tag on that row rather than a card of its own.
    assert rows["kmc_flw_review"]["companion_of"] == ["kmc_programme_metrics"]
    assert all(r["group"] in {g["key"] for g in TEMPLATE_GROUPS} for r in rows.values())


def test_the_picker_template_compiles_and_reads_what_the_api_sends():
    """The modal is Django + Alpine markup nothing else executes in CI. Compiling
    it catches a broken tag; the string checks pin it to the API's field names
    (`groups`, `group`, `companion_of`) so a rename on one side cannot leave the
    picker silently empty."""
    from django.template.loader import get_template

    template = get_template("workflow/list.html")
    assert template.template.nodelist
    src = template.template.source
    for needle in ("data.groups", "t.group", "companion_of", "templateFilter", "visibleGroups()"):
        assert needle in src, needle
