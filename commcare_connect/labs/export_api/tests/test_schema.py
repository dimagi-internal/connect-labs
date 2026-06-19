"""OpenAPI schema coverage for the /api/export/ surface.

Plain passthrough APIViews are dropped from the schema by drf-spectacular; the
@extend_schema annotations + auth extension on the views keep them documented so
an external consumer can read /api/docs/.
"""
import pytest


def _schema():
    from drf_spectacular.generators import SchemaGenerator

    return SchemaGenerator().get_schema(request=None, public=True)


@pytest.mark.django_db
def test_export_paths_documented():
    paths = _schema()["paths"]
    for p in (
        "/api/export/opp_org_program_list/",
        "/api/export/opportunities/",
        "/api/export/opportunity/{opportunity_id}/",
        "/api/export/opportunity/{opportunity_id}/user_visits/",
        "/api/export/opportunity/{opportunity_id}/user_data/",
        "/api/export/opportunity/{opportunity_id}/completed_works/",
        "/api/export/opportunity/{opportunity_id}/completed_module/",
        "/api/export/opportunity/{opportunity_id}/app_structure/",
        "/api/export/opportunity/{opportunity_id}/payment/",
        "/api/export/opportunity/{opportunity_id}/invoice/",
        "/api/export/opportunity/{opportunity_id}/assessment/",
    ):
        assert p in paths, f"{p} missing from OpenAPI schema"


@pytest.mark.django_db
def test_pat_security_scheme_registered():
    schemes = _schema()["components"]["securitySchemes"]
    assert "MCPToken" in schemes
    assert schemes["MCPToken"]["type"] == "http"
    assert schemes["MCPToken"]["scheme"] == "bearer"


@pytest.mark.django_db
def test_app_structure_documents_app_type_param():
    op = _schema()["paths"]["/api/export/opportunity/{opportunity_id}/app_structure/"]["get"]
    param_names = {p["name"] for p in op.get("parameters", [])}
    assert "app_type" in param_names
