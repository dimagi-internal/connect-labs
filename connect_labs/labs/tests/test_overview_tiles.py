"""The overview page's project tiles: the Data page gets one, for everyone.

The Data page shows only data the person already holds, so its tile is not a
Dimagi-only tool like Audit or Labs Admin.
"""

from unittest.mock import patch

import pytest
from django.test import RequestFactory

from connect_labs.labs.views import LabsOverviewView
from connect_labs.users.tests.factories import UserFactory


def _tiles(user):
    request = RequestFactory().get("/labs/overview/")
    request.user = user
    request.session = {}
    request.labs_context = {}
    view = LabsOverviewView()
    view.request = request
    view.kwargs = {}
    with (
        patch("connect_labs.labs.views.is_connect_oauth_active", return_value=False),
        patch("connect_labs.labs.views.is_cchq_oauth_active", return_value=False),
        patch("connect_labs.labs.views.is_ocs_oauth_active", return_value=False),
    ):
        return {p["name"]: p for p in view.get_context_data()["labs_projects"]}


@pytest.mark.django_db
@pytest.mark.parametrize("is_dimagi", [True, False])
def test_the_data_page_has_a_tile_for_everyone(is_dimagi):
    with patch("connect_labs.utils.feature_access.is_dimagi_user", return_value=is_dimagi):
        tiles = _tiles(UserFactory())
    assert tiles["Data"]["url"] == "/labs/data/"


@pytest.mark.django_db
def test_a_dimagi_user_sees_eight_tiles():
    with patch("connect_labs.utils.feature_access.is_dimagi_user", return_value=True):
        assert len(_tiles(UserFactory())) == 8
