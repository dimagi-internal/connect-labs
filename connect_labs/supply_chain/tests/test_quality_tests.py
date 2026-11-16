"""Quality tests in Supply: a batch in store, a dispenser sample, a storage check.

The rules under test: a sample is judged against the product's own specification, with the trials'
average worked out; a storage check fails on any "no"; a test is never edited or deleted, only
withdrawn with a reason; the newest standing test of a batch, dispenser or store decides whether
`quality_test_failed` is raised; the Tests page lists, filters by partner and downloads them.

THIS REPOSITORY IS PUBLIC. Placeholder names and figures only.
"""

import csv
import io
from datetime import date, timedelta

import pytest
from django.urls import reverse

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.labs.models import LabsOrg
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.models import Commodity, Item, QualityTest, SupplyPoint, scope_key
from connect_labs.supply_chain.operations import call_operation
from connect_labs.supply_chain.quality import judge

pytestmark = pytest.mark.django_db

PROGRAM = 10991
SCOPE = scope_key(program_id=PROGRAM)

SPEC = [
    {"field": "pH", "operator": ">", "value": 11},
    {"field": "double_dilution_avg_mg_l", "operator": ">=", "value": 0.96, "unit": "mg/L"},
    {"field": "double_dilution_avg_mg_l", "operator": "<=", "value": 1.44, "unit": "mg/L"},
]


def _access():
    return SupplyDataAccess(access_token="placeholder", program_id=PROGRAM, caller=SYSTEM)


def _op(name, **payload):
    return call_operation(name, _access(), payload)


def _failing():
    return _op("checks_list", kinds=["quality_test_failed"])["checks"]


def _day(days_ago=0):
    return (date.today() - timedelta(days=days_ago)).isoformat()


@pytest.fixture
def world():
    product = Commodity.objects.create(
        scope_key=SCOPE, slug="a-solution", name="A Solution", base_unit="liter", spec_requirements=SPEC
    )
    item = Item.objects.create(scope_key=SCOPE, sku="SOL-3L", name="A Solution 3 L", commodity=product)
    partner = LabsOrg.objects.create(slug="a-partner", name="A Partner")
    other = LabsOrg.objects.create(slug="another-partner", name="Another Partner")
    mine = SupplyPoint.objects.create(
        program_id=PROGRAM,
        slug="mine",
        name="Mine Store",
        kind="central_store",
        managed_by_org=partner,
        source="we_recorded",
    )
    theirs = SupplyPoint.objects.create(
        program_id=PROGRAM,
        slug="theirs",
        name="Their Store",
        kind="central_store",
        managed_by_org=other,
        source="we_recorded",
    )
    return product, item, partner, mine, theirs


def _batch(point, item, *, batch="B1", ph="11.4", trials=("1.2", "1.25", "1.3"), days_ago=1, **over):
    data = {
        "kind": "batch",
        "supply_point_id": point.pk,
        "tested_on": _day(days_ago),
        "tested_by": "A Store Keeper",
        "item_id": item.pk,
        "batch": batch,
        "readings": {"pH": ph},
        "trials": list(trials),
        "source": "we_recorded",
    }
    data.update(over)
    return _op("quality_test_record", data=data)


# ---- the judge -------------------------------------------------------------


def test_the_trials_are_averaged_into_the_figure_the_specification_judges():
    readings, trials = judge.readings_from({"pH": 11.4}, ["1.2", "1.25", "1.3"])
    assert readings == {"pH": "11.4", "double_dilution_avg_mg_l": "1.25"}
    assert trials == ["1.2", "1.25", "1.3"]


def test_an_average_typed_in_stands_over_the_trials():
    readings, _ = judge.readings_from({"double_dilution_avg_mg_l": "1.1"}, ["2", "2", "2"])
    assert readings["double_dilution_avg_mg_l"] == "1.1"


def test_a_reading_that_is_not_a_number_is_refused():
    with pytest.raises(ValueError, match="pH: 'high' is not a number"):
        judge.readings_from({"pH": "high"}, [])


def test_a_storage_check_fails_on_any_no():
    assert judge.judge_storage({"shaded": True, "off_the_floor": False}) == ("fail", ["Off the floor: no"])
    assert judge.judge_storage({"shaded": True}) == ("pass", [])
    assert judge.judge_storage({}) == ("incomplete", ["Nothing was checked."])


