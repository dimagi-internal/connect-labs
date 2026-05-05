"""
Proxy models for Task LocalLabsRecords.

These proxy models provide convenient access to LocalLabsRecord data
for the tasks workflow. LocalLabsRecord is a transient Python object
that deserializes production API responses - no database storage.
"""

from datetime import datetime

from commcare_connect.labs.models import LocalLabsRecord


class TaskRecord(LocalLabsRecord):
    """Proxy model for Task-type LocalLabsRecords."""

    # Properties for convenient access to task data
    # Note: username and opportunity_id are fields on LocalLabsRecord parent
    # LocalLabsRecord is a transient object from API responses, not a database model

    @property
    def task_username(self):
        """Username of the FLW this task is about (stored in data, not parent username field)."""
        return self.data.get("username")

    @property
    def flw_name(self):
        """Display name of the FLW this task is about."""
        return self.data.get("flw_name") or self.data.get("username", "")

    @property
    def user_id(self):
        """User ID of the FLW this task is about (may be None if not available from API)."""
        return self.data.get("user_id")

    @property
    def status(self):
        """Current workflow status: investigating, flw_action_in_progress, flw_action_completed,
        review_needed, closed."""
        return self.data.get("status", "investigating")

    @property
    def assigned_to_type(self):
        """Who the task is assigned to: self, network_manager, program_manager."""
        return self.data.get("assigned_to_type", "self")

    @property
    def assigned_to_name(self):
        """Display name of assignee."""
        return self.data.get("assigned_to_name", "")

    @property
    def resolution_details(self):
        """Resolution details when task is closed: {official_action, resolution_note}."""
        return self.data.get("resolution_details", {})

    @property
    def priority(self):
        """Priority: low, medium, high."""
        return self.data.get("priority", "medium")

    @property
    def title(self):
        """Task title."""
        return self.data.get("title", "")

    @property
    def description(self):
        """Task description."""
        return self.data.get("description", "")

    @property
    def audit_session_id(self):
        """Reference to audit session that triggered this task (optional)."""
        return self.data.get("audit_session_id")

    @property
    def workflow_run_id(self):
        """Reference to the workflow run that created this task (optional)."""
        return self.data.get("workflow_run_id")

    @property
    def date_created(self):
        """Date the task was created (from first 'created' event)."""
        for event in self.data.get("events", []):
            if event.get("event_type") == "created":
                timestamp = event.get("timestamp")
                if timestamp:
                    try:
                        return datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                    except (ValueError, TypeError):
                        return None
        return None

    @property
    def events(self):
        """List of timeline events (includes comments and AI sessions)."""
        return self.data.get("events", [])

    # Helper methods for managing nested data
    def add_event(self, event_type, actor, description, **kwargs):
        """
        Add an event to the task timeline.

        Args:
            event_type: Type of event (created, updated, comment, ai_session, etc.)
            actor: Name of person/system performing the action
            description: Human-readable description of the event
            **kwargs: Additional fields for specific event types (content, session_id, etc.)

        Returns:
            Updated task record (not saved to DB)
        """
        if "events" not in self.data:
            self.data["events"] = []

        event = {
            "event_type": event_type,
            "actor": actor,
            "description": description,
            "timestamp": datetime.now().isoformat(),
        }

        # Add any additional fields from kwargs (for comment content, ai_session fields, etc.)
        for key, value in kwargs.items():
            if value is not None:
                event[key] = value

        self.data["events"].append(event)
        return self

    def add_comment(self, actor, content):
        """
        Add a comment to the task as an event.

        Args:
            actor: Display name of comment author
            content: Comment text

        Returns:
            Updated task record (not saved to DB)
        """
        return self.add_event(
            event_type="comment",
            actor=actor,
            description="Added a comment",
            content=content,
        )

    def add_ai_session(self, actor, session_params, session_id=None, status="initiated"):
        """
        Add an AI assistant session to the task as an event.

        Args:
            actor: Name of person who triggered the AI session
            session_params: Dict with session parameters (platform, experiment, identifier, prompt_text)
            session_id: OCS session ID (may be None initially, linked later)
            status: Session status (initiated, pending, completed)

        Returns:
            Updated task record (not saved to DB)
        """
        identifier = session_params.get("identifier", "FLW")
        return self.add_event(
            event_type="ai_session",
            actor=actor,
            description=f"AI assistant triggered for {identifier}",
            session_id=session_id,
            status=status,
            session_params=session_params,
        )

    def get_timeline(self):
        """
        Get timeline of events sorted by timestamp.

        All events (including comments and AI sessions) are stored in the events array.

        Returns:
            List of events sorted by timestamp (newest first)
        """
        timeline = list(self.events)
        timeline.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
        return timeline

    def get_ai_session_events(self):
        """
        Get AI session events from the timeline.

        Returns:
            List of ai_session events
        """
        return [e for e in self.events if e.get("event_type") == "ai_session"]

    def get_comment_events(self):
        """
        Get comment events from the timeline.

        Returns:
            List of comment events
        """
        return [e for e in self.events if e.get("event_type") == "comment"]

    def get_status_display(self):
        """Get human-readable status label."""
        status_labels = {
            "investigating": "Investigating",
            "flw_action_in_progress": "FLW Action In Progress",
            "flw_action_completed": "FLW Action Completed",
            "review_needed": "Review Needed",
            "closed": "Closed",
            # Legacy values for backwards compatibility
            "unassigned": "Investigating",
            "network_manager": "Investigating",
            "program_manager": "Investigating",
            "action_underway": "FLW Action In Progress",
            "resolved": "Closed",
        }
        return status_labels.get(self.status, self.status.replace("_", " ").title())

    def get_assigned_to_display(self):
        """Get human-readable assigned to label."""
        if self.assigned_to_type == "self":
            return self.assigned_to_name or "Me"
        elif self.assigned_to_type == "network_manager":
            return "Network Manager"
        elif self.assigned_to_type == "program_manager":
            return "Program Manager"
        return self.assigned_to_name or "Unassigned"

    def get_priority_display(self):
        """Get human-readable priority label."""
        priority_labels = {
            "low": "Low",
            "medium": "Medium",
            "high": "High",
        }
        return priority_labels.get(self.priority, self.priority.title())
