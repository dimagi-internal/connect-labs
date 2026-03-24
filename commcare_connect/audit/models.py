"""
Proxy models for Audit LocalLabsRecords.

These proxy models provide convenient access to LocalLabsRecord data
for the audit workflow. LocalLabsRecord is a transient Python object
that deserializes production API responses - no database storage.
"""

from commcare_connect.labs.models import LocalLabsRecord


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

    @property
    def opportunity_id(self):
        """Primary opportunity ID for this audit session (the audit target, not Labs storage)."""
        return self.data.get("opportunity_id")

    @opportunity_id.setter
    def opportunity_id(self, value):
        """Allow setting opportunity_id from LocalLabsRecord.__init__."""
        # LocalLabsRecord.__init__ tries to set this from api_data
        # We intercept it here and store in internal attribute
        object.__setattr__(self, "_opportunity_id_from_api", value)

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

        visit_result["assessments"][blob_id] = assessment

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
                "pending": int,         # Human: not yet assessed
                "ai_match": int,        # AI: match count
                "ai_no_match": int,     # AI: no_match count
                "ai_error": int,        # AI: error count
                "ai_pending": int,      # AI: not yet reviewed
            }
        """
        stats = {
            "total": 0,
            "pass": 0,
            "fail": 0,
            "pending": 0,
            "ai_match": 0,
            "ai_no_match": 0,
            "ai_error": 0,
            "ai_pending": 0,
        }

        for visit_result in self.data.get("visit_results", {}).values():
            for assessment in visit_result.get("assessments", {}).values():
                stats["total"] += 1

                # Human assessment result
                result = assessment.get("result")
                if result == "pass":
                    stats["pass"] += 1
                elif result == "fail":
                    stats["fail"] += 1
                else:
                    stats["pending"] += 1

                # AI review result
                ai_result = assessment.get("ai_result")
                if ai_result == "match":
                    stats["ai_match"] += 1
                elif ai_result == "no_match":
                    stats["ai_no_match"] += 1
                elif ai_result == "error":
                    stats["ai_error"] += 1
                else:
                    stats["ai_pending"] += 1

        return stats

    def to_summary_dict(self) -> dict:
        """
        Convert session to a summary dict for API responses.

        Includes core fields and computed statistics for display.
        """
        stats = self.get_assessment_stats()
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
        }
