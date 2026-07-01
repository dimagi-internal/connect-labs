from unittest.mock import MagicMock, patch

from connect_labs.pages.data_access import SurfaceDataAccess


def _fake_record(**data):
    rec = MagicMock()
    rec.id = 77
    rec.data = data
    return rec


@patch("connect_labs.pages.data_access.LabsRecordAPIClient")
def test_get_surface_by_slug_returns_normalized_dict(mock_client_cls):
    client = mock_client_cls.return_value
    client.get_records.return_value = [
        _fake_record(
            slug="prog-25-hub",
            title="Program 25 Hub",
            cards=[{"provider": "audit", "target": {"opportunity_id": 1}}],
            options={},
        )
    ]

    da = SurfaceDataAccess(access_token="tok", program_id=25)
    surface = da.get_surface_by_slug("prog-25-hub")

    assert surface["id"] == 77
    assert surface["slug"] == "prog-25-hub"
    assert surface["title"] == "Program 25 Hub"
    assert surface["cards"][0]["provider"] == "audit"
    client.get_records.assert_called_once()
    kwargs = client.get_records.call_args.kwargs
    assert kwargs["type"] == "surface"
    assert kwargs["public"] is True
    assert kwargs["data__slug"] == "prog-25-hub"


@patch("connect_labs.pages.data_access.LabsRecordAPIClient")
def test_get_surface_by_slug_returns_none_when_missing(mock_client_cls):
    mock_client_cls.return_value.get_records.return_value = []
    da = SurfaceDataAccess(access_token="tok")
    assert da.get_surface_by_slug("nope") is None


@patch("connect_labs.pages.data_access.LabsRecordAPIClient")
def test_create_surface_posts_public_scoped_record(mock_client_cls):
    client = mock_client_cls.return_value
    client.create_record.return_value = _fake_record(slug="s", title="T", cards=[], options={})

    da = SurfaceDataAccess(access_token="tok", program_id=25)
    da.create_surface(slug="s", title="T", cards=[])

    kwargs = client.create_record.call_args.kwargs
    assert kwargs["type"] == "surface"
    assert kwargs["experiment"] == "25"
    assert kwargs["program_id"] == 25
    assert kwargs["public"] is True
    assert kwargs["data"]["slug"] == "s"


@patch("connect_labs.pages.data_access.LabsRecordAPIClient")
def test_update_surface_patches_public_scoped_record(mock_client_cls):
    client = mock_client_cls.return_value
    client.update_record.return_value = _fake_record(slug="s", title="T", cards=[{"id": 1}], options={})

    da = SurfaceDataAccess(access_token="tok", program_id=25)
    da.update_surface(record_id=99, slug="s", title="T", cards=[{"id": 1}])

    kwargs = client.update_record.call_args.kwargs
    assert kwargs["record_id"] == 99
    assert kwargs["type"] == "surface"
    assert kwargs["program_id"] == 25
    assert kwargs["public"] is True
    assert kwargs["data"]["slug"] == "s"
    assert kwargs["data"]["cards"] == [{"id": 1}]
    assert client.update_record.call_args.kwargs["experiment"] == "25"


@patch("connect_labs.pages.data_access.LabsRecordAPIClient")
def test_get_surface_by_slug_is_deterministic_on_collision(mock_client_cls):
    client = mock_client_cls.return_value
    high = _fake_record(slug="dup", title="High", cards=[], options={})
    high.id = 90
    low = _fake_record(slug="dup", title="Low", cards=[], options={})
    low.id = 12
    client.get_records.return_value = [high, low]

    da = SurfaceDataAccess(access_token="tok")
    surface = da.get_surface_by_slug("dup")

    assert surface["id"] == 12
