from unittest.mock import MagicMock

from connect_labs.labs.synthetic.image_server import SyntheticImageServer


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


# --------------------------------------------------------------------------------------
# The corpus segment is a PARAMETER, not the literal "muac".
#
# Until 2026-09-05 the blob-id pattern was hardcoded to muac, so nothing but a MUAC photo
# could reach a synthetic visit. KMC's own scale reviewers — scale_validation and
# scale_dial_read, both shipped, both written FOR KMC — therefore had no synthetic data
# to run against, and get_image returned None with no error anywhere to notice it by.
# --------------------------------------------------------------------------------------


def test_scale_blob_ids_resolve_to_scale_stock_files():
    fn = SyntheticImageServer._stock_filename
    assert fn("synth-scale-good-001") == "scale_good_001.jpg"
    assert fn("synth-scale-bad-012") == "scale_bad_012.jpg"
    assert fn("synth-scale-004") == "scale_004.jpg"


def test_muac_mapping_is_byte_for_byte_unchanged():
    """The generalisation must not move the existing corpus by a single character —
    every already-generated opp resolves its photos through these exact names."""
    fn = SyntheticImageServer._stock_filename
    assert fn("synth-muac-003") == "muac_003.jpg"
    assert fn("synth-muac-good-007") == "muac_good_007.jpg"
    assert fn("synth-muac-bad-017") == "muac_bad_017.jpg"


def test_a_malformed_or_empty_corpus_is_still_rejected():
    fn = SyntheticImageServer._stock_filename
    assert fn("nope") is None
    assert fn("synth--001") is None
    assert fn("synth-scale-good-") is None
    assert SyntheticImageServer.is_synthetic_blob("synth-scale-good-001") is True
    assert SyntheticImageServer.is_synthetic_blob("blob-abc") is False
