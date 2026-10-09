"""AuditDataAccess.download_image_from_connect on a synthetic (``synth-*``) blob.

A synthetic blob exists only in the labs stock-image Drive folder, never on
production Connect. The audit page's image view already knew that and served it
from the image server, but the AI reviewer fetches through this method and so
asked Connect for every one. Each fetch failed, every image was skipped as
``image_download_failed``, and the run "succeeded" with an empty audit
(job 8499 on opp 10042, 2026-10-09: 47 of 47 skipped -- #2393).
"""

from unittest.mock import Mock

import pytest

from connect_labs.audit.data_access import AuditDataAccess, ImageDownloadError
from connect_labs.labs.synthetic import image_server


def _make_data_access():
    da = object.__new__(AuditDataAccess)
    da.http_client = Mock()
    da.production_url = "https://connect.example"
    return da


class _FakeImageServer:
    def __init__(self, images):
        self._images = images
        self.requested = []

    def get_image(self, blob_id):
        self.requested.append(blob_id)
        return self._images.get(blob_id)


@pytest.fixture
def fake_server(monkeypatch):
    server = _FakeImageServer({"synth-kmc-scale-good-083": b"STOCKJPEG"})
    monkeypatch.setattr(image_server, "get_image_server", lambda: server)
    return server


def test_synthetic_blob_is_served_from_the_image_server_not_connect(fake_server):
    da = _make_data_access()

    assert da.download_image_from_connect("synth-kmc-scale-good-083", 10042) == b"STOCKJPEG"
    assert fake_server.requested == ["synth-kmc-scale-good-083"]
    da.http_client.get.assert_not_called()


def test_unresolvable_synthetic_blob_is_a_404_not_a_connect_call(fake_server):
    da = _make_data_access()

    with pytest.raises(ImageDownloadError) as exc:
        da.download_image_from_connect("synth-kmc-scale-good-999", 10042)
    assert exc.value.status_code == 404
    da.http_client.get.assert_not_called()


def test_a_real_blob_still_goes_to_connect(fake_server):
    da = _make_data_access()
    resp = Mock(content=b"REALJPEG")
    resp.raise_for_status = Mock(return_value=None)
    da.http_client.get.return_value = resp

    assert da.download_image_from_connect("3f2a9c1e-real-blob", 10042) == b"REALJPEG"
    assert fake_server.requested == []


def test_an_undeclared_corpus_is_not_treated_as_synthetic(fake_server):
    """The blob grammar is checked against corpora/*.json, so a lookalike id goes to Connect."""
    da = _make_data_access()
    resp = Mock(content=b"REALJPEG")
    resp.raise_for_status = Mock(return_value=None)
    da.http_client.get.return_value = resp

    assert da.download_image_from_connect("synth-nosuchcorpus-001", 10042) == b"REALJPEG"
    assert fake_server.requested == []
