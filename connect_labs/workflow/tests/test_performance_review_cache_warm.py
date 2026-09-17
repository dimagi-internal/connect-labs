"""`performance_review`'s cache warm is schedulable per INSTANCE, not per template.

`performance_review` is a generic template with many live workflows built from
it. The four templates that already ship a `run_default`
(flw_daily_indicator_report, flw_daily_summary_report,
flw_daily_summary_report_rutf, program_audit_creator) each serve one report
family, so `supports_default_run` on the template is the whole answer for them.

Here it is not. Declaring the hook makes EVERY performance_review workflow look
schedulable, and the ones that never opted in would offer a Schedule button and
then fail with ValueError the first time it fired -- a broken schedule that
reads as healthy until its first run. Hence the config gate, and hence
`definition_supports_default_run`, which asks the definition rather than its
template.

The tests below pin that distinction in both directions, because getting it
wrong in either is silent: gate honoured when absent (every instance breaks),
or gate ignored when present (13005 never warms and every viewer keeps
recomputing ~163,000 visit rows on every load, which is what was actually
happening on 2026-09-17).
"""

import pytest

from connect_labs.workflow.templates import TEMPLATES, definition_supports_default_run, template_supports_default_run
from connect_labs.workflow.templates.performance_review import WARM_CACHE_CONFIG_KEY, run_default

TEMPLATE_KEY = "performance_review"


class FakeDefinition:
    """The two attributes `definition_supports_default_run` actually reads.

    Deliberately not a Django model: nothing under test touches the database,
    and the real failure mode is about which attribute is consulted, not about
    persistence.
    """

    def __init__(self, config=None, template_type=TEMPLATE_KEY, definition_id=13005):
        self.id = definition_id
        self.template_type = template_type
        self.data = {"config": dict(config or {})}


# --- fixture drift guards -------------------------------------------------
# Both assertions below would make the rest of this file vacuous if they
# stopped holding, so they are asserted rather than assumed.


def test_the_template_really_declares_the_gate():
    assert TEMPLATES[TEMPLATE_KEY].get("default_run_config_gate") == WARM_CACHE_CONFIG_KEY


def test_the_template_itself_reports_as_schedulable():
    """The template-level check stays True -- that is exactly why the
    definition-level one has to exist."""
    assert template_supports_default_run(TEMPLATE_KEY) is True


# --- the gate -------------------------------------------------------------


def test_an_instance_that_never_opted_in_is_not_schedulable():
    """The regression this design exists to prevent: every pre-existing
    performance_review workflow must be unaffected by the hook landing."""
    assert definition_supports_default_run(FakeDefinition(config={"multi_opp": True})) is False


def test_an_instance_that_opted_in_is_schedulable():
    definition = FakeDefinition(config={"multi_opp": True, WARM_CACHE_CONFIG_KEY: True})
    assert definition_supports_default_run(definition) is True


def test_a_falsy_gate_value_is_not_opting_in():
    """`config` is patched by hand via workflow_update_definition, so a key
    left at false must read as "not opted in" rather than "present"."""
    assert definition_supports_default_run(FakeDefinition(config={WARM_CACHE_CONFIG_KEY: False})) is False


def test_the_template_type_can_come_from_config_instead_of_the_attribute():
    """Older definitions carry templateType in `config` and leave the column
    empty; `definition_supports_default_run` reads both, so the gate must work
    through either route."""
    definition = FakeDefinition(config={"templateType": TEMPLATE_KEY, WARM_CACHE_CONFIG_KEY: True})
    definition.template_type = None
    assert definition_supports_default_run(definition) is True


def test_an_ungated_template_is_unchanged_by_all_this():
    """A template with no `default_run_config_gate` must stay schedulable
    without opting anything in -- the four that already ship a run_default must
    not start requiring a config key."""
    definition = FakeDefinition(config={}, template_type="program_audit_creator")
    assert definition_supports_default_run(definition) is True


def test_an_unknown_template_is_not_schedulable():
    assert definition_supports_default_run(FakeDefinition(template_type="does_not_exist")) is False


# --- the hook's own refusal ----------------------------------------------


def test_run_default_refuses_a_workflow_that_did_not_opt_in():
    """Belt and braces: the gate is enforced inside the hook too, so a
    schedule created before the gate existed -- or one whose config was edited
    afterwards -- fails loudly instead of warming caches nobody asked for."""
    with pytest.raises(ValueError) as excinfo:
        run_default(definition=FakeDefinition(config={}), access_token="t")
    assert WARM_CACHE_CONFIG_KEY in str(excinfo.value)


def test_run_default_names_the_workflow_it_refused():
    """The message is what a failed schedule shows in the admin, so it has to
    identify which workflow to go and fix."""
    with pytest.raises(ValueError) as excinfo:
        run_default(definition=FakeDefinition(config={}, definition_id=13005), access_token="t")
    assert "13005" in str(excinfo.value)