# ---- recording ---------------------------------------------------------------


def test_a_batch_within_its_specification_passes(world):
    _, item, _, mine, _ = world
    test = _batch(mine, item)
    assert (test["result"], test["findings"]) == ("pass", [])
    assert test["readings"] == {"pH": "11.4", "double_dilution_avg_mg_l": "1.25"}
    assert test["commodity_slug"] == "a-solution"


def test_a_batch_outside_its_specification_fails_and_says_which_figure(world):
    _, item, _, mine, _ = world
    test = _batch(mine, item, ph="10.5", trials=("1.6", "1.7", "1.8"))
    assert test["result"] == "fail"
    assert test["findings"] == [
        "pH 10.5; it must be more than 11.",
        "Double-dilution average 1.7 mg/L; it must be at most 1.44 mg/L.",
    ]


def test_a_figure_not_measured_leaves_the_test_incomplete_not_passed(world):
    _, item, _, mine, _ = world
    test = _batch(mine, item, trials=())
    assert test["result"] == "incomplete"
    assert test["findings"] == ["Double-dilution average was not measured; it must be at least 0.96 mg/L."]


def test_a_product_with_no_specification_cannot_pass(world):
    product, item, _, mine, _ = world
    product.spec_requirements = []
    product.save()
    test = _batch(mine, item)
    assert test["result"] == "incomplete"
    assert "no specification" in test["findings"][0]


def test_a_batch_test_names_its_batch(world):
    _, item, _, mine, _ = world
    with pytest.raises(ValueError, match="names the batch"):
        _batch(mine, item, batch="")


def test_a_test_cannot_be_recorded_before_it_happens(world):
    _, item, _, mine, _ = world
    with pytest.raises(ValueError, match="in the future"):
        _batch(mine, item, days_ago=-2)


def test_a_dispenser_sample_names_the_dispenser_and_is_collected_before_it_is_tested(world):
    _, item, _, mine, _ = world
    data = {
        "kind": "dispenser_sample",
        "supply_point_id": mine.pk,
        "tested_on": _day(1),
        "tested_by": "A Supervisor",
        "item_id": item.pk,
        "dispenser": "Water point 7",
        "collected_by": "a_rider",
        "collected_on": _day(2),
        "readings": {"pH": "11.2"},
        "trials": ["1.0", "1.1", "1.0"],
        "source": "we_recorded",
    }
    assert _op("quality_test_record", data=data)["result"] == "pass"
    with pytest.raises(ValueError, match="collected before it is tested"):
        _op("quality_test_record", data={**data, "collected_on": _day(0)})
    with pytest.raises(ValueError, match="names the dispenser"):
        _op("quality_test_record", data={**data, "dispenser": ""})


def test_a_storage_check_takes_answers_not_readings(world):
    _, _, _, mine, _ = world
    base = {
        "kind": "storage",
        "supply_point_id": mine.pk,
        "tested_on": _day(),
        "tested_by": "A",
        "source": "we_recorded",
    }
    test = _op("quality_test_record", data={**base, "storage": {"shaded": True, "no_leaks": False}})
    assert (test["result"], test["findings"]) == ("fail", ["No leaks: no"])
    with pytest.raises(ValueError, match="readings belong"):
        _op("quality_test_record", data={**base, "storage": {"shaded": True}, "readings": {"pH": 11}})


# ---- withdrawing ------------------------------------------------------------


def test_a_withdrawn_test_stays_listed_marked_and_stops_counting(world):
    _, item, _, mine, _ = world
    test = _batch(mine, item, ph="9")
    assert [c["subject"]["id"] for c in _failing()] == [test["id"]]

    withdrawn = _op("quality_test_withdraw", test_id=test["id"], reason="wrong batch typed")
    assert withdrawn["withdrawn"] and withdrawn["withdrawn_reason"] == "wrong batch typed"
    assert QualityTest.objects.filter(pk=test["id"]).exists()
    assert _op("quality_test_list") == []
    assert [t["id"] for t in _op("quality_test_list", include_withdrawn=True)] == [test["id"]]
    assert _failing() == []
    with pytest.raises(ValueError, match="already withdrawn"):
        _op("quality_test_withdraw", test_id=test["id"], reason="again, by mistake")


