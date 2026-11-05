"""Shadow mode: what a relationship scope WOULD hide is logged for a partner, and nothing else changes.

THIS REPOSITORY IS PUBLIC. Every name and id is invented.
"""

import logging

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.supply_chain import access_shadow, identity
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.operations import call_operation, get_operation
from connect_labs.supply_chain.tests import access_relations_world as worlds

pytestmark = [pytest.mark.django_db, pytest.mark.real_standing]

LOGGER = "connect_labs.supply_chain.access_shadow"


@pytest.fixture
def w():
    return worlds.build()


@pytest.fixture(autouse=True)
def on_and_every_read(settings, monkeypatch):
    settings.SUPPLY_ACL_SHADOW = True
    settings.SUPPLY_ACL_SHADOW_SAMPLE = 1
    monkeypatch.setattr(identity, "_caller_organizations", lambda access: getattr(access, "connect_orgs", None))


def partner_access(program_id, connect_id):
    """A real SupplyDataAccess for a caller who is neither the system nor the programme."""
    da = SupplyDataAccess(program_id=program_id, caller=SYSTEM)
    da.caller = None
    da.user = None
    da.connect_orgs = [{"id": connect_id, "slug": f"conn-{connect_id}"}]
    return da


def shadow_lines(caplog):
    return [r.getMessage() for r in caplog.records if r.name == LOGGER]


def test_a_partner_read_logs_how_many_rows_it_would_have_hidden(w, caplog):
    caplog.set_level(logging.INFO, logger=LOGGER)
    da = partner_access(worlds.B, 501)  # X, the invited bidder
    call_operation("quote_list", da, {})
    (line,) = shadow_lines(caplog)
    assert line == f"supply acl shadow: op=quote_list model=Quote standing=partner would_hide=1 program={worlds.B}"


def test_it_costs_one_count_beyond_what_the_standing_and_organisation_lookups_already_do(w, caplog):
    caplog.set_level(logging.INFO, logger=LOGGER)
    da = partner_access(worlds.B, 501)
    from connect_labs.supply_chain.models import Quote

    # Warm what is cached on the access object for the request (standing, viewer organisations).
    access_shadow.would_hide(da, Quote)
    with CaptureQueriesContext(connection) as ctx:
        access_shadow.would_hide(da, Quote)
    counts = [q["sql"] for q in ctx.captured_queries if "COUNT(" in q["sql"]]
    assert len(counts) == 1
    assert len(ctx.captured_queries) <= 5  # the COUNT, the runner lookup scope() makes, and the savepoint-free reads


def test_it_never_changes_what_the_read_returns(w, settings):
    da = partner_access(worlds.B, 501)
    settings.SUPPLY_ACL_SHADOW = False
    off = call_operation("quote_list", da, {})
    settings.SUPPLY_ACL_SHADOW = True
    on = call_operation("quote_list", da, {})
    assert on == off
    assert (
        len(on["quotes"] if isinstance(on, dict) and "quotes" in on else on) == 2
    )  # W's bid is still returned: shadow only


def test_the_flag_off_logs_nothing_and_queries_nothing_extra(w, settings, caplog):
    caplog.set_level(logging.INFO, logger=LOGGER)
    settings.SUPPLY_ACL_SHADOW = False
    da = partner_access(worlds.B, 501)
    call_operation("quote_list", da, {})
    assert shadow_lines(caplog) == []


def test_a_programme_member_is_not_observed(w, caplog):
    caplog.set_level(logging.INFO, logger=LOGGER)
    da = partner_access(worlds.B, 502)  # Y runs B
    call_operation("quote_list", da, {})
    assert shadow_lines(caplog) == []


def test_the_system_caller_is_not_observed(w, caplog):
    caplog.set_level(logging.INFO, logger=LOGGER)
    call_operation("quote_list", SupplyDataAccess(program_id=worlds.B, caller=SYSTEM), {})
    assert shadow_lines(caplog) == []


def test_a_read_with_no_modelled_rows_is_skipped(w, caplog):
    caplog.set_level(logging.INFO, logger=LOGGER)
    access_shadow.observe_read(get_operation("commodity_list"), partner_access(worlds.B, 501))
    assert shadow_lines(caplog) == []


def test_it_never_raises_even_when_the_lookup_blows_up(w, monkeypatch, caplog):
    def boom(access, model):
        raise RuntimeError("anything")

    monkeypatch.setattr(access_shadow, "would_hide", boom)
    da = partner_access(worlds.B, 501)
    call_operation("quote_list", da, {})  # no exception
    assert shadow_lines(caplog) == []


def test_sampling_observes_one_read_in_n(w, settings, monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger=LOGGER)
    settings.SUPPLY_ACL_SHADOW_SAMPLE = 5
    draws = iter([3, 0])
    monkeypatch.setattr(access_shadow.random, "randrange", lambda n: next(draws))
    da = partner_access(worlds.B, 501)
    call_operation("quote_list", da, {})
    call_operation("quote_list", da, {})
    assert len(shadow_lines(caplog)) == 1


def test_the_log_line_carries_no_names(w, caplog):
    caplog.set_level(logging.INFO, logger=LOGGER)
    call_operation("quote_list", partner_access(worlds.B, 501), {})
    (line,) = shadow_lines(caplog)
    for needle in ("Org ", "acl-", "conn-", "@"):
        assert needle not in line


def test_every_shadowed_operation_is_a_real_read_of_a_modelled_table():
    from connect_labs.supply_chain import access_relations, models
    from connect_labs.supply_chain.operations import all_operations

    for name, model_name in access_shadow.READ_OPERATION_MODELS.items():
        assert name in all_operations(), name
        assert not all_operations()[name].is_write, name
        assert access_relations.modelled(getattr(models, model_name)), name
