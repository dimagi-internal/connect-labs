"""A template's render-contract config flags reach the instances stamped before them.

A definition's `config` is stamped at create-from-template time and never
migrates, so a flag added to a template later is inert on every workflow already
created from it -- the same failure `_INHERITED_SAFETY_FLAGS` was written for, one
level up. `noPipelineStream` is the case that bit: it records that the template's
render fetches its own rows, so an instance predating it streamed every pipeline
for all twelve of its opportunities on every load and discarded the result.
"""

from connect_labs.workflow.templates import _INHERITED_CONFIG_FLAGS, TEMPLATES, with_inherited_config_flags


def test_an_instance_stamped_before_the_flag_inherits_it():
    stale = {"name": "JJ - KMC Worker Review", "config": {"templateType": "kmc_flw_review", "multi_opp": True}}
    out = with_inherited_config_flags(stale, "kmc_flw_review")
    assert out["config"]["noPipelineStream"] is True, "the stale instance still streams the cohort"
    # Everything else it held is untouched.
    assert out["config"]["multi_opp"] is True
    assert out["name"] == "JJ - KMC Worker Review"


def test_it_does_not_mutate_the_record_it_was_handed():
    """Read-time resolution: the caller passes `definition.data` straight in."""
    stale = {"config": {"templateType": "kmc_flw_review"}}
    with_inherited_config_flags(stale, "kmc_flw_review")
    assert "noPipelineStream" not in stale["config"], "the definition data was mutated in place"


def test_an_explicit_opt_out_is_left_alone():
    """A deliberate `False` is a choice, not an absence."""
    opted_out = {"config": {"templateType": "kmc_flw_review", "noPipelineStream": False}}
    out = with_inherited_config_flags(opted_out, "kmc_flw_review")
    assert out["config"]["noPipelineStream"] is False


def test_a_template_that_declares_nothing_changes_nothing():
    """Only flags a template actually declares are inherited, so this cannot
    quietly alter the other twenty-odd templates."""
    data = {"config": {"templateType": "performance_review"}}
    assert with_inherited_config_flags(data, "performance_review") is data
    assert with_inherited_config_flags({"config": {}}, None) == {"config": {}}
    assert with_inherited_config_flags({"config": {}}, "no_such_template") == {"config": {}}


def test_it_tolerates_a_definition_with_no_config_at_all():
    out = with_inherited_config_flags({"name": "x"}, "kmc_flw_review")
    assert out["config"]["noPipelineStream"] is True


def test_every_inherited_flag_is_declared_by_at_least_one_template():
    """A flag no template declares is dead code that reads as a live guard."""
    declared = set()
    for tpl in TEMPLATES.values():
        declared |= set((tpl.get("definition") or {}).get("config") or {})
    for flag in _INHERITED_CONFIG_FLAGS:
        assert flag in declared, f"{flag!r} is inherited from nowhere"


def test_the_runner_context_resolves_the_flag():
    """The wiring, not just the helper: the runner reads the flag off
    `initialData.definition.config`, so the resolution has to happen where that
    payload is built or it never reaches the browser."""
    from pathlib import Path

    views = (Path(__file__).resolve().parents[1] / "views.py").read_text()
    assert (
        "with_inherited_config_flags(definition.data, definition.template_type)" in views
    ), "workflow_data still ships the raw definition data"
