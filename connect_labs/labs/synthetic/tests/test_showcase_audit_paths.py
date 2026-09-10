"""The generator and the audit must agree on the path, or the audit is empty.

`data_access.py` matches an image rule to an image by EXACT equality against the
image's `question_id` (`rule.get("image_path") == question_id`). And
`extract_images_with_question_ids` builds that id from the form_json AFTER
unwrapping the top-level ``form`` key, so the id is `anthropometric/...`, not
`form/anthropometric/...`.

Get it wrong and there is no error: the audit is created, finds no image with a
matching rule, and runs no reviewer. A demo that shows nothing, with nothing in
the logs to say why. The docstring in ai_review_config.py says `form/scale_photo`,
which reads as though the prefix belongs — this test is here because that example
sent one person down exactly that path.
"""

import datetime as dt

import pytest

from connect_labs.audit.analysis_config import extract_images_with_question_ids
from connect_labs.labs.synthetic.cohort import CohortSpec
from connect_labs.labs.synthetic.generator.fixtures.manifest import ImageConfig
from connect_labs.labs.synthetic.generator.fixtures.showcase import build_showcase_visits

SPEC = "connect_labs/labs/synthetic/cohorts/kmc.yaml"


@pytest.fixture(scope="module")
def kmc():
    with open(SPEC) as fh:
        return CohortSpec.from_yaml(fh.read())


def _first_visit(image_config: dict):
    cfg = ImageConfig(**image_config)
    visits = build_showcase_visits(cfg, opportunity_id=10020, start_date=dt.date(2026, 3, 2))
    # The first visit is now the registration form, which carries no weighing
    # and no photo; the audit reads the weighings, so test the first of those.
    return next(v for v in visits if v["images"])


def test_the_audit_sees_the_question_id_the_config_must_name(kmc):
    visit = _first_visit(kmc.image_config)
    images = extract_images_with_question_ids(visit)
    assert images, "a showcase visit must expose an image to the audit extractor"
    qid = images[0]["question_id"]

    # What an audit's image_audits[].image_path has to be, character for character.
    assert qid == "anthropometric/upload_weight_image"
    # The trap: the `form/` prefix does NOT belong, because form_json is unwrapped.
    assert not qid.startswith("form/")


def test_the_configured_paths_derive_from_the_spec_not_a_constant(kmc):
    """If someone renames question_path in the spec, the expected audit path moves
    with it — the assertion above would then be stale, so tie them together."""
    expected = kmc.image_config["question_path"].removeprefix("form.").replace(".", "/")
    visit = _first_visit(kmc.image_config)
    assert extract_images_with_question_ids(visit)[0]["question_id"] == expected


def test_the_comparison_field_resolves_against_the_same_visit(kmc):
    """The reviewer pulls its reading via comparison_field. Both separators work
    for a VALUE lookup (unlike image_path), but it must actually resolve."""
    from connect_labs.audit.data_access import AuditDataAccess

    visit = _first_visit(kmc.image_config)
    form = visit["form_json"]["form"]
    comparison_field = kmc.image_config["reading_path"].removeprefix("form.").replace(".", "/")
    value = AuditDataAccess._extract_field_value(form, comparison_field)
    assert value is not None, f"{comparison_field} does not resolve on a showcase visit"
    assert float(value) > 0
