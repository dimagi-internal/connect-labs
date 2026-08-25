"""The photo types must be declared in ONE place and read everywhere else.

Four surfaces have to agree about what a photo type is and what it is called: the dashboard
picker, the schedule dialog, the rules the headless runner builds, and the per-type numbers
the results tab shows. Each of those could perfectly well carry its own copy of the list --
and then a type added in one place would silently be missing from the others, or worse,
named one thing here and another there.

These tests fail if any of those surfaces starts keeping its own copy, and if the key the
render reads for per-type numbers stops being the key the backend sends.
"""

import re

from connect_labs.audit.models import AuditSessionRecord
from connect_labs.workflow.templates.kmc_image_audit import (
    DEFINITION,
    IMAGE_TYPE_NAMES,
    IMAGE_TYPES,
    RENDER_CODE,
    TEMPLATE,
)

EQUIPMENT = "danger_signs_checklist/equipment_image_capture_checklist/equipments_image_capture"
WRAP = "commodities_delivered/kmc_wrap_provided_image"


class TestTheDeclarationIsShippedOnConfig:
    def test_config_carries_the_types_and_their_labels(self):
        config = DEFINITION["config"]

        assert config["image_types"] == IMAGE_TYPES
        assert config["image_type_names"] == IMAGE_TYPE_NAMES

    def test_the_labels_map_matches_the_types_exactly(self):
        assert set(IMAGE_TYPE_NAMES) == {t["path"] for t in IMAGE_TYPES}

    def test_the_schedule_option_reads_that_same_config_key(self):
        opt = {o["key"]: o for o in TEMPLATE["schedule_options"]}["image_paths"]

        assert opt["choices_from_config"] == "image_type_names"
        assert opt["choices_from_config"] in DEFINITION["config"]

    def test_every_declared_type_has_the_fields_the_rules_need(self):
        for spec in IMAGE_TYPES:
            assert spec.get("path"), spec
            assert spec.get("label"), spec
            assert "scoreable" in spec, spec
            # A scoreable type must name the field it is checked against, or the
            # classifier is handed a photo and no reading to compare it with.
            if spec["scoreable"]:
                assert spec.get("field_path"), spec


class TestTheRenderReadsThemRatherThanRepeatingThem:
    def test_the_render_takes_its_types_from_config(self):
        assert "cfg.image_types" in RENDER_CODE

    def test_the_render_does_not_hardcode_a_photo_path(self):
        """The strongest form of the guard: if these strings appear in the render at all,
        someone has written the list down a second time and the two can now drift."""
        assert EQUIPMENT not in RENDER_CODE
        assert WRAP not in RENDER_CODE

    def test_the_render_filters_photos_on_the_image_path(self):
        """The per-photo filter must key on question_id -- the image's own path, which the
        bulk-data rows carry. Keying it on the comparison field would filter on a value
        that only weight photos have."""
        assert "x.question_id === photoType" in RENDER_CODE

    def test_the_render_reads_the_key_the_backend_actually_sends(self):
        """Cross-module, deliberately. The render reads s.by_image_type; if that key is
        ever renamed on the session summary the per-type counts silently become blank, and
        a blank column looks like "this worker has no photos" rather than a broken link.
        """
        assert "s.by_image_type" in RENDER_CODE

        session = AuditSessionRecord(
            {
                "id": 1,
                "experiment": "audit",
                "type": "AuditSession",
                "data": {
                    "visit_images": {"1": [{"blob_id": "b", "question_id": EQUIPMENT}]},
                    "visit_results": {},
                },
                "opportunity_id": 1487,
            }
        )
        summary = session.to_summary_dict()

        assert "by_image_type" in summary
        assert EQUIPMENT in summary["by_image_type"]

    def test_the_render_degrades_when_that_key_is_absent(self):
        """It has to: the key only arrives once the server side is deployed, and the render
        is live-editable and ships first. Until then every photo counts as scoreable, which
        is exactly the behaviour this dashboard had when it only audited weight photos."""
        assert "if (!byType || typeof byType !== 'object')" in RENDER_CODE
        assert "scoreable: imgs, humanOnly: 0" in RENDER_CODE


class TestTheClassifierRoutingIsStatedOnce:
    def test_only_a_scoreable_type_is_given_a_reviewer_in_the_render(self):
        assert "reviewers: isScoreablePath(p) ?" in RENDER_CODE

    def test_the_render_keeps_the_single_agent_call_for_the_weight_only_case(self):
        """The nightly schedule's shape. If this disappears, every run switches to the
        per-image-type path and the run summary stops naming the classifier."""
        assert "const weightOnly = imageTypes.length === 1 && isScoreablePath(imageTypes[0])" in RENDER_CODE
        assert "{ ai_agent_id: effectiveAgent(oid) }" in RENDER_CODE


class TestTheRenderIsStillWellFormed:
    def test_the_embedded_literal_survived_escaping(self):
        """RENDER_CODE lives in a NON-raw triple-quoted literal, so every backslash in the
        JSX has to be doubled in the source. Miss that and the CSV line-join's \\r\\n
        becomes a real newline inside a JS string -- a syntax error that no Python test
        would otherwise notice, because the module still imports perfectly.
        """
        assert "'\\r\\n'" in RENDER_CODE, "the CSV join lost its escapes"
        assert re.search(r'/\[",\\n\]/', RENDER_CODE), "the CSV quoting regex lost its escapes"

    def test_it_is_one_component_and_is_not_truncated(self):
        assert RENDER_CODE.startswith("function WorkflowUI(")
        assert RENDER_CODE.rstrip().endswith("}")
        assert RENDER_CODE.count("function WorkflowUI(") == 1
