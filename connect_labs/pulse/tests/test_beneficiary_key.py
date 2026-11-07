"""Counting people without storing them: the beneficiary label and its keyed hash.

Label shapes are the real formats measured on production 2026-11-07 (names and
numbers invented): MBW Nigeria appends the visit to the label, MBW cohort and KMC
use name + phone, CHC uses name + birth date.
"""

import pytest

from connect_labs.pulse.normalize import beneficiary_key_for, beneficiary_label, work_to_fields


@pytest.mark.parametrize(
    "entity_id, label",
    [
        ("Sa,adatu Yakubu - 8037760312 - 1 month visit", "sa,adatu yakubu - 8037760312"),
        ("Sa,adatu Yakubu - 8037760312 - postnatal visit", "sa,adatu yakubu - 8037760312"),
        ("Sa,adatu  Yakubu - 8037760312", "sa,adatu yakubu - 8037760312"),
        ("Amina Bello08031234567", "amina bello08031234567"),
        ("Musa Ali2023-05-12", "musa ali2023-05-12"),
        ("Halima Musa - -", "halima musa"),
        (None, ""),
    ],
)
def test_label_keeps_the_person_and_drops_the_visit(entity_id, label):
    assert beneficiary_label(entity_id) == label


def _work(entity_id, opp=765, pu=994):
    return {"opportunity_id": opp, "username": "w1", "entity_id": entity_id, "payment_unit_id": pu}


def test_every_visit_of_one_mother_shares_a_key():
    a = beneficiary_key_for(_work("Sa,adatu Yakubu - 8037760312 - 1 month visit"))
    b = beneficiary_key_for(_work("Sa,adatu Yakubu - 8037760312 - 3 month visit", pu=995))
    assert a == b and len(a) == 64


def test_different_people_and_different_opportunities_get_different_keys():
    a = beneficiary_key_for(_work("Sa,adatu Yakubu - 8037760312"))
    assert a != beneficiary_key_for(_work("Sa,adatu Yakubu - 8037760313"))
    assert a != beneficiary_key_for(_work("Sa,adatu Yakubu - 8037760312", opp=627))


def test_key_is_keyed_not_a_plain_hash_and_holds_no_label():
    import hashlib

    entity = "Sa,adatu Yakubu - 8037760312"
    key = beneficiary_key_for(_work(entity))
    assert key != hashlib.sha256(f"765\x1f{beneficiary_label(entity)}".encode()).hexdigest()
    assert "8037760312" not in key


def test_a_work_with_no_label_has_no_key():
    assert beneficiary_key_for(_work("")) == ""


def test_work_fields_carry_the_key_and_never_the_label():
    fields = work_to_fields(
        {**_work("Sa,adatu Yakubu - 8037760312 - 1 month visit"), "date_created": "2026-07-28T13:32:40Z"}
    )
    assert fields["beneficiary_key"] == beneficiary_key_for(_work("Sa,adatu Yakubu - 8037760312"))
    assert not any("8037760312" in str(v) or "Yakubu" in str(v) for v in fields.values())
