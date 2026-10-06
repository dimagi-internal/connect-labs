"""The IPTsc School Delivery dashboard template's contract with the framework.

The page's calculations are tested in JavaScript
(``templates/__tests__/iptsc_render.test.mjs``); this file pins what the Python
side promises: the registry entry, the pipelines the render reads, and the
CommCare HQ sign-in the Adverse Event Log and child cases need.
"""

from connect_labs.workflow.templates import TEMPLATE_GROUP_OF, get_template
from connect_labs.workflow.templates import iptsc_dashboard as tpl


def test_registered_as_a_report():
    t = get_template("iptsc_dashboard")
    assert t is not None
    assert TEMPLATE_GROUP_OF["iptsc_dashboard"] == "reports"
    # A live dashboard: no completion moment, so no saved runs.
    assert not t.get("supports_saved_runs")


def test_the_render_reads_the_pipelines_the_template_creates():
    aliases = [p["alias"] for p in tpl.PIPELINE_SCHEMAS]
    assert aliases == ["doses", "child_cases", "ae_log"]
    for alias in aliases:
        assert f"pipelines.{alias}" in tpl.RENDER_CODE


def test_commcare_sources_require_a_commcare_sign_in():
    sources = {p["alias"]: p["schema"]["data_source"]["type"] for p in tpl.PIPELINE_SCHEMAS}
    assert sources == {"doses": "connect_csv", "child_cases": "cchq_cases", "ae_log": "cchq_forms"}
    assert tpl.DEFINITION["config"]["auth_requires"] == ["connect", "commcare_hq"]


def test_children_are_keyed_on_the_child_case_not_the_connect_entity():
    """The follow-up form builds its Connect entity id from 'Dose 1' for every
    dose, so the entity id can never identify a child's dose 2 or 3."""
    fields = {f["name"]: f for f in tpl.DOSES_SCHEMA["fields"]}
    assert fields["child_id"]["paths"][0] == "form.child_id"
    assert tpl.DOSES_SCHEMA["linking_field"] == "child_id"
    window = tpl.DOSES_SCHEMA["window_fields"][0]
    assert window["partition_by"] == "child_id"


def test_field_names_are_lower_case():
    """Field names become SQL column aliases; Postgres folds upper case away."""
    for p in tpl.PIPELINE_SCHEMAS:
        for f in p["schema"]["fields"]:
            assert f["name"] == f["name"].lower(), f["name"]
