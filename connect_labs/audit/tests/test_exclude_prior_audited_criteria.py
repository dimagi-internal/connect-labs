from connect_labs.audit.data_access import AuditCriteria


def test_defaults_to_false():
    assert AuditCriteria.from_dict({}).exclude_prior_audited is False


def test_parses_snake_case():
    assert AuditCriteria.from_dict({"exclude_prior_audited": True}).exclude_prior_audited is True


def test_parses_camel_case():
    assert AuditCriteria.from_dict({"excludePriorAudited": True}).exclude_prior_audited is True
