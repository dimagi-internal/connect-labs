"""A template's config reaches the instances stamped before it.

A definition's `config` is stamped at create-from-template time and never
migrates, so a key added to a template later is inert on every workflow already
created from it -- the same failure `_INHERITED_SAFETY_FLAGS` was written for, one
level up. `noPipelineStream` is the case that bit: it records that the template's
render fetches its own rows, so an instance predating it streamed every pipeline
for all twelve of its opportunities on every load and discarded the result.
`renderWhileLoading` shipped two days later, to the same template, for the same
reason -- which is why the rule is now "the template owns its config" rather than
a named list of the flags someone has already been bitten by.
"""

import copy

from connect_labs.workflow.templates import TEMPLATES, with_inherited_config_flags

# A template whose config is EMPTY, so "declares nothing" has a real subject.
# Asserted below rather than assumed, because the identity guarantee this file
# pins is only meaningful if this template really declares no config at all.
NO_CONFIG_TEMPLATE = "ocs_outreach"


def test_the_no_config_template_really_declares_no_config():
    assert not ((TEMPLATES[NO_CONFIG_TEMPLATE].get("definition") or {}).get("config") or {})


def test_an_instance_stamped_before_the_flag_inherits_it():
    stale = {"name": "JJ - KMC Worker Review", "config": {"templateType": "kmc_flw_review", "multi_opp": True}}
    out = with_inherited_config_flags(stale, "kmc_flw_review")
    assert out["config"]["noPipelineStream"] is True, "the stale instance still streams the cohort"
    # Everything else it held is untouched.
    assert out["config"]["multi_opp"] is True
    assert out["name"] == "JJ - KMC Worker Review"


def test_it_inherits_every_key_the_template_declares_not_a_named_list():
    """The generalisation. `renderWhileLoading` was added to kmc_flw_review two
    days after `noPipelineStream`, for the same reason, and was not on the
    allow-list -- so an instance created in between got one flag and not the
    other. Asserted over the template's WHOLE config so a key added tomorrow is
    covered without editing this test."""
    declared = (TEMPLATES["kmc_flw_review"].get("definition") or {}).get("config") or {}
    assert "renderWhileLoading" in declared, "fixture drift: pick another late-added key"

    stale = {"config": {"templateType": "kmc_flw_review"}}
    out = with_inherited_config_flags(stale, "kmc_flw_review")
    for key, value in declared.items():
        if key == "templateType":
            continue
        assert out["config"][key] == value, f"{key!r} did not reach the stale instance"


def test_it_does_not_mutate_the_record_it_was_handed():
    """Read-time resolution: the caller passes `definition.data` straight in."""
    stale = {"config": {"templateType": "kmc_flw_review"}}
    with_inherited_config_flags(stale, "kmc_flw_review")
    assert stale == {"config": {"templateType": "kmc_flw_review"}}, "the definition data was mutated in place"


def test_it_does_not_hand_out_the_templates_own_config_objects():
    """A template's DEFINITION is module state shared by every request. An
    inherited value is now often a nested structure (`opp_meta`, `image_types`),
    so handing the live object to a page payload would let one edit reach every
    other workflow in the process."""
    template_config = (TEMPLATES["kmc_image_audit"].get("definition") or {}).get("config") or {}
    before = copy.deepcopy(template_config["opp_meta"])

    out = with_inherited_config_flags({"config": {}}, "kmc_image_audit")
    out["config"]["opp_meta"]["9999"] = {"llo": "INJECTED"}

    assert template_config["opp_meta"] == before, "the template registry was mutated through the returned payload"


def test_an_explicit_opt_out_is_left_alone():
    """A deliberate `False` is a choice, not an absence -- presence is the test,
    never truthiness, or every flag an instance turned off would come back on."""
    opted_out = {"config": {"templateType": "kmc_flw_review", "noPipelineStream": False}}
    out = with_inherited_config_flags(opted_out, "kmc_flw_review")
    assert out["config"]["noPipelineStream"] is False


def test_an_explicitly_set_value_wins_for_every_falsy_shape():
    """`False` is the one that bites, but `None`, `0`, `""` and `[]` are all
    values an instance can legitimately hold and all of them would be replaced
    by a `key not in config`-shaped check written as `config.get(key) is None`
    or `not config.get(key)`."""
    declared = (TEMPLATES["kmc_flw_review"].get("definition") or {}).get("config") or {}
    assert declared["audit_count_per_flw"] == 25 and declared["audit_enabled"] is True
    assert declared["scale_unverified_llos"], "fixture drift: expected a non-empty declared list"

    own = {
        "templateType": "kmc_flw_review",
        "audit_enabled": False,
        "audit_count_per_flw": 0,
        "weight_value_path": "",
        "scale_unverified_llos": [],
        "source_workflow_id": None,
    }
    out = with_inherited_config_flags({"config": dict(own)}, "kmc_flw_review")
    for key, value in own.items():
        assert out["config"][key] == value, f"{key!r} was overwritten by the template's value"


def test_a_template_that_declares_nothing_changes_nothing():
    """Identity, not equality: this helper runs on EVERY run-page load for
    EVERY template, so the no-op path must not copy the definition data."""
    data = {"config": {"templateType": NO_CONFIG_TEMPLATE}}
    assert with_inherited_config_flags(data, NO_CONFIG_TEMPLATE) is data
    assert with_inherited_config_flags({"config": {}}, None) == {"config": {}}
    assert with_inherited_config_flags({"config": {}}, "no_such_template") == {"config": {}}


def test_an_instance_that_already_has_every_key_is_returned_unchanged():
    """The normal case -- an instance created from the current template carries
    every key the template declares -- must also take the no-copy path."""
    declared = (TEMPLATES["performance_review"].get("definition") or {}).get("config") or {}
    assert declared, "fixture drift: performance_review declares no config"
    data = {"config": dict(declared)}
    assert with_inherited_config_flags(data, "performance_review") is data


def test_it_tolerates_a_definition_with_no_config_at_all():
    out = with_inherited_config_flags({"name": "x"}, "kmc_flw_review")
    assert out["config"]["noPipelineStream"] is True


def test_the_runner_context_resolves_the_flag():
    """The wiring, not just the helper: the runner reads the flag off
    `initialData.definition.config`, so the resolution has to happen where that
    payload is built or it never reaches the browser."""
    from pathlib import Path

    views = (Path(__file__).resolve().parents[1] / "views.py").read_text()
    assert (
        "with_inherited_config_flags(definition.data, definition.template_type)" in views
    ), "workflow_data still ships the raw definition data"
