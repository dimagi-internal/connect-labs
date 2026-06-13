from commcare_connect.labs.synthetic.generator.fixtures.opportunity import build_opportunity


def test_build_opportunity_passes_through_known_keys():
    detail = {
        "id": 1237,
        "name": "Demo Opportunity",
        "organization": "Acme",
        "currency": "USD",
    }
    out = build_opportunity(detail, opportunity_name_override="Pretty Name")
    assert out["id"] == 1237
    assert out["name"] == "Pretty Name"
    assert out["organization"] == "Acme"
    assert out["currency"] == "USD"


def test_build_opportunity_defaults_missing_fields():
    out = build_opportunity({"id": 1, "name": "X"})
    assert "currency" in out
    assert "organization" in out
