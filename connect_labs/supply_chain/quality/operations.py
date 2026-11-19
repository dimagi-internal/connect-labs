"""Quality test operations: the Tests page, the HTTP API and the MCP tools all go through these."""

from connect_labs.supply_chain import records
from connect_labs.supply_chain.operations import ID, _data_with, obj, record, register_operation
from connect_labs.supply_chain.quality import service

_DATE = {"type": "string", "format": "date"}
_READING = {"type": ["number", "string"]}

_QUALITY_TEST_DATA = _data_with(
    ("kind", "supply_point_id", "tested_on", "tested_by", "source"),
    kind={"enum": list(records.QUALITY_TEST_KINDS)},
    supply_point_id=ID,
    tested_on=_DATE,
    tested_by={"type": "string", "minLength": 1, "maxLength": 255},
    commodity_slug={"type": "string", "minLength": 1},
    item_id=ID,
    batch={"type": "string", "maxLength": 64},
    sample_ref={"type": "string", "maxLength": 64},
    dispenser={"type": "string", "maxLength": 255},
    collected_by={"type": "string", "maxLength": 255},
    collected_on=_DATE,
    readings={
        "type": "object",
        "additionalProperties": _READING,
        "description": (
            "What was measured, each under the name of the product's own specification figure "
            '(its spec_requirements\' `field`), e.g. {"pH": 11.4}.'
        ),
    },
    trials={
        "type": "array",
        "items": _READING,
        "maxItems": 10,
        "description": (
            f"Repeat readings averaged into readings.{records.TRIALS_AVERAGE_FIELD} unless that reading "
            "is given outright, e.g. the three trials of a double dilution."
        ),
    },
    storage={
        "type": "object",
        "properties": {name: {"type": "boolean"} for name in records.STORAGE_CHECKS},
        "additionalProperties": False,
        "description": "A storage check: true where it is as it should be, false where it is not.",
    },
    opportunity_id=ID,
    source={"enum": list(records.SOURCES)},
    recorded_by_org_id=ID,
    note={"type": "string"},
)


@register_operation(
    name="quality_test_list",
    summary=(
        "Quality tests recorded in this program, newest first: batch tests in store, dispenser samples and "
        "storage checks, each with what was measured, its result against the product's specification "
        "(pass, fail or incomplete) and why. partner_org_id keeps the places one partner runs. Withdrawn "
        "tests are left out unless include_withdrawn."
    ),
    input_schema=obj(
        {
            "supply_point_id": ID,
            "partner_org_id": ID,
            "kind": {"enum": list(records.QUALITY_TEST_KINDS)},
            "result": {"enum": list(records.QUALITY_TEST_RESULTS)},
            "batch": {"type": "string"},
            "since": _DATE,
            "until": _DATE,
            "include_withdrawn": {"type": "boolean"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 5000},
        }
    ),
)
def quality_test_list(
    access,
    supply_point_id=None,
    partner_org_id=None,
    kind=None,
    result=None,
    batch=None,
    since=None,
    until=None,
    include_withdrawn=False,
    limit=500,
):
    return [
        record(test)
        for test in service.list_tests(
            access,
            supply_point_id=supply_point_id,
            partner_org_id=partner_org_id,
            kind=kind,
            result=result,
            batch=batch,
            since=since,
            until=until,
            include_withdrawn=include_withdrawn,
            limit=limit,
        )
    ]


@register_operation(
    name="quality_test_record",
    summary=(
        "Record a quality test a supervisor or store keeper carried out. kind batch: a sample from a batch "
        "in store (name the batch). kind dispenser_sample: a sample a field worker brought back from a "
        "dispenser (name the dispenser, who collected it and when). Both give readings under the product's "
        "specification figures, or trials to average, and are judged against the product's own "
        "spec_requirements. kind storage: answer the storage checks; any false fails. The result and the "
        "reasons are stored with the test; a failed one is listed by checks_list as quality_test_failed "
        "until a newer test of the same batch, dispenser or store passes."
    ),
    input_schema=obj({"data": _QUALITY_TEST_DATA}, required=("data",)),
    is_write=True,
)
def quality_test_record(access, data):
    return record(service.record_test(access, data))


@register_operation(
    name="quality_test_withdraw",
    summary=(
        "Withdraw a quality test recorded by mistake (the wrong batch, a mistyped reading). Nothing is "
        "deleted: the test stays on the list marked withdrawn, with who withdrew it, when and why, and "
        "stops counting in every check. Record the test again with the right figures."
    ),
    input_schema=obj(
        {"test_id": ID, "reason": {"type": "string", "minLength": 5, "maxLength": 500}},
        required=("test_id", "reason"),
    ),
    is_write=True,
)
def quality_test_withdraw(access, test_id, reason):
    return record(service.withdraw_test(access, test_id, reason))
