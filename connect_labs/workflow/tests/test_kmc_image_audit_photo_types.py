"""Auditing more than the weight photo.

KMC visits carry three photos worth auditing: the weight photo the scale classifier reads,
plus an equipment photo and a KMC-wrap photo that no classifier exists for. Collecting all
three in ONE audit is the point -- a reviewer should not need a second workflow to look at
the wrap photos -- and that is what makes the two risks here worth pinning:

* A single ``ai_agent_id`` is applied to EVERY image in a session. Sent down that path, the
  scale classifier would be handed equipment and wrap photos and would report each one as a
  scale mismatch: false flags on photos it was never meant to see, at full gateway cost.
* Adding photos nothing can score must not change what the weight photos scored. The photo
  types are therefore kept separable end to end rather than blended into one total.

The weight-only case is deliberately left on the exact call this workflow has always made,
because that is what the nightly schedule runs unattended.
"""

from types import SimpleNamespace
from unittest import mock

import pytest

from connect_labs.workflow.templates.kmc_image_audit import (
    DEFAULT_IMAGE_PATHS,
    IMAGE_TYPE_NAMES,
    IMAGE_TYPES,
    _effective_image_types,
    _image_audits,
    _image_rules,
    _resolve_image_paths,
    run_default,
)

TEMPLATE_KEY = "kmc_image_audit"
DIAL_OPP, DIGITAL_OPP = 1236, 1487

WEIGHT = "anthropometric/upload_weight_image"
EQUIPMENT = "danger_signs_checklist/equipment_image_capture_checklist/equipments_image_capture"
WRAP = "commodities_delivered/kmc_wrap_provided_image"


def _definition(**config_overrides):
    config = {
        "opp_meta": {
            str(DIAL_OPP): {"llo": "EHA", "scale": "dial"},
            str(DIGITAL_OPP): {"llo": "PIPN", "scale": "digital"},
        },
        "agent_for_scale": {"digital": "scale_validation", "dial": "scale_dial_read"},
        "weight_image_path": WEIGHT,
        "weight_field_path": "child_weight_visit",
    }
    config.update(config_overrides)
    return SimpleNamespace(id=99, opportunity_id=DIGITAL_OPP, opportunity_ids=[DIGITAL_OPP], data={"config": config})


class _EagerResult:
    result = {"sessions": [{"id": 1, "images": [{"blob_id": "b"}]}]}

    def successful(self):
        return True


@pytest.fixture
def patched():
    with mock.patch("connect_labs.workflow.data_access.WorkflowDataAccess") as wda_cls, mock.patch(
        "connect_labs.audit.tasks.run_audit_creation"
    ) as task:
        wda_cls.return_value.create_run.return_value = SimpleNamespace(id=4242)
        task.apply.return_value = _EagerResult()
        yield {"wda": wda_cls, "task": task}


def _kwargs(patched):
    return patched["task"].apply.call_args.kwargs["kwargs"]


class TestWhichTypesARunCovers:
    def test_unset_means_the_weight_photo_alone(self):
        """A schedule saved before this setting existed must audit exactly what it did."""
        assert _resolve_image_paths(None, IMAGE_TYPES) == [WEIGHT]
        assert _resolve_image_paths([], IMAGE_TYPES) == [WEIGHT]
        assert DEFAULT_IMAGE_PATHS == [WEIGHT]

    def test_an_unknown_path_is_dropped_rather_than_passed_through(self):
        """A rule naming a path no opportunity has selects nothing and reports an empty
        window -- indistinguishable from a genuinely quiet day."""
        assert _resolve_image_paths([WRAP, "made/up/path"], IMAGE_TYPES) == [WRAP]

    def test_every_unknown_path_falls_back_to_the_weight_photo(self):
        assert _resolve_image_paths(["nope"], IMAGE_TYPES) == [WEIGHT]

    def test_order_follows_the_declaration_and_repeats_collapse(self):
        """Two rules for one path would double every image of that type."""
        assert _resolve_image_paths([WRAP, WEIGHT, WRAP], IMAGE_TYPES) == [WEIGHT, WRAP]

    def test_a_non_list_value_is_treated_as_unset(self):
        assert _resolve_image_paths("weight", IMAGE_TYPES) == [WEIGHT]


