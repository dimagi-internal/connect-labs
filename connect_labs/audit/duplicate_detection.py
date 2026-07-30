"""
Duplicate-photo detection assessment.

A *group*-level image assessment (unlike the per-image agents in
``connect_labs/labs/ai_review_agents/``): it sends every image of one photo
type for one FLW on one day to the ``/detect_duplicates`` endpoint and gets
back groups of image IDs judged to share a subject -- a common fabrication
signal. The endpoint is live on the same gateway as the scale/muac agents and
uses the same ``SCALE_VALIDATION_API_KEY``.

The detector fetches images by URL, so each blob must first be turned into a
publicly-fetchable presigned URL via Connect's exporter
(``/export/opportunity/<opp>/attachment_signed_url/``). ``get_signed_url`` mints
these just-in-time, immediately before each ``/detect_duplicates`` call, because
the presigned-URL TTL is short. A presign failure skips just that one image
(it drops out of the manifest) and is counted into the run summary; a failed
``/detect_duplicates`` call skips just that day-batch and is likewise counted, so
partial failures are always surfaced (see ``run_duplicate_detection``'s summary).

Recording is flag-only (non-destructive): each grouped image gets an AI flag
labelled "Potential Duplicate" merged into its ``ai_notes`` plus a
``duplicate_group`` component id so the review UI can sort duplicates adjacently.
"""

import logging

import httpx
from django.conf import settings

logger = logging.getLogger(__name__)


class DuplicateDetectionError(Exception):
    """A /detect_duplicates call did not succeed (config, rate-limit, HTTP, or
    malformed response). Raised so the caller can COUNT the failure into the run
    summary instead of silently treating it like an empty 'no duplicates' result.
    """


DETECT_ENDPOINT_PATH = "/detect_duplicates"
DEFAULT_TIMEOUT = 60.0
# Duplicate detection is a long-running single op over a whole day-batch of
# images -- it needs far longer than the per-image agents' 60s. The signed-URL
# presign above stays on DEFAULT_TIMEOUT (it's a quick lookup).
DETECT_TIMEOUT = 180.0
DEFAULT_MAX_IMAGES_PER_DAY = 40

# Label written into ai_notes for every image the detector groups. Splitting
# ai_notes on AI_NOTES_JOIN_SEP recovers it in get_assessment_stats().
DUPLICATE_FLAG_LABEL = "Potential Duplicate"


def get_signed_url(opportunity_id: int, blob_id: str, access_token: str) -> str:
    """Return a publicly-fetchable presigned URL for one image blob.

    Calls Connect's ``attachment_signed_url`` exporter with an OAuth Bearer
    token. Raises on any error (HTTP failure, or a response missing the
    ``attachment_signed_url`` field) -- the caller treats a presign failure as
    non-fatal for the session and skips its detection rather than sending a bad
    manifest.
    """
    production_url = settings.CONNECT_PRODUCTION_URL.rstrip("/")
    url = f"{production_url}/export/opportunity/{opportunity_id}/attachment_signed_url/"
    with httpx.Client(
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=DEFAULT_TIMEOUT,
    ) as client:
        response = client.get(url, params={"blob_id": blob_id})
        response.raise_for_status()
        signed = response.json().get("attachment_signed_url")
        if not signed:
            raise ValueError(f"attachment_signed_url response missing url for blob {blob_id}")
        return signed


class DuplicateDetectionClient:
    """HTTP client for the live ``/detect_duplicates`` gateway endpoint.

    Mirrors the httpx/settings pattern of the per-image agents (same base URL
    and ``x-api-key`` as scale validation / muac).
    """

    def __init__(self):
        self._client: httpx.Client | None = None

    @property
    def api_key(self) -> str:
        return getattr(settings, "SCALE_VALIDATION_API_KEY", "")

    @property
    def api_url(self) -> str:
        return getattr(
            settings,
            "SCALE_VALIDATION_API_URL",
            "https://image-pipeline-scale-gw-4pc8jsfa.uc.gateway.dev",
        ).rstrip("/")

    @property
    def http_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                headers={"Content-Type": "application/json", "x-api-key": self.api_key},
                timeout=DETECT_TIMEOUT,
            )
        return self._client

    def close(self):
        if self._client:
            self._client.close()
            self._client = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def detect(self, manifest_images: list[dict]) -> list[list[str]]:
        """POST a manifest of ``{"id","url"}`` items, return the detected groups.

        Returns an empty list ONLY for a genuine empty input (no images to check).
        Raises ``DuplicateDetectionError`` on any actual failure (missing key,
        rate limit, HTTP error, malformed response) -- an empty ``[]`` return must
        mean "checked, found no duplicates", never "the call failed", so the
        caller can count real failures into the run summary.

        This is a long-running single op. Callers should invoke it SEQUENTIALLY --
        do not issue many detect calls concurrently (see run_duplicate_detection's
        CONCURRENCY note); heavy parallelism slows every request problematically.
        """
        if not manifest_images:
            return []
        if not self.api_key:
            raise DuplicateDetectionError("SCALE_VALIDATION_API_KEY not configured")

        try:
            response = self.http_client.post(
                f"{self.api_url}{DETECT_ENDPOINT_PATH}",
                json={"images": manifest_images},
            )
            if response.status_code == 429:
                raise DuplicateDetectionError("rate limited (429) -- service busy or starting up")
            response.raise_for_status()
            groups = response.json().get("groups", [])
            # Defensive: coerce to list-of-list-of-str.
            return [[str(i) for i in group] for group in groups if isinstance(group, (list, tuple))]
        except httpx.HTTPStatusError as e:
            detail = ""
            try:
                detail = e.response.json().get("details", e.response.text)
            except Exception:
                detail = e.response.text
            raise DuplicateDetectionError(f"API error: {detail}") from e
        except httpx.HTTPError as e:
            raise DuplicateDetectionError(f"connection error: {e}") from e


