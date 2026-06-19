from commcare_connect.labs.synthetic.bundle import read_bundle, scrub_opportunity, write_bundle


def test_scrub_drops_row_level_lists():
    detail = {"id": 523, "name": "KMC", "payment_units": [{"id": 1}], "deliver_units": [{"id": 2}], "flws": [{"x": 1}]}
    scrubbed = scrub_opportunity(detail)
    assert scrubbed["name"] == "KMC"
    assert "flws" not in scrubbed
    # payment_units / deliver_units are program config and may be kept:
    assert "payment_units" in scrubbed


def test_write_then_read_roundtrip(tmp_path):
    bundle = write_bundle(
        tmp_path,
        523,
        manifest_yaml="opportunity_id: 10000\n",
        app_structure={"learn_app": None, "deliver_app": {"modules": []}},
        opportunity={"id": 523, "name": "KMC"},
    )
    loaded = read_bundle(bundle)
    assert loaded.source_opp_id == 523
    assert loaded.app_structure["deliver_app"] == {"modules": []}
    assert "opportunity_id: 10000" in loaded.manifest_yaml
