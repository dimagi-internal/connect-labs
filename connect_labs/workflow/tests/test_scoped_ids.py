"""A labs-only id the context middleware dropped must not come back through ?opportunity_id=."""

from unittest.mock import patch

from django.test import RequestFactory

from connect_labs.workflow import views


def _req(qs, context=None):
    req = RequestFactory().get("/x/", qs)
    req.labs_context = context or {}
    return req


def test_a_url_id_the_context_check_refuses_reads_as_absent():
    with patch.object(views, "validate_context_access", return_value={}):
        assert views._scoped_opportunity_id(_req({"opportunity_id": "10077"})) is None
        assert views._scoped_program_id(_req({"program_id": "10088"})) is None


def test_a_url_id_the_context_check_accepts_is_used():
    with patch.object(views, "validate_context_access", return_value={"opportunity_id": 77}):
        assert views._scoped_opportunity_id(_req({"opportunity_id": "77"})) == 77


def test_the_checked_context_wins_without_another_check():
    with patch.object(views, "validate_context_access") as check:
        assert views._scoped_opportunity_id(_req({"opportunity_id": "5"}, {"opportunity_id": 9})) == 9
    check.assert_not_called()