# ---- the check ---------------------------------------------------------------


def test_the_newest_test_of_a_batch_decides_whether_it_is_failing(world):
    _, item, _, mine, _ = world
    _batch(mine, item, ph="9", days_ago=5)
    assert len(_failing()) == 1
    _batch(mine, item, days_ago=2)  # retested, passed
    assert _failing() == []
    failed_again = _batch(mine, item, ph="9", days_ago=1)
    found = _failing()
    assert [c["subject"]["id"] for c in found] == [failed_again["id"]]
    assert found[0]["facts"]["batch"] == "B1" and found[0]["category"] == "threshold"


def test_two_batches_are_judged_apart(world):
    _, item, _, mine, _ = world
    _batch(mine, item, batch="B1", ph="9")
    _batch(mine, item, batch="B2")
    assert [c["facts"]["batch"] for c in _failing()] == ["B1"]


def test_a_failed_test_reaches_the_partner_alert(world):
    from connect_labs.supply_chain.alerts.operations import PARTNER_CHECKS
    from connect_labs.supply_chain.alerts.service import _subject_scope, record_url

    _, item, _, mine, _ = world
    test = _batch(mine, item, ph="9")
    assert "quality_test_failed" in PARTNER_CHECKS
    points, commodities = _subject_scope("quality_test", test["id"], {})
    assert points == {mine.pk} and len(commodities) == 1
    assert "/supply/tests/" in record_url("quality_test", test["id"], {}, PROGRAM)


# ---- listing -------------------------------------------------------------------


def test_the_list_filters_by_partner_kind_result_batch_and_dates(world):
    _, item, partner, mine, theirs = world
    a = _batch(mine, item, batch="B1", days_ago=10)
    b = _batch(theirs, item, batch="B2", ph="9", days_ago=3)
    assert [t["id"] for t in _op("quality_test_list", partner_org_id=partner.pk)] == [a["id"]]
    assert [t["id"] for t in _op("quality_test_list", result="fail")] == [b["id"]]
    assert [t["id"] for t in _op("quality_test_list", batch="b1")] == [a["id"]]
    assert [t["id"] for t in _op("quality_test_list", since=_day(5))] == [b["id"]]
    assert [t["id"] for t in _op("quality_test_list", until=_day(5))] == [a["id"]]
    assert _op("quality_test_list", kind="storage") == []


# ---- the page --------------------------------------------------------------------


@pytest.fixture
def page(client, django_user_model, monkeypatch):
    from connect_labs.scope_config import service
    from connect_labs.scope_config.scopes import Scope
    from connect_labs.supply_chain import form_views, views  # noqa: F401  -- bind before patching
    from connect_labs.supply_chain.api_views import _access as real_access
    from connect_labs.supply_chain.quality import views as quality_views  # noqa: F401

    account = django_user_model.objects.create_user(username="a-person", password="x", email="a@dimagi.com")
    client.force_login(account)

    def _scoped(request):
        access = real_access(request)
        access.program_id = PROGRAM
        access.caller = SYSTEM
        return access

    for module in ("form_views", "views", "quality.views"):
        monkeypatch.setattr(f"connect_labs.supply_chain.{module}.has_program_context", lambda request: True)
        monkeypatch.setattr(f"connect_labs.supply_chain.{module}._access", _scoped)
    monkeypatch.setattr(
        service.access,
        "may_use",
        lambda caller, *, organization_id=None, program_id=None, opportunity_id=None: None,
    )
    service.update("supply", Scope.of("program", PROGRAM), {"tabs": {"supply_chain:tests": {"hidden": False}}}, SYSTEM)
    return client


