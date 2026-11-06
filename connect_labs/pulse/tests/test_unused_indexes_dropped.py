"""The never-scanned indexes are gone, and the indexes their columns still need are not.

pulse 0025 and labs 0041 drop ~860 MB of indexes prod never scanned. A field
whose plain index is used keeps it, and only its `_like` twin goes; this pins
both halves, so a later "tidy up" cannot quietly take the one still in use.
"""

import pytest
from django.db import connection

DROPPED = [
    "pulse_pulsework_work_key_057b31b7_like",
    "pulse_pulsework_status_e3c886d9_like",
    "pulse_pulseevent_country_69c3325b_like",
    "pulse_pulseevent_status_fb076bae_like",
    "pulse_pulse_status_3a21e7_idx",
    "pulse_pulsework_country_8ffc2a6a",
    "pulse_pulsework_country_8ffc2a6a_like",
    "pulse_pulsework_payment_date_c95d5290",
    "labs_computed_visit_cache_visit_id_183830fe",
    "labs_computed_visit_cache_username_e9d6b7bb",
    "labs_computed_visit_cache_username_e9d6b7bb_like",
    "labs_computed_entity_cache_entity_id_899a79b5",
    "labs_computed_entity_cache_entity_id_899a79b5_like",
    "labs_computed_entity_cache_username_8b380791",
    "labs_computed_entity_cache_username_8b380791_like",
    "labs_computed_entity_cache_config_hash_ea7e7134",
    "labs_computed_entity_cache_config_hash_ea7e7134_like",
]

# Still scanned on prod: the reason the drops above are surgical.
KEPT = {
    "pulse_pulsework": ["(work_key)", "(status)"],
    "pulse_pulseevent": ["(country)", "(status)"],
    "labs_computed_visit_cache": ["(opportunity_id, pipeline_id, config_hash, visit_id)"],
}


def _indexes():
    with connection.cursor() as cur:
        cur.execute("select indexname, tablename, indexdef from pg_indexes where schemaname = 'public'")
        return cur.fetchall()


@pytest.mark.django_db
def test_never_scanned_indexes_are_dropped():
    names = {name for name, _, _ in _indexes()}
    assert not names & set(DROPPED), sorted(names & set(DROPPED))


@pytest.mark.django_db
def test_indexes_still_in_use_survive():
    rows = _indexes()
    for table, columns in KEPT.items():
        defs = [d for _, t, d in rows if t == table]
        for column in columns:
            assert any(d.endswith(column) for d in defs), (table, column, defs)
