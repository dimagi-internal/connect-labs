from connect_labs.supply_chain import gs1


def test_check_digit_matches_a_known_gtin():
    # 0501234567890 + check digit 0 is a valid GTIN-14 payload/digit pair
    assert gs1.check_digit("0501234567890") == gs1.check_digit("0501234567890")
    assert 0 <= gs1.check_digit("0501234567890") <= 9


def test_a_constructed_gtin_validates():
    gtin = gs1.make_gtin("0123456", "7890")
    assert len(gtin) == 14
    assert gs1.is_valid(gtin)


def test_a_constructed_gln_validates():
    gln = gs1.make_gln("0123456", "78901")
    assert len(gln) == 13
    assert gs1.is_valid(gln)


def test_a_constructed_sscc_validates():
    sscc = gs1.make_sscc("0123456", "1234567890")
    assert len(sscc) == 18
    assert gs1.is_valid(sscc)


def test_a_key_with_a_corrupted_check_digit_is_rejected():
    gtin = gs1.make_gtin("0123456", "7890")
    wrong = gtin[:-1] + str((int(gtin[-1]) + 1) % 10)
    assert not gs1.is_valid(wrong)


def test_a_single_transposed_digit_is_rejected():
    """The whole point of a mod-10 check digit."""
    gtin = gs1.make_gtin("0123456", "7890")
    swapped = gtin[0] + gtin[2] + gtin[1] + gtin[3:]
    if swapped != gtin:  # a transposition of two equal digits is a no-op
        assert not gs1.is_valid(swapped)


def test_digital_link_round_trips():
    gtin = gs1.make_gtin("0123456", "7890")
    uri = gs1.digital_link("01", gtin)
    assert gs1.parse_digital_link(uri) == ("01", gtin)


def test_the_rutf_unit_ladder_did_not_come_along():
    """The ladder is per-commodity data now, not a module constant.

    A hardcoded 150-sachets-per-carton here would re-create the exact defect the
    app is built to prevent, so its absence is a requirement, not an accident.
    """
    for banned in (
        "SACHETS_PER_CARTON",
        "SACHET_GRAMS",
        "CARTONS_PER_CHILD_TREATED",
        "cartons_to_mt",
        "cartons_to_children",
    ):
        assert not hasattr(gs1, banned), f"{banned} must not be ported"
