"""
Data Access Layer for Tasks.

This layer uses LabsRecordAPIClient to interact with production LabsRecord API.
It handles:
1. Managing task state via production API
2. Fetching opportunity/user data dynamically from Connect OAuth APIs
3. Task operations (add events, comments, AI sessions)

This is a pure API client with no local database storage.
"""

from datetime import datetime

from commcare_connect.tasks.models import TaskRecord
from commcare_connect.workflow.data_access import BaseDataAccess


class TaskDataAccess(BaseDataAccess):
    """
    Data access layer for tasks that uses LabsRecordAPIClient for state
    and fetches opportunity/user data via OAuth APIs.
    """

    # Task CRUD Methods

    def create_task(
        self,
        username: str,
        opportunity_id: int,
        priority: str = "medium",
        title: str = "",
        description: str = "",
        user_id: int | None = None,
        flw_name: str | None = None,
        **kwargs,
    ) -> TaskRecord:
        """
        Create a new task.

        Args:
            username: FLW username (primary identifier in Connect)
            opportunity_id: Opportunity ID this task relates to
            priority: Priority (low, medium, high)
            title: Task title
            description: Task description
            user_id: FLW user ID (optional, may not be available from API)
            flw_name: FLW display name (optional, falls back to username)
            **kwargs: Additional fields (audit_session_id, creator_name, status, assigned_to_type, assigned_to_name)

        Returns:
            TaskRecord instance with initial "created" event

        Raises:
            ValueError: If username is empty or appears invalid
        """
        # Validate username
        if not username or not username.strip():
            raise ValueError("Username is required to create a task")

        # Warn about suspiciously long usernames (might be tokens or IDs)
        if len(username) > 50:
            import logging

            logger = logging.getLogger(__name__)
            logger.warning(f"Creating task with unusually long username (len={len(username)}): {username[:50]}...")

        # Build initial "created" event
        creator_name = kwargs.get("creator_name", "Unknown")
        initial_event = {
            "event_type": "created",
            "actor": creator_name,
            "description": f"Task created by {creator_name}",
            "timestamp": datetime.now().isoformat(),
        }

        # Task details first, events last
        data = {
            # Task details
            "title": title,
            "description": description,
            "priority": priority,
            "status": kwargs.get("status", "investigating"),
            # FLW info
            "username": username,
            "flw_name": flw_name or username,
            "user_id": user_id,
            "opportunity_id": opportunity_id,
            # Assignment
            "assigned_to_type": kwargs.get("assigned_to_type", "self"),
            "assigned_to_name": kwargs.get("assigned_to_name", creator_name),
            # Optional references
            "audit_session_id": kwargs.get("audit_session_id"),
            "workflow_run_id": kwargs.get("workflow_run_id"),
            "resolution_details": {},
            # Events (timeline)
            "events": [initial_event],
        }

        record = self.labs_api.create_record(
            experiment="tasks",
            type="Task",
            data=data,
            username=username,  # Use username not user_id
        )

        return TaskRecord(
            {
                "id": record.id,
                "experiment": record.experiment,
                "type": record.type,
                "data": record.data,
                "username": record.username,
                "opportunity_id": record.opportunity_id,
            }
        )

    def get_task(self, task_id: int) -> TaskRecord | None:
        """
        Get a task by ID.

        Args:
            task_id: Task ID

        Returns:
            TaskRecord or None if not found
        """
        return self.labs_api.get_record_by_id(
            record_id=task_id, experiment="tasks", type="Task", model_class=TaskRecord
        )

    def get_tasks(
        self,
        status: str | None = None,
        assigned_to_id: int | None = None,
    ) -> list[TaskRecord]:
        """
        Query tasks for the current opportunity.

        Args:
            status: Filter by status (client-side)
            assigned_to_id: Filter by assigned user ID (client-side)

        Returns:
            List of TaskRecord instances
        """
        all_tasks = self.labs_api.get_records(
            experiment="tasks",
            type="Task",
            model_class=TaskRecord,
        )

        # Apply filters client-side if needed
        if status or assigned_to_id:
            filtered = all_tasks
            if status:
                filtered = [t for t in filtered if t.data.get("status") == status]
            if assigned_to_id:
                filtered = [t for t in filtered if t.data.get("assigned_to_id") == assigned_to_id]
            return filtered

        return all_tasks

    def save_task(self, task_record: TaskRecord) -> TaskRecord:
        """
        Save a task record via API.

        Args:
            task_record: TaskRecord instance to save

        Returns:
            Saved TaskRecord instance
        """
        return self.labs_api.update_record(
            record_id=task_record.id,
            experiment="tasks",
            type="Task",
            data=task_record.data,
        )

    # Task Operation Methods

    def add_event(
        self,
        task: TaskRecord,
        event_type: str,
        actor: str,
        description: str,
        **kwargs,
    ) -> TaskRecord:
        """
        Add an event to a task and save it.

        Args:
            task: TaskRecord instance
            event_type: Type of event (created, status_changed, assigned, etc.)
            actor: Name of actor
            description: Event description
            **kwargs: Additional fields for specific event types

        Returns:
            Updated TaskRecord
        """
        task.add_event(event_type, actor, description, **kwargs)
        return self.labs_api.update_record(
            record_id=task.id,
            experiment="tasks",
            type="Task",
            data=task.data,
        )

    def add_comment(self, task: TaskRecord, actor: str, content: str) -> TaskRecord:
        """
        Add a comment to a task and save it.

        Args:
            task: TaskRecord instance
            actor: Comment author display name
            content: Comment text

        Returns:
            Updated TaskRecord
        """
        task.add_comment(actor, content)
        return self.labs_api.update_record(
            record_id=task.id,
            experiment="tasks",
            type="Task",
            data=task.data,
        )

    def add_ai_session(self, task: TaskRecord, actor: str, session_params: dict, **kwargs) -> TaskRecord:
        """
        Add an AI session to a task and save it.

        Args:
            task: TaskRecord instance
            actor: Name of person who triggered the AI session
            session_params: Dict with session parameters
            **kwargs: Additional fields (session_id, status)

        Returns:
            Updated TaskRecord
        """
        task.add_ai_session(actor, session_params, **kwargs)
        return self.labs_api.update_record(
            record_id=task.id,
            experiment="tasks",
            type="Task",
            data=task.data,
        )

    def update_status(self, task: TaskRecord, new_status: str, actor: str) -> TaskRecord:
        """
        Update task status and add event.

        Args:
            task: TaskRecord instance
            new_status: New status value
            actor: Name of actor making the change

        Returns:
            Updated TaskRecord
        """
        old_status = task.status
        task.data["status"] = new_status

        # Add event
        task.add_event(
            event_type="status_changed",
            actor=actor,
            description=f"Status changed from {old_status} to {new_status}",
        )

        return self.labs_api.update_record(
            record_id=task.id,
            experiment="tasks",
            type="Task",
            data=task.data,
        )

    def assign_task(self, task: TaskRecord, assigned_to_name: str, assigned_to_type: str, actor: str) -> TaskRecord:
        """
        Assign task and add event.

        Args:
            task: TaskRecord instance
            assigned_to_name: Display name of assignee
            assigned_to_type: Type of assignee (self, network_manager, program_manager)
            actor: Name of actor making the assignment

        Returns:
            Updated TaskRecord
        """
        task.data["assigned_to_type"] = assigned_to_type
        task.data["assigned_to_name"] = assigned_to_name

        # Add event
        task.add_event(
            event_type="assigned",
            actor=actor,
            description=f"Assigned to {assigned_to_name}",
        )

        return self.labs_api.update_record(
            record_id=task.id,
            experiment="tasks",
            type="Task",
            data=task.data,
        )

    # Connect API Integration Methods

    def search_opportunities(self, query: str = "", limit: int = 100) -> list[dict]:
        """
        Search for opportunities.

        Args:
            query: Search query (name or ID)
            limit: Maximum results

        Returns:
            List of opportunity dicts (raw from API)
        """
        # Call Connect API
        response = self._call_connect_api("/export/opp_org_program_list/")
        data = response.json()

        opportunities_list = data.get("opportunities", [])
        results = []

        query_lower = query.lower().strip()
        for opp_data in opportunities_list:
            # Filter by query if provided
            if query_lower:
                opp_id_match = query_lower.isdigit() and int(query_lower) == opp_data.get("id")
                name_match = query_lower in opp_data.get("name", "").lower()
                if not (opp_id_match or name_match):
                    continue

            results.append(opp_data)

            if len(results) >= limit:
                break

        return results

    def get_opportunity_details(self, opportunity_id: int) -> dict | None:
        """
        Get detailed information about an opportunity.

        Args:
            opportunity_id: Opportunity ID

        Returns:
            Opportunity dict (raw from API) or None
        """
        # Search for this specific opportunity
        response = self._call_connect_api("/export/opp_org_program_list/")
        data = response.json()

        opportunities_list = data.get("opportunities", [])

        for opp_data in opportunities_list:
            if opp_data.get("id") == opportunity_id:
                return opp_data

        return None

    def get_users_from_opportunity(self, opportunity_id: int) -> list[dict]:
        """
        Get users for an opportunity from Connect API.

        Note: The /export/opportunity/<id>/user_data/ endpoint does NOT include user_id,
        only username. This is a limitation of the current data export API.

        Args:
            opportunity_id: Opportunity ID

        Returns:
            List of user dicts with username (no user_id available from API)
        """
        import io
        import logging

        import pandas as pd

        logger = logging.getLogger(__name__)

        # Download user data CSV
        endpoint = f"/export/opportunity/{opportunity_id}/user_data/"
        response = self._call_connect_api(endpoint)

        # Parse CSV from response bytes
        df = pd.read_csv(io.BytesIO(response.content))

        # Log CSV structure for debugging
        logger.info(f"CSV columns for opportunity {opportunity_id}: {list(df.columns)}")
        logger.info(f"CSV has {len(df)} rows")

        users = []
        for idx, row in df.iterrows():
            username = str(row["username"]) if pd.notna(row.get("username")) else None
            if username:
                # Parse all available fields from CSV
                user_dict = {"username": username}

                # Add optional fields if they exist in the CSV
                optional_fields = [
                    "name",
                    "phone_number",
                    "total_visits",
                    "approved_visits",
                    "flagged_visits",
                    "rejected_visits",
                    "last_active",
                    "email",
                ]
                for field in optional_fields:
                    if field in row and pd.notna(row[field]):
                        user_dict[field] = str(row[field]) if not isinstance(row[field], (int, float)) else row[field]

                users.append(user_dict)

        return users

    def get_learning_modules(self, opportunity_id: int) -> list[dict]:
        """
        Get learning modules for an opportunity from Connect API.

        Args:
            opportunity_id: Opportunity ID

        Returns:
            List of learning module dicts with id, slug, name, description, time_estimate
        """
        import logging

        logger = logging.getLogger(__name__)

        # Get full opportunity details including learn_app
        endpoint = f"/export/opportunity/{opportunity_id}/"
        response = self._call_connect_api(endpoint)
        opp_data = response.json()

        logger.info(f"Fetched opportunity {opportunity_id} details for learning modules")

        # Extract learn_app and its modules
        learn_app = opp_data.get("learn_app")
        if not learn_app:
            logger.warning(f"No learn_app found for opportunity {opportunity_id}")
            return []

        modules = learn_app.get("learn_modules", [])
        logger.info(f"Found {len(modules)} learning modules for opportunity {opportunity_id}")

        return modules

    def get_completed_modules(self, opportunity_id: int, username: str | None = None) -> list[dict]:
        """
        Get completed learning modules for an opportunity from Connect API.

        Args:
            opportunity_id: Opportunity ID
            username: Optional username to filter by specific user

        Returns:
            List of completed module dicts with username, module, opportunity_id, date, duration
        """
        import io
        import logging

        import pandas as pd

        logger = logging.getLogger(__name__)

        # Download completed modules CSV
        endpoint = f"/export/opportunity/{opportunity_id}/completed_module/"
        response = self._call_connect_api(endpoint)

        # Parse CSV from response bytes
        df = pd.read_csv(io.BytesIO(response.content))

        logger.info(f"CSV columns for completed modules: {list(df.columns)}")
        logger.info(f"CSV has {len(df)} completed module records")

        # Filter by username if provided
        if username:
            df = df[df["username"] == username]
            logger.info(f"Filtered to {len(df)} records for user {username}")

        completed_modules = []
        for idx, row in df.iterrows():
            module_dict = {
                "username": str(row["username"]) if pd.notna(row.get("username")) else None,
                "module": int(row["module"]) if pd.notna(row.get("module")) else None,
                "opportunity_id": int(row["opportunity_id"]) if pd.notna(row.get("opportunity_id")) else None,
                "date": str(row["date"]) if pd.notna(row.get("date")) else None,
                "duration": str(row["duration"]) if pd.notna(row.get("duration")) else None,
            }
            completed_modules.append(module_dict)

        # Sort by date descending (most recent first)
        completed_modules.sort(key=lambda x: x.get("date") or "", reverse=True)

        return completed_modules
