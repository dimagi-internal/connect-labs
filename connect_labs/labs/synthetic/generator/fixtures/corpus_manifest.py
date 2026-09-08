"""Load a stock-image corpus's own manifest.

Every corpus under ``stock-images/`` ships a ``manifest.json`` describing itself,
mirrored here under ``corpora/``. Both corpora answer the SAME questions in the
same shape -- what is measured, which reviewer judges it, what the pools mean,
and whether there is per-image ground truth -- so the generator can consume a
new corpus without special-casing it.

Why this exists: ``ImageConfig.readings`` is an inline dict on the opportunity
manifest. That is fine for a handful of values and unusable for a real corpus --
the scale corpus has 83 of them, and pasting those into every opp manifest makes
each copy free to drift from the images on Drive. The corpus is the authority on
its own contents; an opp manifest should only have to name it.

``ground_truth.kind`` is the field that decides how a corpus may be used:

- ``none``           -- the reviewer judges the picture alone (MUAC). There is no
                        value to compare, so weight-matching is meaningless here.
- ``reading_per_image`` -- the reviewer compares a typed value against the photo
                        (scale). The entered value MUST come from this manifest.
"""

from __future__ import annotations

import functools
import json
import logging
import pathlib

logger = logging.getLogger(__name__)

_CORPORA_DIR = pathlib.Path(__file__).parent / "corpora"

GROUND_TRUTH_NONE = "none"
GROUND_TRUTH_PER_IMAGE = "reading_per_image"


class CorpusManifestError(Exception):
    """Raised when a corpus manifest is missing or unusable."""


@functools.cache
def load_corpus(corpus: str) -> dict:
    """Return the manifest for ``corpus``, or raise CorpusManifestError.

    Raises rather than returning {} on a missing corpus: a silent empty manifest
    would generate an opportunity with no images and no explanation, which is the
    failure mode this whole area keeps producing.
    """
    path = _CORPORA_DIR / f"{corpus}.json"
    if not path.exists():
        available = sorted(p.stem for p in _CORPORA_DIR.glob("*.json"))
        raise CorpusManifestError(f"no manifest for corpus {corpus!r}; available: {available}")
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        raise CorpusManifestError(f"corpus {corpus!r} manifest is not valid JSON: {e}") from e
    if data.get("corpus") != corpus:
        raise CorpusManifestError(f"corpus manifest at {path.name} declares corpus={data.get('corpus')!r}")
    return data


def readings_for(corpus: str) -> dict[str, float]:
    """{blob_id: reading} for a corpus, empty when it carries no ground truth."""
    man = load_corpus(corpus)
    if man["ground_truth"]["kind"] == GROUND_TRUTH_NONE:
        return {}
    return {
        img["blob_id"]: img["reading_grams"] for img in man["images"].values() if img.get("reading_grams") is not None
    }


def bands_for(corpus: str) -> dict[str, list[float]]:
    """{blob_id: [lo, hi]} for images whose reviewer accepts a RANGE, not a point.

    Only dial-type scale images have one. A value intended to FAIL review has to
    fall outside this band -- differing from the recorded midpoint is not enough,
    because the midpoint sits at the centre of a 500-600 g window (see #1563).
    """
    man = load_corpus(corpus)
    return {img["blob_id"]: img["match_band_grams"] for img in man["images"].values() if img.get("match_band_grams")}


def pool_sizes(corpus: str) -> tuple[int, int]:
    """(good_count, bad_count) as the corpus itself reports them."""
    pools = load_corpus(corpus)["pools"]
    return pools["good"]["count"], pools["bad"]["count"]


def trajectories(corpus: str) -> dict[str, list[dict]]:
    """{subject: [{visit_seq, file, blob_id, reading_grams}, ...]} in visit order.

    Empty for a cross-sectional corpus. A generator that wants a realistic weight
    series can either match its own curve against ``readings_for`` or replay one
    of these real series directly.
    """
    return load_corpus(corpus)["trajectories"].get("series", {})


def requires_ground_truth(corpus: str) -> bool:
    """Does this corpus's reviewer compare a typed value against the photo?"""
    return load_corpus(corpus)["ground_truth"]["kind"] == GROUND_TRUTH_PER_IMAGE
