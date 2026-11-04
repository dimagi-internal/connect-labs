"""An award's reason is required only above an amount the programme sets (Sophie's RUTF feedback, Sourcing).

Invented names and figures only (public repo).
"""

import pytest

from connect_labs.scope_config.models import ScopeConfig
from connect_labs.supply_chain import award_policy
from connect_labs.supply_chain.award_policy import AwardReasonRequired
from connect_labs.supply_chain.config import SUPPLY
from connect_labs.supply_chain.tests import chlorine_world
from connect_labs.supply_chain.tests.chlorine_world import PROGRAM, _compare_url, op

pytestmark = pytest.mark.django_db

da = chlorine_world.da
account = chlorine_world.account
client_in_programme = chlorine_world.client_in_programme
world = chlorine_world.world


def set_limit(value):
    ScopeConfig.objects.update_or_create(
        scope_type="program",
        scope_key=str(PROGRAM),
        namespace=SUPPLY.key,
        defaults={"data": {award_policy.KEY: value}},
    )


@pytest.fixture
def second_quote(da, world):
    other = op(da, "supplier_create", data={"name": "Kano Traders"})
    return op(
        da,
        "quote_record",
        data={
            "tender_id": world["tender"]["id"],
            "commodity_slug": "chlorine",
            "supplier_id": other["id"],
            "as_quoted_amount": "5.00",
            "as_quoted_unit": "per_pack",
            "quantity_basis": "600",
            "quantity_basis_unit": "jerry_can",
            "pack_spec_source": "stated_on_quote",
            "base_per_pack_stated": 20,
            "freight_basis": "included",
            "duties_basis": "included",
        },
    )


@pytest.fixture
def uncomputable_quote(da, world):
    """A quote that states nothing a total can be worked out from."""
    other = op(da, "supplier_create", data={"name": "Vague Traders"})
    return op(
        da,
        "quote_record",
        data={"tender_id": world["tender"]["id"], "commodity_slug": "chlorine", "supplier_id": other["id"]},
    )


def award(da, world, quote, **extra):
    return op(da, "award_create", tender_id=world["tender"]["id"], quote_id=quote["id"], **extra)


def test_no_limit_means_a_reason_is_never_needed(da, world, second_quote):
    assert award(da, world, second_quote)["rationale"] == ""


def test_above_the_limit_a_reason_is_needed(da, world, second_quote):
    set_limit(100)
    with pytest.raises(AwardReasonRequired, match="asks for a reason above 100"):
        award(da, world, second_quote)
    assert award(da, world, second_quote, rationale="lowest delivered price")["rationale"] == "lowest delivered price"


def test_below_the_limit_it_is_still_optional(da, world, second_quote):
    set_limit(1_000_000)
    assert award(da, world, second_quote)["rationale"] == ""


def test_a_value_we_cannot_work_out_asks_for_a_reason_when_a_limit_is_set(da, world, uncomputable_quote):
    assert award(da, world, uncomputable_quote)["rationale"] == ""
    set_limit(1_000_000)
    with pytest.raises(AwardReasonRequired, match="cannot work out"):
        award(da, world, uncomputable_quote)


def test_the_page_says_so_and_refuses_with_the_reason(client_in_programme, da, world, second_quote):
    set_limit(100)
    page = client_in_programme.get(_compare_url(world)).content.decode()
    assert 'data-testid="award-reason-required"' in page and "needed above 100" in page
    response = client_in_programme.post(_compare_url(world), {"quote_id": second_quote["id"], "rationale": ""})
    assert response.status_code == 200 and "asks for a reason above 100" in response.content.decode()
    done = client_in_programme.post(
        _compare_url(world), {"quote_id": second_quote["id"], "rationale": "lowest delivered price"}
    )
    assert done.status_code == 302


def test_the_setting_is_a_valid_supply_setting():
    from connect_labs.scope_config.namespaces import validate_layer

    validate_layer(SUPPLY, {award_policy.KEY: 5000})
    with pytest.raises(ValueError):
        validate_layer(SUPPLY, {award_policy.KEY: -1})
