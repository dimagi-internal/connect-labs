from connect_labs.campaign.services import roles


def test_role_mapping_roundtrip():
    assert roles.to_short("campaign_admin") == "admin"
    assert roles.to_key("admin") == "campaign_admin"
    assert roles.to_short("operations_manager") == "operations"
    assert roles.to_key("reporting") == "reporting_user"
    for key in ["campaign_admin", "payment_admin", "compliance_admin", "operations_manager", "reporting_user"]:
        assert roles.to_key(roles.to_short(key)) == key