class TestOnlyTheWeightPhotoIsMachineScoreable:
    def test_exactly_one_declared_type_is_scoreable(self):
        scoreable = [t["path"] for t in IMAGE_TYPES if t["scoreable"]]
        assert scoreable == [WEIGHT]

    def test_the_choice_map_covers_every_declared_type(self):
        """The schedule dialog reads this map; a type missing from it is a type nobody
        can schedule."""
        assert set(IMAGE_TYPE_NAMES) == {t["path"] for t in IMAGE_TYPES}
        assert all(IMAGE_TYPE_NAMES.values()), "every type needs a label"


class TestSelectionRules:
    def test_each_chosen_type_gets_its_own_filter_rule(self):
        rules = _image_rules(image_paths=[WEIGHT, EQUIPMENT], image_types=IMAGE_TYPES)

        assert [r["image_path"] for r in rules] == [WEIGHT, EQUIPMENT]
        assert all(r["filter_by_image"] for r in rules), "several such rules are OR-ed by the selection layer"

    def test_the_weight_rule_carries_the_reading_it_is_checked_against(self):
        (rule,) = _image_rules(image_paths=[WEIGHT], image_types=IMAGE_TYPES)

        assert rule["field_path"] == "child_weight_visit"
        assert rule["label"] == "Scale Weight Reading"

    def test_a_type_with_nothing_to_compare_carries_no_field(self):
        """AuditCriteria.from_dict allows image-only rules and _extract_field_value
        returns None for an empty path, so no stray related field is attached."""
        rules = _image_rules(image_paths=[EQUIPMENT, WRAP], image_types=IMAGE_TYPES)

        assert [r["field_path"] for r in rules] == ["", ""]


class TestTheClassifierOnlySeesPhotosItCanRead:
    def test_only_the_scoreable_type_gets_a_reviewer(self):
        entries = _image_audits(
            image_paths=[WEIGHT, EQUIPMENT, WRAP], image_types=IMAGE_TYPES, agent_id="scale_validation"
        )
        by_path = {e["image_path"]: e for e in entries}

        assert [r["agent_id"] for r in by_path[WEIGHT]["reviewers"]] == ["scale_validation"]
        assert by_path[EQUIPMENT]["reviewers"] == []
        assert by_path[WRAP]["reviewers"] == []

    def test_the_reviewer_is_told_which_field_to_compare_against(self):
        entries = _image_audits(image_paths=[WEIGHT], image_types=IMAGE_TYPES, agent_id="scale_dial_read")
        (reviewer,) = entries[0]["reviewers"]

        assert reviewer["config"]["comparison_field"] == "child_weight_visit"

    def test_every_chosen_type_is_still_collected_even_with_no_reviewer(self):
        """No reviewer means the review task skips those photos, NOT that they are left
        out of the audit -- they are exactly the ones a human is there to look at."""
        entries = _image_audits(image_paths=[EQUIPMENT, WRAP], image_types=IMAGE_TYPES, agent_id="scale_validation")

        assert [e["image_path"] for e in entries] == [EQUIPMENT, WRAP]


class TestOneEffectiveAnswerForWhereTheWeightPhotoIs:
    def test_the_older_config_key_still_wins_and_reaches_the_reviewer(self):
        """config carried weight_image_path before there were image types, and the live
        workflows set it. If it stopped being honoured the picker would offer one path
        and the audit would select another."""
        cfg = {"weight_image_path": "renamed/weight_photo", "weight_field_path": "renamed_reading"}

        types = _effective_image_types(cfg)
        scoreable = [t for t in types if t["scoreable"]]

        assert [t["path"] for t in scoreable] == ["renamed/weight_photo"]
        entries = _image_audits(image_paths=["renamed/weight_photo"], image_types=types, agent_id="scale_validation")
        assert entries[0]["reviewers"][0]["config"]["comparison_field"] == "renamed_reading"

    def test_the_unscoreable_types_are_untouched_by_that_override(self):
        types = _effective_image_types({"weight_image_path": "renamed/weight_photo"})

        assert {t["path"] for t in types if not t["scoreable"]} == {EQUIPMENT, WRAP}

    def test_it_does_not_mutate_the_module_level_declaration(self):
        _effective_image_types({"weight_image_path": "renamed/weight_photo"})

        assert [t["path"] for t in IMAGE_TYPES if t["scoreable"]] == [WEIGHT]


