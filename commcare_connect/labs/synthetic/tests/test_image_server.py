from unittest.mock import MagicMock

from commcare_connect.labs.synthetic.image_server import SyntheticImageServer


def test_resolve_blob_id():
    server = SyntheticImageServer.__new__(SyntheticImageServer)
    server._cache = {}
    server._drive = None
    server._stock_folder_id = None

    assert server._stock_filename("synth-muac-003") == "muac_003.jpg"
    assert server._stock_filename("synth-muac-015") == "muac_015.jpg"
    # Pooled forms map to the new corpus filenames.
    assert server._stock_filename("synth-muac-good-001") == "muac_good_001.jpg"
    assert server._stock_filename("synth-muac-good-008") == "muac_good_008.jpg"
    assert server._stock_filename("synth-muac-bad-001") == "muac_bad_001.jpg"
    assert server._stock_filename("synth-muac-bad-013") == "muac_bad_013.jpg"
    # Invalid pool tags do not match.
    assert server._stock_filename("synth-muac-other-001") is None
    assert server._stock_filename("real-blob-id") is None


def test_is_synthetic_blob():
    assert SyntheticImageServer.is_synthetic_blob("synth-muac-001") is True
    assert SyntheticImageServer.is_synthetic_blob("synth-muac-015") is True
    assert SyntheticImageServer.is_synthetic_blob("synth-muac-good-001") is True
    assert SyntheticImageServer.is_synthetic_blob("synth-muac-bad-013") is True
    assert SyntheticImageServer.is_synthetic_blob("synth-muac-other-001") is False
    assert SyntheticImageServer.is_synthetic_blob("real-blob-abc123") is False
    assert SyntheticImageServer.is_synthetic_blob("") is False


def test_serve_from_cache():
    server = SyntheticImageServer.__new__(SyntheticImageServer)
    server._cache = {"synth-muac-001": b"fake-jpeg-bytes"}
    server._drive = MagicMock()
    server._stock_folder_id = "folder123"

    result = server.get_image("synth-muac-001")

    assert result == b"fake-jpeg-bytes"
    server._drive.download_file.assert_not_called()
