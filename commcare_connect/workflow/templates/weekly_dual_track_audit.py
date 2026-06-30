"""Weekly Dual-Track Image Audit — multi-opp, action-shaped creator.

Each weekly run creates, per FLW, two audits per opportunity:
  - Track A ("muac"): census of the pinned MUAC image type(s), 100%, with the
    muac_overzoom AI agent auto-tagging fails.
  - Track B ("rest"): the remaining pinned image types, sampled (default 10%),
    human-reviewed.

The per-opp image paths and track config live on the workflow DEFINITION
(instance config); the batch window lives in run state. See
docs/superpowers/specs/2026-06-30-audit-program-report-design.md.
"""


def _image_audits(paths, reviewer):
    """One image_audits entry per pinned image path. The track's reviewer (or no
    reviewer) is attached to each — the PR #771 per-image-type model. See
    commcare_connect/audit/ai_review_config.build_review_config."""
    reviewers = [reviewer] if reviewer else []
    return [{"image_path": p, "reviewers": list(reviewers)} for p in (paths or [])]


def build_track_audit_calls(
    *,
    opportunity_ids,
    opp_names,
    per_opp,
    track_a,
    track_b,
    window_start,
    window_end,
    username,
    workflow_run_id,
):
    """Build the per-opp, per-track run_audit_creation kwargs for one weekly batch.

    Returns a flat list of kwargs dicts. A track is skipped when its per-opp
    image-path list is empty. JSON-coerced string keys are used to look up
    per_opp / opp_names, so callers may pass either int or str opp ids.
    """
    calls = []
    for opp_id in opportunity_ids:
        key = str(opp_id)
        cfg = per_opp.get(key, {})
        name = opp_names.get(key, "")
        for track, paths in (
            (track_a, cfg.get("muac_image_paths")),
            (track_b, cfg.get("rest_image_paths")),
        ):
            image_audits = _image_audits(paths, track.get("reviewer"))
            if not image_audits:
                continue
            calls.append(
                {
                    "username": username,
                    "opportunities": [{"id": opp_id, "name": name}],
                    "criteria": {
                        "audit_type": "date_range",
                        "start_date": window_start,
                        "end_date": window_end,
                        "sample_percentage": track["sample_percentage"],
                        "granularity": "per_flw",
                        "tag": track["tag"],
                        # related_fields is derived by run_audit_creation from image_audits.
                    },
                    "workflow_run_id": workflow_run_id,
                    "image_audits": image_audits,
                    "context_fields": None,
                }
            )
    return calls
