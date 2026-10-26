"""What kind of value a form field holds, and the setting that kind gets by default.

The rules were the synthetic profiler's (``labs/synthetic/generator/fixtures/profiler.py``,
which still imports them from here): a field is an identifier by a WHOLE segment of its
leaf name (``mother_name``, ``phone``; never "nin" inside "positioning"), or by its values
(a uuid, a run of seven or more digits), and free text by its values (too many distinct
ones, or a long token). Everything that is not one of those is judged on what was seen:
dates, numbers, a closed set of categories.

``classify`` never needs to keep a value: it takes a ``Counter`` of what was seen and
returns a kind. Callers that store the result store the kind, never the counter.
"""

from __future__ import annotations

import datetime as dt
import re
from collections import Counter

# ---------------------------------------------------------------------------
# The profiler's rules (moved here so the cloner and the export share them)
# ---------------------------------------------------------------------------

#: A field with more distinct values than this is not a closed list of choices.
MAX_CATEGORY_VALUES = 50

#: A category value shaped like an identifier is refused however few distinct values
#: there are: a uuid (a case id) or a long run of digits (a phone or national id).
IDENTIFIER_VALUE_RE = re.compile(
    r"^(?:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|\+?\d{7,})$", re.IGNORECASE
)
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)

#: A replayable (and, here, categorical) field must be genuinely enumerable.
MAX_REPLAYABLE_CARDINALITY = 25
MAX_REPLAYABLE_VALUE_LEN = 40
MIN_REPLAYABLE_OBSERVATIONS = 5

#: Leaf-name tokens that mark a direct identifier, matched on whole segments.
IDENTIFIER_LEAF_TOKENS = frozenset(
    {
        "name",
        "names",
        "phone",
        "mobile",
        "tel",
        "address",
        "email",
        "gps",
        "coordinate",
        "coordinates",
        "latitude",
        "longitude",
        "lat",
        "lon",
        "nin",
        "bvn",
        "id",
        "ids",
        "uuid",
        "contact",
        "surname",
        "guardian",
        "signature",
        "photo",
        "image",
        "comment",
        "comments",
        "remark",
        "remarks",
        "note",
        "notes",
        "specify",
        "description",
        "other",
    }
)

#: Submission-envelope paths: CommCare transport metadata, not anything the form asked.
ENVELOPE_PREFIXES = ("metadata.", "form.meta.", "form.@", "form.#", "form.case.@")
ENVELOPE_EXACT = frozenset(
    {
        "received_on",
        "server_modified_on",
        "indexed_on",
        "edited_on",
        "archived",
        "version",
        "uiversion",
        "type",
        "doc_type",
        "id",
        "build_id",
        "app_id",
        "xmlns",
        "form_name",
        "state",
        "partial_submission",
        "is_phone_submission",
        "initial_processing_complete",
    }
)


def leaf_segments(path: str) -> set[str]:
    leaf = path.rsplit(".", 1)[-1].lower()
    return {seg for seg in re.split(r"[^a-z0-9]+", leaf) if seg}


def is_identifier_path(path: str) -> bool:
    """Match whole underscore-separated segments of the leaf, never substrings.

    Substring matching over-blocks badly: "nin" is inside "kmc_positioning_checklist",
    "id" is inside "valid" and "avoid"."""
    return bool(leaf_segments(path) & IDENTIFIER_LEAF_TOKENS)


def is_envelope_path(path: str) -> bool:
    return path in ENVELOPE_EXACT or path.startswith(ENVELOPE_PREFIXES)


def looks_like_free_text(counts: Counter) -> bool:
    """True when the observed values are not a closed list of choice codes: too many
    distinct values, or any space-separated token too long or shaped like an id."""
    if len(counts) > MAX_CATEGORY_VALUES:
        return True
    for value in counts:
        for token in str(value).split():
            if len(token) > MAX_REPLAYABLE_VALUE_LEN or IDENTIFIER_VALUE_RE.match(token):
                return True
    return False


def parse_iso_date(raw: str) -> dt.date | None:
    """Separator-formatted ISO dates only (``YYYY-MM-DD``, optionally followed by a
    time), which is what CommCare emits. Stricter than ``date.fromisoformat``, which
    also accepts ``YYYYMMDD`` -- so a bare id like ``20250601`` would read as a date."""
    text = (raw or "").strip()
    if len(text) < 10 or text[4] != "-" or text[7] != "-":
        return None
    try:
        return dt.date.fromisoformat(text[:10])
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Kinds and their default settings
# ---------------------------------------------------------------------------

IDENTIFIER = "identifier"  # a name, phone, address, national id: says who someone is
FREE_TEXT = "free_text"  # typed prose: may name anyone
GPS = "gps"  # a geopoint
CASE_ID = "case_id"  # a CommCare case id: links visits, says nothing itself
ID = "id"  # another id (a record number): links, says little
MEDIA = "media"  # a photo, signature or other attachment
DATE = "date"
NUMBER = "number"
CATEGORICAL = "categorical"
EMPTY = "empty"