def test_the_page_lists_tests_with_their_result_and_what_failed(world, page):
    _, item, partner, mine, theirs = world
    _batch(mine, item, batch="GOOD-1")
    _batch(theirs, item, batch="BAD-1", ph="9")
    body = page.get(reverse("supply_chain:tests") + f"?program_id={PROGRAM}").content.decode()
    assert 'data-testid="tests-table"' in body
    assert "GOOD-1" in body and "BAD-1" in body
    assert 'data-testid="tests-failing"' in body and "Do not issue this batch" in body

    mine_only = page.get(
        reverse("supply_chain:tests") + f"?program_id={PROGRAM}&partner={partner.pk}"
    ).content.decode()
    assert "GOOD-1" in mine_only and "BAD-1" not in mine_only


def test_the_download_is_every_test_on_the_page_as_csv(world, page):
    _, item, partner, mine, theirs = world
    _batch(mine, item, batch="GOOD-1")
    _batch(theirs, item, batch="BAD-1", ph="9")
    response = page.get(reverse("supply_chain:tests_download") + f"?program_id={PROGRAM}&partner={partner.pk}")
    assert response.status_code == 200 and response["Content-Type"].startswith("text/csv")
    rows = list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))
    header, body = rows[0], rows[1:]
    assert ["pH", "Average (mg/L)"] == [h for h in header if h in ("pH", "Average (mg/L)")]
    assert len(body) == 1 and body[0][header.index("Batch")] == "GOOD-1"
    assert body[0][header.index("Partner")] == "A Partner" and body[0][header.index("Result")] == "Pass"


def test_recording_a_batch_test_from_the_form(world, page):
    _, item, _, mine, _ = world
    url = reverse("supply_chain:test_record", args=["batch"]) + f"?program_id={PROGRAM}"
    form = page.get(url).content.decode()
    assert "Trial 1 (mg/L)" in form and "pH" in form
    response = page.post(
        url,
        {
            "supply_point": mine.pk,
            "tested_on": _day(1),
            "tested_by": "A Store Keeper",
            "item": item.pk,
            "batch": "FORM-1",
            "reading__pH": "11.5",
            "trial_1": "1.1",
            "trial_2": "1.2",
            "trial_3": "1.3",
            "source": "we_recorded",
        },
    )
    assert response.status_code == 302, response.context["form"].errors
    saved = QualityTest.objects.get(batch="FORM-1")
    assert saved.result == "pass" and saved.readings["double_dilution_avg_mg_l"] == "1.2"


def test_recording_a_storage_check_from_the_form(world, page):
    _, _, _, mine, _ = world
    url = reverse("supply_chain:test_record", args=["storage"]) + f"?program_id={PROGRAM}"
    response = page.post(
        url,
        {
            "supply_point": mine.pk,
            "tested_on": _day(),
            "tested_by": "A Supervisor",
            "check__shaded": "yes",
            "check__off_the_floor": "no",
            "source": "we_recorded",
        },
    )
    assert response.status_code == 302, response.context["form"].errors
    saved = QualityTest.objects.get(kind="storage")
    assert saved.storage == {"shaded": True, "off_the_floor": False} and saved.result == "fail"


def test_withdrawing_from_the_page(world, page):
    _, item, _, mine, _ = world
    test = _batch(mine, item)
    url = reverse("supply_chain:test_withdraw", args=[test["id"]]) + f"?program_id={PROGRAM}"
    assert page.post(url, {"reason": "recorded twice"}).status_code == 302
    assert QualityTest.objects.get(pk=test["id"]).withdrawn_reason == "recorded twice"


def test_the_tab_is_off_until_the_programme_turns_it_on(world, client, django_user_model, monkeypatch):
    from connect_labs.supply_chain.navigation import TAB_FOR_VIEW

    assert TAB_FOR_VIEW["supply_chain:tests_download"] == "supply_chain:tests"
    from connect_labs.supply_chain.config import SUPPLY

    assert SUPPLY.defaults["tabs"]["supply_chain:tests"] == {"hidden": True}


def test_a_dispenser_named_with_the_word_is_said_once(world):
    from connect_labs.supply_chain.quality.service import subject_label

    _, item, _, mine, _ = world
    test = QualityTest(kind="dispenser_sample", dispenser="Dispenser WP-04", supply_point=mine)
    assert subject_label(test) == "Dispenser WP-04, tested at Mine Store"
    assert subject_label(QualityTest(kind="dispenser_sample", dispenser="WP-04", supply_point=mine)).startswith(
        "Dispenser WP-04,"
    )
