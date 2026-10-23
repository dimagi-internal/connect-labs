"""Migration 0033 keeps every saved row's meaning as Labs Admin's table of ticks."""

import importlib
from types import SimpleNamespace

MIGRATION = importlib.import_module("connect_labs.labs.migrations.0033_canopy_agent_settings_from_site_agents")


def test_each_old_setting_becomes_its_ticks():
    row = SimpleNamespace(
        site_agent="ace",
        data_agent="labsdata",
        phi_agents=["deep-bot"],
        offered_agents=["coach-bot", "ace"],
    )
    assert MIGRATION._ticks(row) == [
        {"slug": "ace", "offered": True, "real_data": False},
        {"slug": "labsdata", "offered": True, "real_data": False},
        {"slug": "coach-bot", "offered": True, "real_data": False},
        {"slug": "deep-bot", "offered": True, "real_data": True},
    ]


def test_prods_row_today_maps_to_ace_by_default_and_labsdata_shown_without_real_data():
    row = SimpleNamespace(site_agent="ace", data_agent="labsdata", phi_agents=[], offered_agents=[])
    assert MIGRATION._ticks(row) == [
        {"slug": "ace", "offered": True, "real_data": False},
        {"slug": "labsdata", "offered": True, "real_data": False},
    ]