def assign_group_ids(groups: list[list[str]]) -> dict[str, int]:
    """Collapse overlapping detector groups into connected components.

    The endpoint may return overlapping groups (e.g. ``[["a","b"],["b","c"]]``);
    union-find merges them so ``a``, ``b``, ``c`` share one component. Returns
    ``{blob_id: component_index}`` for blobs in components of size >= 2 only
    (singletons are not duplicates), with component indices ordered by each
    blob's first appearance across the input for deterministic, adjacency-
    friendly sort keys.
    """
    parent: dict[str, str] = {}
    appearance: list[str] = []

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for group in groups:
        for blob in group:
            if blob not in parent:
                parent[blob] = blob
                appearance.append(blob)
        for blob in group[1:]:
            union(group[0], blob)

    # Component sizes, to drop singletons.
    sizes: dict[str, int] = {}
    for blob in appearance:
        sizes[find(blob)] = sizes.get(find(blob), 0) + 1

    root_to_id: dict[str, int] = {}
    result: dict[str, int] = {}
    next_id = 0
    for blob in appearance:
        root = find(blob)
        if sizes.get(root, 0) < 2:
            continue
        if root not in root_to_id:
            root_to_id[root] = next_id
            next_id += 1
        result[blob] = root_to_id[root]
    return result


def build_duplicate_warnings(counts: dict, max_per_day: int | None = None) -> tuple[list[str], str]:
    """Build the human warnings list + note from a duplicate-detection summary.

    Shared by the per-session summary (stored on the session for the review-screen
    banner) and the run-level aggregate (the run summary), so both read the same
    way. Recognizes any skip/failure counter present in ``counts``; missing keys
    are treated as 0. Returns ``([], "")`` for a clean run.
    """
    if max_per_day is None:
        max_per_day = getattr(settings, "DUPLICATE_DETECTION_MAX_IMAGES_PER_DAY", DEFAULT_MAX_IMAGES_PER_DAY)
    warnings: list[str] = []
    if counts.get("detect_failures"):
        warnings.append(f"{counts['detect_failures']} day-batch(es) failed the duplicate check")
    if counts.get("skipped_presign"):
        warnings.append(f"{counts['skipped_presign']} image(s) skipped due to presigned-URL errors")
    if counts.get("skipped_over_limit"):
        warnings.append(f"{counts['skipped_over_limit']} image(s) skipped over the {max_per_day}/day limit")
    if counts.get("session_errors"):
        warnings.append(f"{counts['session_errors']} session(s) errored during duplicate detection")
    note = ("Duplicate detection completed with issues: " + "; ".join(warnings) + ".") if warnings else ""
    return warnings, note


def _images_grouped_by_type_and_day(session, image_paths: list[str] | None) -> dict:
    """Bucket a session's images into ``{(question_id, day): [image_dict, ...]}``.

    ``day`` is the ISO date prefix of each image's ``visit_date``. Images whose
    ``question_id`` is not in ``image_paths`` are skipped (None = keep all types,
    which is what the wizards already filter down to).
    """
    buckets: dict[tuple, list[dict]] = {}
    visit_images = session.data.get("visit_images", {})
    for visit_id, images in visit_images.items():
        for image in images:
            question_id = image.get("question_id")
            blob_id = image.get("blob_id")
            if not question_id or not blob_id:
                continue
            if image_paths is not None and question_id not in image_paths:
                continue
            day = (image.get("visit_date") or "")[:10] or "unknown"
            enriched = {**image, "visit_id": visit_id}
            buckets.setdefault((question_id, day), []).append(enriched)
    return buckets


