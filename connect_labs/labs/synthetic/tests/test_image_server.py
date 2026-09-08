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


def test_kmc_scale_blob_ids_resolve_to_kmc_scale_stock_files():
    fn = SyntheticImageServer._stock_filename
    assert fn("synth-kmc-scale-good-001") == "kmc-scale_good_001.jpg"
    assert fn("synth-kmc-scale-bad-012") == "kmc-scale_bad_012.jpg"
    assert fn("synth-kmc-scale-004") == "kmc-scale_004.jpg"


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
    assert fn("synth-kmc-scale-good-") is None
    assert SyntheticImageServer.is_synthetic_blob("synth-kmc-scale-good-001") is True
    assert SyntheticImageServer.is_synthetic_blob("blob-abc") is False


# ---------------------------------------------------------------------------
# Per-corpus subfolders
#
# The stock area was always one-folder-per-corpus (the parent is named
# "stock-images" and held a single "muac" child); this server flattened it by
# resolving every corpus out of ONE folder by filename prefix, so a second
# corpus had to be poured into the folder named after the first. Both layouts
# must work, so the folder move and the settings change can land separately.
# ---------------------------------------------------------------------------


class _FakeDrive:
    def __init__(self, listings):
        self.listings = listings
        self.downloads = []

    def list_folder(self, folder_id):
        return self.listings.get(folder_id, {})

    def download_file(self, file_id):
        self.downloads.append(file_id)
        return b"bytes"


def _server(listings, root):
    from connect_labs.labs.synthetic.image_server import SyntheticImageServer

    s = SyntheticImageServer.__new__(SyntheticImageServer)
    s._drive = _FakeDrive(listings)
    s._stock_folder_id = root
    s._cache = {}
    s._folder_listing = None
    s._corpus_folders = {}
    s._corpus_listings = {}
    return s


def test_nested_layout_resolves_each_corpus_from_its_own_subfolder():
    listings = {
        "root": {"muac": "muacF", "kmc-scale": "ksF"},
        "muacF": {"muac_good_001.jpg": "m1"},
        "ksF": {"kmc-scale_good_001.jpg": "s1"},
    }
    s = _server(listings, "root")
    assert s.get_image("synth-kmc-scale-good-001") == b"bytes"
    assert s._drive.downloads == ["s1"]
    assert s.get_image("synth-muac-good-001") == b"bytes"
    assert s._drive.downloads == ["s1", "m1"]


def test_flat_layout_still_resolves_the_historical_settings_value():
    """Setting still pointed at the muac folder itself: no 'muac' child, flat lookup."""
    listings = {"muacF": {"muac_good_001.jpg": "m1"}}
    s = _server(listings, "muacF")
    assert s.get_image("synth-muac-good-001") == b"bytes"
    assert s._drive.downloads == ["m1"]


def test_a_corpus_without_its_own_folder_does_not_borrow_anothers_images():
    """kmc-scale_* must not be served out of the muac folder just because it is configured."""
    listings = {"muacF": {"muac_good_001.jpg": "m1"}}
    s = _server(listings, "muacF")
    assert s.get_image("synth-kmc-scale-good-001") is None


# ---------------------------------------------------------------------------
# Hyphenated corpus ids
#
# "kmc-scale" puts a hyphen inside the corpus segment, which is also the
# delimiter between segments. A GREEDY character class including "-" reads
# synth-kmc-scale-good-001 as corpus="kmc-scale-good" with no pool, and then
# resolves the wrong filename with no error anywhere -- so the lazy match is
# load-bearing, not cosmetic.
# ---------------------------------------------------------------------------


def test_a_hyphenated_corpus_does_not_swallow_the_pool_segment():
    from connect_labs.labs.synthetic.image_server import SyntheticImageServer

    fn = SyntheticImageServer._stock_filename
    assert fn("synth-kmc-scale-good-001") == "kmc-scale_good_001.jpg"
    assert fn("synth-kmc-scale-bad-010") == "kmc-scale_bad_010.jpg"
    # legacy uncategorised form, hyphenated corpus
    assert fn("synth-kmc-scale-004") == "kmc-scale_004.jpg"
    # single-word corpora keep working exactly as before
    assert fn("synth-muac-good-001") == "muac_good_001.jpg"
    assert fn("synth-muac-003") == "muac_003.jpg"


def test_hyphenated_corpus_resolves_from_its_own_subfolder():
    listings = {
        "root": {"muac": "muacF", "kmc-scale": "ksF"},
        "muacF": {"muac_good_001.jpg": "m1"},
        "ksF": {"kmc-scale_good_001.jpg": "k1"},
    }
    s = _server(listings, "root")
    assert s.get_image("synth-kmc-scale-good-001") == b"bytes"
    assert s._drive.downloads == ["k1"]


def test_a_corpus_id_may_not_start_with_a_hyphen():
    from connect_labs.labs.synthetic.image_server import SyntheticImageServer

    assert SyntheticImageServer._stock_filename("synth--good-001") is None
