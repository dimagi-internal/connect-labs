"""
Proxy models for Audit LocalLabsRecords.

These proxy models provide convenient access to LocalLabsRecord data
for the audit workflow. LocalLabsRecord is a transient Python object
that deserializes production API responses - no database storage.
"""

from connect_labs.labs.models import LocalLabsRecord

# Shared with connect_labs.audit.tasks._combine_reviewer_results, which joins
# multiple independent AI reviewers' badge_labels into one ai_notes string
# with this separator. get_assessment_stats() below splits on the SAME
# constant to recover each reviewer's own label -- importing this one value
# on both sides means a change to the separator fails loudly (an import
# error or a single obvious edit site) instead of silently desyncing.
AI_NOTES_JOIN_SEP = "; "


class AuditSessionRecord(LocalLabsRecord):
    """Proxy model for AuditSession-type LocalLabsRecords with nested visit results."""

    # Properties for convenient access
    @property
    def title(self):
        """Audit session title."""
        return self.data.get("title", "")

    @property
    def tag(self):
        """Audit session tag."""
        return self.data.get("tag", "")

    @property
    def status(self):
        """Audit status: in_progress or completed."""
        return self.data.get("status", "in_progress")

    @property
    def overall_result(self):
        """Overall result: pass, fail, or None."""
        return self.data.get("overall_result")

    @property
    def pass_threshold(self):
        """Minimum % of assessments that must pass for the audit to be marked pass (75-100, default 100)."""
        return self.data.get("pass_threshold", 100)

    @property
    def completed_at(self):
        """When the image review was completed (None while in progress).

        Returned as a datetime, parsed from the ISO string the completion
        flow writes into ``data["completed_at"]`` — the bulk-assessment
        template formats this with the ``|date`` filter, which silently
        renders '' for raw strings.
        """
        import datetime as dt

        raw = self.data.get("completed_at")
        if not raw:
            return None
        try:
            return dt.datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError:
            return None

    @property
    def notes(self):
        """General audit notes."""
        return self.data.get("notes", "")

    @property
    def kpi_notes(self):
        """KPI-related notes."""
        return self.data.get("kpi_notes", "")

    @property
    def visit_ids(self):
        """List of UserVisit IDs to audit."""
        return self.data.get("visit_ids", [])

    # ------------------------------------------------------------------
    # An audit session carries TWO opportunities. Keep them apart.
    #
    #   storage_opportunity_id — where the RECORD is filed. The production
    #       export API authorizes, filters, and writes by this one. Comes
    #       from the API envelope (``api_data["opportunity_id"]``).
    #   opportunity_id         — what the audit is ABOUT: the opportunity
    #       whose visits are under review. Lives in the payload
    #       (``data["opportunity_id"]``) and is what the UI displays.
    #
    # They are equal for a session created while a single opportunity is
    # selected — the overwhelmingly common case, and why this went eight
    # months without anyone noticing. They diverge under program scope, and
    # every incident below is that divergence meeting code that assumed one
    # name meant one thing: #933 (workflow scope), #1012 ("Complete Review"
    # failed with a generic error for real reviewers), #1037 (54 minutes at
    # 100% CPU and ~700 req/min against production Connect), #1060/#1074
    # (23,445 scoped probes in a day).
    #
    # `opportunity_id` deliberately still shadows the base class's storage
    # value, because ~10 call sites across views, templates and workflow
    # templates read it for DISPLAY and mean the audit target. What changed
    # is that the storage value is no longer hidden behind a private
    # `_opportunity_id_from_api` that nothing was expected to read: it has a
    # name, a docstring, and tests. Reach for `storage_opportunity_id`
    # whenever you are addressing the API rather than describing the audit.
    # ------------------------------------------------------------------

    @property
    def opportunity_id(self):
        """The opportunity this audit is ABOUT (its subject) — NOT where it is stored.

        For the scope the production API files and authorizes this record
        under, use :attr:`storage_opportunity_id`.
        """
        return self.data.get("opportunity_id")

    @opportunity_id.setter
    def opportunity_id(self, value):
        """Absorb ``LocalLabsRecord.__init__``'s assignment of the STORAGE scope.

        The base constructor runs ``self.opportunity_id = api_data["opportunity_id"]``,
        which is the storage scope. Because the getter above is the audit
        subject, that write is routed to :attr:`storage_opportunity_id`'s
        backing field rather than being allowed to overwrite the subject.
        """
        object.__setattr__(self, "_storage_opportunity_id", value)

    @property
    def storage_opportunity_id(self):
        """The opportunity this RECORD IS FILED UNDER — the scope the API uses.

        Use this for anything addressed to the production API: fetching by
        id, choosing a scoped client, or building a write payload. Using
        :attr:`opportunity_id` for those is what silently relocated sessions
        and drove the cross-opportunity sweep.

        Falls back to the audit subject for records built without an API
        envelope (locally constructed or older fixtures), where the two are
        the same by construction.
        """
        value = getattr(self, "_storage_opportunity_id", None)
        return self.data.get("opportunity_id") if value is None else value

    def to_api_dict(self):
        """Serialize for the API, filing the record under its STORAGE scope.

        The inherited implementation emits ``self.opportunity_id``, which on
        this class is the audit SUBJECT — so a session whose two opportunities
        differ would be written back under the wrong scope, i.e. moved. No
        caller in the audit app hits that path today; this override means one
        cannot be introduced by accident. The subject is untouched: it rides
        along inside ``data``.
        """
        payload = super().to_api_dict()
        payload["opportunity_id"] = self.storage_opportunity_id
        return payload

    @property
    def opportunity_name(self):
        """Name of the primary opportunity being audited."""
        return self.data.get("opportunity_name", "")

    @property
    def flw_username(self):
        """FLW username extracted from first visit's images (same pattern as bulk assessment)."""
        visit_images = self.data.get("visit_images", {})
        for visit_id, images in visit_images.items():
            if images:
                return images[0].get("username", "")
        return ""

    @property
    def description(self):
        """Human-readable description of how this audit session was created."""
        return self.data.get("description", "")

    @property
    def criteria(self):
        """
        Audit criteria used to create this session.

        Returns dict with audit_type, start_date, end_date, count_per_flw, etc.
        May be None for sessions created before criteria storage was added.
        """
        return self.data.get("criteria")

    @property
    def workflow_run_id(self):
        """
        ID of the workflow run that created this session, if any.

        Returns the labs_record_id which points to a workflow run record,
        or None if created from the wizard UI.
        """
        return self.labs_record_id

    @property
    def visit_results(self):
        """Dict of visit results keyed by visit_id."""
        return self.data.get("visit_results", {})

    # Helper methods for managing nested visit results
    def get_visit_result(self, visit_id: int) -> dict | None:
        """
        Get result for a specific visit by UserVisit ID.

        Args:
            visit_id: UserVisit ID from Connect

        Returns:
            Dict with xform_id, result, notes, assessments, or None if not found
        """
        return self.data.get("visit_results", {}).get(str(visit_id))

    def set_visit_result(
        self,
        visit_id: int,
        xform_id: str,
        result: str | None,
        notes: str,
        user_id: int,
        opportunity_id: int,
    ):
        """
        Set/update result for a visit using UserVisit ID as key.

        Args:
            visit_id: UserVisit ID from Connect
            xform_id: Form ID
            result: "pass" or "fail"
            notes: Notes about the visit
            user_id: FLW user ID
            opportunity_id: Opportunity ID
        """
        if "visit_results" not in self.data:
            self.data["visit_results"] = {}

        visit_key = str(visit_id)
        existing = self.data["visit_results"].get(visit_key, {})

        self.data["visit_results"][visit_key] = {
            "xform_id": xform_id,
            "result": result,
            "notes": notes,
            "user_id": user_id,
            "opportunity_id": opportunity_id,
            "assessments": existing.get("assessments", {}),
        }

    def clear_visit_result(self, visit_id: int):
        """
        Clear the stored result for a visit without losing assessments.

        Args:
            visit_id: UserVisit ID from Connect
        """
        visit_key = str(visit_id)
        visit_data = self.data.get("visit_results", {}).get(visit_key)
        if visit_data:
            visit_data["result"] = None
            visit_data["notes"] = ""

    def get_assessments(self, visit_id: int) -> dict:
        """
        Get all assessments for a visit by UserVisit ID.

        Args:
            visit_id: UserVisit ID from Connect

        Returns:
            Dict of assessments keyed by blob_id
        """
        return self.data.get("visit_results", {}).get(str(visit_id), {}).get("assessments", {})

    def set_assessment(
        self,
        visit_id: int,
        blob_id: str,
        question_id: str,
        result: str | None,
        notes: str,
        ai_result: str | None = None,
        ai_notes: str | None = None,
        ai_confidence: float | None = None,
    ):
        """
        Set/update assessment for an image.

        Args:
            visit_id: UserVisit ID from Connect
            blob_id: Blob ID
            question_id: CommCare question path
            result: "pass" or "fail"
            notes: Notes about the assessment
            ai_result: AI review result ("match", "no_match", "error", or None)
            ai_notes: AI review notes/details
            ai_confidence: AI review confidence score (0.0-1.0), if the agent reported one
        """
        visit_key = str(visit_id)

        if "visit_results" not in self.data:
            self.data["visit_results"] = {}

        if visit_key not in self.data["visit_results"]:
            # Initialize visit result if doesn't exist
            self.data["visit_results"][visit_key] = {"assessments": {}}

        visit_result = self.data["visit_results"][visit_key]
        if "assessments" not in visit_result:
            visit_result["assessments"] = {}

        assessment = {
            "question_id": question_id,
            "result": result,
            "notes": notes,
        }
        # Include AI fields if provided
        if ai_result is not None:
            assessment["ai_result"] = ai_result
        if ai_notes is not None:
            assessment["ai_notes"] = ai_notes
        if ai_confidence is not None:
            assessment["ai_confidence"] = ai_confidence

        visit_result["assessments"][blob_id] = assessment

    def flag_potential_duplicate(self, visit_id: int, blob_id: str, question_id: str, group_id: int):
        """Non-destructively flag an image as a potential duplicate subject.

        Composes with any per-image AI review already written for this blob
        ONLY when that prior review was itself a flag (ai_result == "no_match"):
        the "Potential Duplicate" label is MERGED into ai_notes using
        AI_NOTES_JOIN_SEP (the same mechanism _combine_reviewer_results uses),
        so get_assessment_stats().ai_flags_by_label counts it alongside e.g. a
        muac_overzoom "Hyperzoomed" flag rather than clobbering it.

        A prior "match" (pass) or "error" (e.g. a rate-limited classifier call)
        is DISCARDED rather than merged -- those labels describe a verdict that
        no longer applies once ai_result flips to "no_match" here, and merging
        them in used to leak stale pass-labels ("Not Hyperzoomed", "MUAC Match")
        and raw error text ("Rate limited...") into ai_flags_by_label as if they
        were real flags, both on the per-image tile and the session summary.

        The human `result` is left untouched (flag-only). ``group_id`` is a
        connected-component index so the review UI can sort duplicates
        adjacently.
        """
        from connect_labs.audit.duplicate_detection import DUPLICATE_FLAG_LABEL

        visit_key = str(visit_id)
        visit_results = self.data.setdefault("visit_results", {})
        visit_result = visit_results.setdefault(visit_key, {"assessments": {}})
        assessments = visit_result.setdefault("assessments", {})
        assessment = assessments.setdefault(blob_id, {"question_id": question_id, "result": None, "notes": ""})

        was_already_flagged = assessment.get("ai_result") == "no_match"
        assessment["question_id"] = question_id or assessment.get("question_id")
        assessment["ai_result"] = "no_match"
        assessment["duplicate_group"] = group_id

        existing_labels = (
            [label.strip() for label in (assessment.get("ai_notes") or "").split(AI_NOTES_JOIN_SEP) if label.strip()]
            if was_already_flagged
            else []
        )
        if DUPLICATE_FLAG_LABEL not in existing_labels:
            existing_labels.append(DUPLICATE_FLAG_LABEL)
        assessment["ai_notes"] = AI_NOTES_JOIN_SEP.join(existing_labels)

    def flag_potential_duplicate_and_tag(self, visit_id: int, blob_id: str, question_id: str, group_id: int):
        """Like flag_potential_duplicate, but ALSO auto-tags the human `result`
        as "duplicate_fake" when the assessment is still untouched -- never
        overwriting an existing manual verdict.

        A second, opt-in version of the flag-only method above: some callers
        (the dual-track workflow's visit-clustering-grouping detection, see
        connect_labs.audit.visit_cluster_duplicate_detection) want a confirmed
        duplicate to show up already tagged in bulk assessment, not just
        flagged in the AI summary. Kept as a distinct method rather than a
        parameter on flag_potential_duplicate because this is likely to become
        a per-run, user-configurable choice (flag-only vs. flag-and-tag) rather
        than a fixed per-caller behavior.
        """
        self.flag_potential_duplicate(visit_id, blob_id, question_id, group_id)
        visit_key = str(visit_id)
        assessment = self.data["visit_results"][visit_key]["assessments"][blob_id]
        if not assessment.get("result"):
            assessment["result"] = "duplicate_fake"

    def clear_assessment(self, visit_id: int, blob_id: str):
        """
        Remove an assessment entry for an image.

        Args:
            visit_id: UserVisit ID from Connect
            blob_id: Blob ID
        """
        visit_key = str(visit_id)
        visit_result = self.data.get("visit_results", {}).get(visit_key)
        if visit_result and "assessments" in visit_result:
            visit_result["assessments"].pop(blob_id, None)

    def get_progress_stats(self) -> dict:
        """
        Calculate progress statistics based on assessments.

        Returns:
            Dict with percentage, assessed count, and total count
        """
        total_assessments = 0
        assessed_count = 0

        for visit_result in self.data.get("visit_results", {}).values():
            for assessment in visit_result.get("assessments", {}).values():
                total_assessments += 1
                if assessment.get("result"):
                    assessed_count += 1

        percentage = (assessed_count / total_assessments * 100) if total_assessments > 0 else 0

        return {
            "percentage": round(percentage, 1),
            "assessed": assessed_count,
            "total": total_assessments,
        }

    def is_complete(self) -> bool:
        """Check if audit is completed."""
        return self.status == "completed"

    def get_visit_count(self) -> int:
        """Get total number of visits in this audit."""
        return len(self.visit_ids)

    def get_assessment_stats(self) -> dict:
        """
        Calculate comprehensive assessment statistics.

        Returns:
            Dict with counts for human assessment and AI review:
            {
                "total": int,           # Total assessments
                "pass": int,            # Human: pass count
                "fail": int,            # Human: fail count
                "duplicate_fake": int,  # Human: flagged as a duplicate/fake image
                "pending": int,         # Human: not yet assessed
                "ai_match": int,        # AI: match count
                "ai_no_match": int,     # AI: no_match count
                "ai_error": int,        # AI: error count
                "ai_pending": int,      # AI: not yet reviewed
                "ai_flags_by_label": dict[str, int],  # AI: no_match count per classifier label
                "ai_flags_unlabeled": int,  # AI: no_match count with no recoverable label
            }

            Note: ai_flags_by_label's values can sum to MORE than ai_no_match
            -- an image with two independent reviewers both failing (e.g.
            MUAC OverZoom + MUAC Match) counts toward BOTH labels while still
            being a single no_match assessment. Don't infer ai_flags_unlabeled
            by subtracting one from the other; it's tracked as its own
            counter for exactly this reason.
        """
        stats = {
            "total": 0,
            "pass": 0,
            "fail": 0,
            "duplicate_fake": 0,
            "pending": 0,
            "ai_match": 0,
            "ai_no_match": 0,
            "ai_error": 0,
            "ai_pending": 0,
            "ai_flags_by_label": {},
            "ai_flags_unlabeled": 0,
        }

        for visit_result in self.data.get("visit_results", {}).values():
            for assessment in visit_result.get("assessments", {}).values():
                stats["total"] += 1

                # Human assessment result. "duplicate_fake" is a distinct
                # bucket from "pending" — it's a completed assessment (the
                # image was reviewed and flagged), not an unreviewed one. It
                # already counts against the pass rate the same way fail
                # does, since neither is counted in "pass" and pass rate is
                # computed as pass/total. "duplicate" and "fake" are the same
                # bucket split into two distinct results -- only ever written
                # by the muac_picture_audit workflow's review screen.
                result = assessment.get("result")
                if result == "pass":
                    stats["pass"] += 1
                elif result == "fail":
                    stats["fail"] += 1
                elif result in ("duplicate_fake", "duplicate", "fake"):
                    stats["duplicate_fake"] += 1
                else:
                    stats["pending"] += 1

                # AI review result
                ai_result = assessment.get("ai_result")
                if ai_result == "match":
                    stats["ai_match"] += 1
                elif ai_result == "no_match":
                    stats["ai_no_match"] += 1
                    # Multiple independent reviewers on one image path (e.g.
                    # MUAC OverZoom + MUAC Match) each contribute their own
                    # badge_label; _combine_reviewer_results joins every
                    # failing reviewer's label with AI_NOTES_JOIN_SEP into
                    # ai_notes (see connect_labs/audit/tasks.py). Splitting it
                    # back apart here recovers which classifier(s) flagged
                    # this image -- one image can count toward MORE THAN ONE
                    # label, so ai_flags_by_label's values can sum to more
                    # than ai_no_match. ai_flags_unlabeled is tracked as its
                    # own counter (not inferred by subtracting one from the
                    # other) for exactly that reason.
                    found_label = False
                    for label in (assessment.get("ai_notes") or "").split(AI_NOTES_JOIN_SEP):
                        label = label.strip()
                        if label:
                            stats["ai_flags_by_label"][label] = stats["ai_flags_by_label"].get(label, 0) + 1
                            found_label = True
                    if not found_label:
                        stats["ai_flags_unlabeled"] += 1
                elif ai_result == "error":
                    stats["ai_error"] += 1
                else:
                    stats["ai_pending"] += 1

        return stats

    def get_flw_count(self) -> int:
        """Number of distinct FLWs (usernames) whose images are in this session.

        A combined session spans many FLWs; per-FLW sessions have exactly one.
        Lets the UI label combined sessions honestly instead of showing a single
        (first) FLW's name for everyone's images.
        """
        usernames = set()
        for images in self.data.get("visit_images", {}).values():
            for img in images or []:
                username = img.get("username")
                if username:
                    usernames.add(username)
        return len(usernames)

    def to_summary_dict(self) -> dict:
        """
        Convert session to a summary dict for API responses.

        Includes core fields and computed statistics for display.
        """
        stats = self.get_assessment_stats()
        criteria = self.criteria or {}
        return {
            "id": self.id,
            "title": self.title,
            "tag": self.tag,
            "status": self.status,
            "overall_result": self.overall_result,
            "opportunity_id": self.opportunity_id,
            "opportunity_name": self.opportunity_name,
            "description": self.description,
            "visit_count": self.get_visit_count(),
            "image_count": self.data.get("image_count", 0),
            "assessment_stats": stats,
            "workflow_run_id": self.workflow_run_id,
            "flw_username": self.flw_username,
            "flw_count": self.get_flw_count(),
            "visit_clusters": self.data.get("visit_clusters", []),
            "has_ai_reviewer": self.data.get("has_ai_reviewer", False),
            # The clustering filter actually used to create THIS session (its
            # own stored criteria), not the template's current/pinned default
            # -- lets the duplicate-grouping UI tell a reviewer what params to
            # expect without them having to go re-check the run's config.
            # Named "_used" (not "visit_clustering", which the workflow
            # template's DEFINITION.config.audit_batch already uses for its
            # pinned, not-yet-run default) to avoid two same-named, different-
            # meaning structures in this codebase's audit-clustering feature.
            "visit_clustering_used": {
                "enable_time_gap": bool(criteria.get("enable_time_gap")),
                "time_gap_minutes": criteria.get("time_gap_minutes"),
                "enable_distance": bool(criteria.get("enable_distance")),
                "distance_meters": criteria.get("distance_meters"),
            },
        }

    def get_assessment_stats_by_question(self) -> dict:
        """
        Calculate pass/fail/pending counts grouped by photo question_id.

        Unlike get_assessment_stats() (one overall total), this disaggregates
        by photo type so callers can show e.g. "MUAC Photo 4/10, Vaccine Card 7/12"
        per FLW instead of a single blended pass rate.

        Returns:
            Dict keyed by question_id -> {"label": str, "pass": int, "fail": int,
            "duplicate_fake": int, "pending": int, "total": int}
        """
        by_question: dict[str, dict] = {}
        for visit_result in self.data.get("visit_results", {}).values():
            for assessment in visit_result.get("assessments", {}).values():
                qid = assessment.get("question_id") or "unknown"
                bucket = by_question.setdefault(
                    qid,
                    {
                        "label": qid.rsplit("/", 1)[-1],
                        "pass": 0,
                        "fail": 0,
                        "duplicate_fake": 0,
                        "pending": 0,
                        "total": 0,
                    },
                )
                bucket["total"] += 1
                # "duplicate_fake" is a distinct, completed assessment outcome
                # (the image was reviewed and flagged) — not "pending" (not
                # yet reviewed). See get_assessment_stats for why pass/total
                # already treats it as failing without further bucket math.
                result = assessment.get("result")
                if result == "pass":
                    bucket["pass"] += 1
                elif result == "fail":
                    bucket["fail"] += 1
                elif result in ("duplicate_fake", "duplicate", "fake"):
                    bucket["duplicate_fake"] += 1
                else:
                    bucket["pending"] += 1
        return by_question

    def get_visit_date_range(self) -> tuple[str | None, str | None]:
        """
        Derive (earliest, latest) visit date strings from stored per-image
        metadata (visit_images). Used as a fallback for sessions whose
        creation criteria didn't record explicit start/end dates (e.g. the
        "last_n_per_opp" audit mode, where photos are picked by recency
        rather than an explicit date range).
        """
        dates = []
        for images in self.data.get("visit_images", {}).values():
            for img in images:
                vd = img.get("visit_date")
                if vd:
                    dates.append(str(vd))
        if not dates:
            return None, None
        return min(dates), max(dates)

    def to_question_summary_dict(self) -> dict:
        """
        Convert session to a summary dict disaggregated by photo question type.

        Used by the opportunity-scoped sessions-summary API so callers (e.g.
        a Labs workflow's render code) can display per-photo-type pass/fail
        without fetching or parsing full per-image assessment data themselves.
        Includes pass_threshold so callers don't need their own hardcoded
        cutoff — each session's own configured threshold is the source of
        truth for whether its images "passed" overall.
        """
        criteria = self.criteria or {}
        start_date = criteria.get("start_date")
        end_date = criteria.get("end_date")
        if not start_date or not end_date:
            derived_start, derived_end = self.get_visit_date_range()
            start_date = start_date or (derived_start[:10] if derived_start else None)
            end_date = end_date or (derived_end[:10] if derived_end else None)
        return {
            "id": self.id,
            "flw_username": self.flw_username,
            "opportunity_id": self.opportunity_id,
            "status": self.status,
            "start_date": start_date,
            "end_date": end_date,
            "completed_at": self.data.get("completed_at"),
            "pass_threshold": self.pass_threshold,
            "by_question": self.get_assessment_stats_by_question(),
        }