def run_duplicate_detection(
    session,
    access_token: str,
    image_paths: list[str] | None = None,
    max_per_day: int | None = None,
    progress_callback=None,
) -> dict:
    """Run per-(FLW, day, photo-type) duplicate detection on one audit session.

    Mutates ``session`` in place (writes flags + raw groups); the caller is
    responsible for persisting via ``data_access.save_audit_session``. Returns a
    summary dict for progress/logging.

    CONCURRENCY -- INTENTIONALLY SEQUENTIAL: the day-batches below are processed
    one at a time, and each ``/detect_duplicates`` call completes before the next
    starts. Do NOT fan these out (e.g. a ThreadPoolExecutor over buckets firing
    ~10 detect calls at once) even though the sibling AI-review path
    (``_run_ai_review_on_sessions``) does exactly that per image. Each detect call
    is a long single op (see DETECT_TIMEOUT); running many concurrently slows every
    request enough to be problematic. Some incidental overlap is tolerable, but
    deliberate parallelism of the detect calls is not the intended model.

    (Presigning WITHIN a batch may be parallelized if needed -- that's a different
    concern, kept short so signed-URL TTLs don't expire before the POST.)
    """
    if max_per_day is None:
        max_per_day = getattr(settings, "DUPLICATE_DETECTION_MAX_IMAGES_PER_DAY", DEFAULT_MAX_IMAGES_PER_DAY)

    buckets = _images_grouped_by_type_and_day(session, image_paths)
    # Counters below drive the run-summary note. "skipped_*" record images the
    # detector never got a verdict on; "detect_failures" records whole day-batches
    # whose /detect_duplicates call failed. All are non-fatal -- work continues.
    summary = {
        "groups_detected": 0,
        "images_flagged": 0,
        "days_processed": 0,
        "skipped_over_limit": 0,  # images dropped by the max_per_day cap
        "skipped_presign": 0,  # images dropped because their presign failed
        "detect_failures": 0,  # day-batches whose detect call did not succeed
    }
    if not buckets:
        return summary

    raw_groups_store: dict[str, list] = session.data.setdefault("duplicate_detection", {})
    client = DuplicateDetectionClient()
    processed = 0
    try:
        # Sequential on purpose -- one detect call at a time. See the docstring's
        # CONCURRENCY note before considering any parallel fan-out here.
        for (question_id, day), images in buckets.items():
            summary["days_processed"] += 1
            if len(images) > max_per_day:
                dropped = len(images) - max_per_day
                logger.info(
                    "[DuplicateDetection] %s on %s has %d images; capping at %d (%d skipped)",
                    question_id,
                    day,
                    len(images),
                    max_per_day,
                    dropped,
                )
                images = images[:max_per_day]
                summary["skipped_over_limit"] += dropped

            # blob_id is the manifest id (unique per image); keep a lookup back
            # to (visit_id, question_id) for writing flags. A presign failure
            # drops just that image from the manifest and is counted -- the rest
            # of the batch still runs.
            by_blob = {img["blob_id"]: img for img in images}
            manifest = []
            for img in images:
                opp_for_image = img.get("opportunity_id") or session.opportunity_id
                try:
                    signed = get_signed_url(opp_for_image, img["blob_id"], access_token)
                except Exception as exc:
                    summary["skipped_presign"] += 1
                    logger.warning(
                        "[DuplicateDetection] presign failed for blob %s (opp %s): %s -- skipping image",
                        img["blob_id"],
                        opp_for_image,
                        exc,
                    )
                    continue
                manifest.append({"id": img["blob_id"], "url": signed})

            if not manifest:
                # Every image in this batch was skipped -- nothing to check.
                continue

            try:
                groups = client.detect(manifest)
            except DuplicateDetectionError as exc:
                summary["detect_failures"] += 1
                logger.error(
                    "[DuplicateDetection] detect call failed for %s on %s (%d images): %s",
                    question_id,
                    day,
                    len(manifest),
                    exc,
                )
                continue

            raw_groups_store[f"{question_id}|{day}"] = groups
            summary["groups_detected"] += len(groups)

            blob_to_group = assign_group_ids(groups)
            for blob_id, group_id in blob_to_group.items():
                img = by_blob.get(blob_id)
                if not img:
                    continue
                session.flag_potential_duplicate(
                    visit_id=int(img["visit_id"]),
                    blob_id=blob_id,
                    question_id=question_id,
                    group_id=group_id,
                )
                summary["images_flagged"] += 1

            processed += 1
            if progress_callback:
                progress_callback(
                    processed,
                    len(buckets),
                    f"Duplicate detection {processed}/{len(buckets)} day-batches "
                    f"({summary['images_flagged']} flagged)",
                )
    finally:
        client.close()

    # Stash a per-session summary + human note on the session so the bulk
    # assessment review screen (where the user lands after creation) can show a
    # banner when any part of this session's detection failed or was skipped.
    warnings, note = build_duplicate_warnings(summary, max_per_day)
    session.data["duplicate_detection_summary"] = {**summary, "warnings": warnings, "note": note}

    logger.info("[DuplicateDetection] session complete: %s", summary)
    return summary