class TestHowTheAuditIsDispatched:
    def test_weight_only_still_uses_the_single_agent_call_unchanged(self, patched):
        """The nightly schedule's path. It is also the only shape that names the
        classifier on the run summary; the per-image-type path reports "per-image-type"."""
        run_default(
            definition=_definition(schedule_defaults={"opportunity_ids": [DIGITAL_OPP]}),
            access_token="t",
            cadence="daily",
        )

        kwargs = _kwargs(patched)
        assert kwargs["ai_agent_id"] == "scale_validation"
        assert "image_audits" not in kwargs

    def test_adding_a_type_switches_to_per_image_type_reviewers(self, patched):
        run_default(
            definition=_definition(
                schedule_defaults={"opportunity_ids": [DIGITAL_OPP], "image_paths": [WEIGHT, EQUIPMENT]}
            ),
            access_token="t",
            cadence="daily",
        )

        kwargs = _kwargs(patched)
        assert "ai_agent_id" not in kwargs, "a single agent would be run on the equipment photos too"
        by_path = {e["image_path"]: e for e in kwargs["image_audits"]}
        assert [r["agent_id"] for r in by_path[WEIGHT]["reviewers"]] == ["scale_validation"]
        assert by_path[EQUIPMENT]["reviewers"] == []

    def test_an_unscoreable_type_on_its_own_never_calls_a_classifier(self, patched):
        """Selecting only wrap photos must not spend gateway budget."""
        run_default(
            definition=_definition(schedule_defaults={"opportunity_ids": [DIGITAL_OPP], "image_paths": [WRAP]}),
            access_token="t",
            cadence="daily",
        )

        kwargs = _kwargs(patched)
        assert "ai_agent_id" not in kwargs
        assert all(not e["reviewers"] for e in kwargs["image_audits"])

    def test_the_agent_still_follows_the_scale_hardware_on_the_new_path(self, patched):
        """EHA is a dial LLO; routing must not be lost by changing dispatch."""
        run_default(
            definition=_definition(schedule_defaults={"opportunity_ids": [DIAL_OPP], "image_paths": [WEIGHT, WRAP]}),
            access_token="t",
            cadence="daily",
        )

        by_path = {e["image_path"]: e for e in _kwargs(patched)["image_audits"]}
        assert [r["agent_id"] for r in by_path[WEIGHT]["reviewers"]] == ["scale_dial_read"]

    def test_the_selection_rules_cover_every_chosen_type(self, patched):
        run_default(
            definition=_definition(
                schedule_defaults={"opportunity_ids": [DIGITAL_OPP], "image_paths": [WEIGHT, EQUIPMENT, WRAP]}
            ),
            access_token="t",
            cadence="daily",
        )

        rules = _kwargs(patched)["criteria"]["related_fields"]
        assert [r["image_path"] for r in rules] == [WEIGHT, EQUIPMENT, WRAP]


class TestTheRunRecordsWhatItAudited:
    def test_the_chosen_types_are_on_the_run_from_the_start(self, patched):
        """Without this a run that audited three photo types is indistinguishable in the
        history from a weight-only one, and its lower machine-reviewed share looks like a
        classifier failure rather than the extra photos it actually was."""
        run_default(
            definition=_definition(
                schedule_defaults={"opportunity_ids": [DIGITAL_OPP], "image_paths": [WEIGHT, WRAP]}
            ),
            access_token="t",
            cadence="daily",
        )

        state = patched["wda"].return_value.create_run.call_args.kwargs["initial_state"]
        assert state["image_paths"] == [WEIGHT, WRAP]

    def test_the_outcome_reports_them_too(self, patched):
        result = run_default(
            definition=_definition(schedule_defaults={"opportunity_ids": [DIGITAL_OPP], "image_paths": [WRAP]}),
            access_token="t",
            cadence="daily",
        )

        assert result["image_paths"] == [WRAP]