KINDS = (IDENTIFIER, FREE_TEXT, GPS, CASE_ID, ID, MEDIA, DATE, NUMBER, CATEGORICAL, EMPTY)

#: Leaf tokens that mean typed prose rather than a name.
_PROSE_TOKENS = frozenset(
    {"comment", "comments", "remark", "remarks", "note", "notes", "specify", "description", "other"}
)
_GPS_TOKENS = frozenset({"gps", "coordinate", "coordinates", "latitude", "longitude", "lat", "lon", "geopoint"})
_ID_TOKENS = frozenset({"id", "ids", "uuid"})
_MEDIA_TOKENS = frozenset({"photo", "image", "signature", "picture", "audio", "video"})
#: A date of birth narrows a person to a few hundred others; it is cut to the month.
_BIRTH_TOKENS = frozenset({"dob", "birth", "birthday", "birthdate"})

_GEOPOINT_RE = re.compile(r"^-?\d{1,2}(\.\d+)?\s+-?\d{1,3}(\.\d+)?(\s+-?[\d.]+){0,2}$")
_MEDIA_RE = re.compile(r"\.(jpe?g|png|gif|webp|heic|mp3|m4a|amr|mp4|3gp|wav)$", re.IGNORECASE)


def _share(counts: Counter, test) -> float:
    total = sum(counts.values())
    return sum(n for v, n in counts.items() if test(str(v).strip())) / total if total else 0.0


def _is_number(text: str) -> bool:
    try:
        float(text)
    except ValueError:
        return False
    return True


def classify(path: str, counts: Counter) -> str:
    """The kind of value ``path`` holds, from its name and the values seen (``counts``:
    value -> visits). Errs towards the more sensitive kind whenever two could apply."""
    counts = Counter({v: n for v, n in counts.items() if v not in (None, "")})
    segments = leaf_segments(path)
    leaf = path.rsplit(".", 1)[-1].lower()
    if leaf.endswith("case_id"):  # @case_id, child_case_id, kmc_beneficiary_case_id
        return CASE_ID
    if not counts:
        return EMPTY
    if segments & _GPS_TOKENS or _share(counts, lambda t: bool(_GEOPOINT_RE.match(t))) >= 0.5:
        return GPS
    if segments & _MEDIA_TOKENS or _share(counts, lambda t: bool(_MEDIA_RE.search(t))) >= 0.5:
        return MEDIA
    if segments & _PROSE_TOKENS:
        return FREE_TEXT
    if _share(counts, lambda t: bool(UUID_RE.match(t))) >= 0.5:
        return CASE_ID if "case" in segments else ID
    if segments & _ID_TOKENS:
        return ID
    if segments & IDENTIFIER_LEAF_TOKENS:
        return IDENTIFIER
    # A number that names someone rather than measures them: a phone or national id.
    if _share(counts, lambda t: bool(IDENTIFIER_VALUE_RE.match(t.split(".")[0]))) >= 0.5:
        return IDENTIFIER
    texts = [str(v).strip() for v in counts]
    if all(parse_iso_date(t) is not None for t in texts):
        return DATE
    if all(_is_number(t) for t in texts) and len(counts) > MAX_REPLAYABLE_CARDINALITY:
        return NUMBER
    if (
        len(counts) <= MAX_REPLAYABLE_CARDINALITY
        and all(len(t) <= MAX_REPLAYABLE_VALUE_LEN for t in texts)
        and not looks_like_free_text(counts)
    ):
        return CATEGORICAL
    if all(_is_number(t) for t in texts):
        return NUMBER
    return FREE_TEXT


#: The setting each kind starts with, before anyone reviews it: ``(setting, params)``.
#: An unreviewed field is dropped whatever this says (``labsdata.dictionary``); this is
#: only the suggestion a reviewer accepts or changes.
DEFAULT_SETTINGS: dict[str, tuple[str, dict]] = {
    IDENTIFIER: ("drop", {}),
    FREE_TEXT: ("drop", {}),
    GPS: ("drop", {}),
    MEDIA: ("drop", {}),
    EMPTY: ("drop", {}),
    CASE_ID: ("pseudonymise", {"namespace": "case"}),
    ID: ("pseudonymise", {}),
    DATE: ("keep", {}),
    NUMBER: ("keep", {}),
    CATEGORICAL: ("keep", {}),
}

#: Kinds that do not name anyone alone but can single someone out in combination
#: (a date, a place, a worker): the export's small-group check reads these together.
QUASI_IDENTIFYING = frozenset({DATE})


def default_setting(path: str, kind: str) -> tuple[str, dict]:
    setting, params = DEFAULT_SETTINGS.get(kind, ("drop", {}))
    if kind == DATE and leaf_segments(path) & _BIRTH_TOKENS:
        return "month", {}
    return setting, dict(params)


def is_quasi_identifying(path: str, kind: str) -> bool:
    return kind in QUASI_IDENTIFYING or bool(leaf_segments(path) & _BIRTH_TOKENS)