class TestTheScheduleDialogCanSetIt:
    def test_the_option_is_declared_against_the_shared_choice_map(self):
        from connect_labs.workflow.templates import TEMPLATES

        opt = {o["key"]: o for o in TEMPLATES[TEMPLATE_KEY]["schedule_options"]}["image_paths"]

        assert opt["type"] == "multi_str"
        assert opt["choices_from_config"] == "image_type_names"

    def test_the_dialog_offers_the_declared_types_as_string_choices(self):
        from connect_labs.workflow.templates import TEMPLATES, schedule_options_for_definition

        config = TEMPLATES[TEMPLATE_KEY]["definition"]["config"]
        definition = SimpleNamespace(template_type=TEMPLATE_KEY, data={"config": config})

        opt = {o["key"]: o for o in schedule_options_for_definition(definition)}["image_paths"]

        assert opt["unavailable"] is False
        assert {c["value"] for c in opt["choices"]} == set(IMAGE_TYPE_NAMES)
        assert all(isinstance(c["value"], str) for c in opt["choices"])

    def test_it_is_pre_ticked_so_a_schedule_that_predates_it_still_saves(self):
        """The dialog posts EVERY option, so an untouched multi-select posting an empty
        list would fail the "select at least one" rule and block the whole schedule --
        on a setting the user never opened."""
        from connect_labs.workflow.templates import TEMPLATES, schedule_options_for_definition

        config = TEMPLATES[TEMPLATE_KEY]["definition"]["config"]
        definition = SimpleNamespace(template_type=TEMPLATE_KEY, data={"config": config})

        opt = {o["key"]: o for o in schedule_options_for_definition(definition)}["image_paths"]
        assert opt["selected"] == [WEIGHT]

    def test_a_saved_selection_round_trips_through_validation(self):
        from connect_labs.workflow.templates import TEMPLATES, schedule_options_for_definition
        from connect_labs.workflow.views import _clean_schedule_defaults

        config = TEMPLATES[TEMPLATE_KEY]["definition"]["config"]
        definition = SimpleNamespace(template_type=TEMPLATE_KEY, data={"config": config})
        options = schedule_options_for_definition(definition)

        values, error = _clean_schedule_defaults({"image_paths": [WRAP, WEIGHT]}, options)

        assert error is None, error
        assert values["image_paths"] == sorted([WRAP, WEIGHT])

    def test_a_path_that_is_not_offered_is_refused_by_name(self):
        from connect_labs.workflow.templates import TEMPLATES, schedule_options_for_definition
        from connect_labs.workflow.views import _clean_schedule_defaults

        config = TEMPLATES[TEMPLATE_KEY]["definition"]["config"]
        definition = SimpleNamespace(template_type=TEMPLATE_KEY, data={"config": config})

        values, error = _clean_schedule_defaults(
            {"image_paths": ["made/up/path"]}, schedule_options_for_definition(definition)
        )

        assert values is None
        assert "made/up/path" in error

    def test_an_empty_selection_is_refused_rather_than_silently_auditing_nothing(self):
        from connect_labs.workflow.templates import TEMPLATES, schedule_options_for_definition
        from connect_labs.workflow.views import _clean_schedule_defaults

        config = TEMPLATES[TEMPLATE_KEY]["definition"]["config"]
        definition = SimpleNamespace(template_type=TEMPLATE_KEY, data={"config": config})

        values, error = _clean_schedule_defaults({"image_paths": []}, schedule_options_for_definition(definition))

        assert values is None
        assert "select at least one" in error


class TestTheIntegerMultiSelectStillBehaves:
    """multi_int and multi_str now share one code path; opportunity_ids is the existing
    caller and must be unaffected by that."""

    def test_opportunities_are_still_integers_end_to_end(self):
        from connect_labs.workflow.templates import TEMPLATES, schedule_options_for_definition
        from connect_labs.workflow.views import _clean_schedule_defaults

        config = TEMPLATES[TEMPLATE_KEY]["definition"]["config"]
        definition = SimpleNamespace(template_type=TEMPLATE_KEY, data={"config": config})
        options = schedule_options_for_definition(definition)
        opt = {o["key"]: o for o in options}["opportunity_ids"]

        assert all(isinstance(c["value"], int) for c in opt["choices"])

        values, error = _clean_schedule_defaults({"opportunity_ids": [DIGITAL_OPP]}, options)
        assert error is None, error
        assert values["opportunity_ids"] == [DIGITAL_OPP]

    def test_opportunities_are_not_pre_ticked_because_they_declare_no_default(self):
        from connect_labs.workflow.templates import TEMPLATES, schedule_options_for_definition

        config = TEMPLATES[TEMPLATE_KEY]["definition"]["config"]
        definition = SimpleNamespace(template_type=TEMPLATE_KEY, data={"config": config})

        opt = {o["key"]: o for o in schedule_options_for_definition(definition)}["opportunity_ids"]
        assert opt["selected"] == []
