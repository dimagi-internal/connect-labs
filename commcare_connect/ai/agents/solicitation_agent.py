"""
Solicitation AI agent for creating, managing, and querying solicitations.

Used by both the in-app AI chat panel and the Celery task runner.
"""
import logging
from dataclasses import dataclass

from pydantic_ai import Agent, RunContext
from pydantic_ai.settings import ModelSettings

from commcare_connect.ai.types import UserDependencies
from commcare_connect.solicitations.data_access import SolicitationsDataAccess

logger = logging.getLogger(__name__)

INSTRUCTIONS = """
You are a solicitations assistant for CommCare Connect. You help users create,
manage, and review solicitations (RFPs and EOIs) for community health programs.

You can:
- List and search existing solicitations
- Create new solicitations with all required details
- Update existing solicitations
- List responses to solicitations

When creating a solicitation, ask for key details if not provided:
- Title and description
- Type (EOI or RFP)
- Whether it should be publicly listed
- Application deadline, start/end dates
- Scope of work and estimated scale

Be concise and action-oriented. When the user asks you to create or update
something, call the appropriate tool immediately rather than just describing
what you would do.
"""


@dataclass
class SolicitationAgentDeps:
    """Dependencies for solicitation agent."""

    user_deps: UserDependencies


def _get_data_access(ctx: RunContext[SolicitationAgentDeps]) -> SolicitationsDataAccess:
    """Create a SolicitationsDataAccess from agent context."""
    deps = ctx.deps.user_deps
    program_id = str(deps.program_id) if deps.program_id else None
    return SolicitationsDataAccess(
        request=deps.request,
        program_id=program_id,
    )


def _serialize_solicitation(s) -> dict:
    return {
        "id": s.pk,
        "title": s.title,
        "description": s.description,
        "scope_of_work": s.scope_of_work,
        "solicitation_type": s.solicitation_type,
        "status": s.status,
        "is_public": s.is_public,
        "program_name": s.program_name,
        "application_deadline": str(s.application_deadline) if s.application_deadline else None,
        "expected_start_date": str(s.expected_start_date) if s.expected_start_date else None,
        "expected_end_date": str(s.expected_end_date) if s.expected_end_date else None,
        "estimated_scale": s.estimated_scale,
        "contact_email": s.contact_email,
    }


def create_solicitation_agent_with_model(model: str) -> Agent[SolicitationAgentDeps, str]:
    """Create the solicitation agent with a specific model."""
    logger.info(f"[Solicitation Agent] Creating agent with model: {model}")

    agent = Agent(
        model,
        deps_type=SolicitationAgentDeps,
        output_type=str,
        instructions=INSTRUCTIONS,
        model_settings=ModelSettings(max_tokens=4096),
    )

    @agent.tool
    async def list_solicitations(
        ctx: RunContext[SolicitationAgentDeps],
        status: str | None = None,
        solicitation_type: str | None = None,
    ) -> str:
        """List solicitations, optionally filtered by status or type.

        Args:
            status: Filter by status ('active', 'closed', 'draft', 'awarded').
            solicitation_type: Filter by type ('eoi', 'rfp').
        """
        da = _get_data_access(ctx)
        results = da.get_solicitations(status=status, solicitation_type=solicitation_type)
        serialized = [_serialize_solicitation(s) for s in results]
        if not serialized:
            return "No solicitations found."
        return f"Found {len(serialized)} solicitations: {serialized}"

    @agent.tool
    async def get_solicitation(
        ctx: RunContext[SolicitationAgentDeps],
        solicitation_id: int,
    ) -> str:
        """Get details of a specific solicitation by ID.

        Args:
            solicitation_id: The solicitation record ID.
        """
        da = _get_data_access(ctx)
        result = da.get_solicitation_by_id(solicitation_id)
        if not result:
            return f"Solicitation {solicitation_id} not found."
        return f"Solicitation details: {_serialize_solicitation(result)}"

    @agent.tool
    async def create_solicitation(
        ctx: RunContext[SolicitationAgentDeps],
        title: str,
        description: str,
        solicitation_type: str = "rfp",
        status: str = "draft",
        is_public: bool = True,
        scope_of_work: str = "",
        application_deadline: str | None = None,
        expected_start_date: str | None = None,
        expected_end_date: str | None = None,
        estimated_scale: str = "",
        contact_email: str = "",
    ) -> str:
        """Create a new solicitation.

        Args:
            title: Solicitation title.
            description: Full description of the solicitation.
            solicitation_type: 'eoi' (Expression of Interest) or 'rfp' (Request for Proposal).
            status: 'draft', 'active', 'closed', or 'awarded'.
            is_public: Whether to list publicly.
            scope_of_work: Detailed scope of work.
            application_deadline: Deadline date (YYYY-MM-DD format).
            expected_start_date: Expected start date (YYYY-MM-DD format).
            expected_end_date: Expected end date (YYYY-MM-DD format).
            estimated_scale: Scale description (e.g. '1000 beneficiaries').
            contact_email: Contact email for inquiries.
        """
        deps = ctx.deps.user_deps
        data = {
            "title": title,
            "description": description,
            "solicitation_type": solicitation_type,
            "status": status,
            "is_public": is_public,
            "scope_of_work": scope_of_work,
            "application_deadline": application_deadline,
            "expected_start_date": expected_start_date,
            "expected_end_date": expected_end_date,
            "estimated_scale": estimated_scale,
            "contact_email": contact_email,
            "created_by": deps.user.username if deps.user else "",
        }
        da = _get_data_access(ctx)
        result = da.create_solicitation(data)
        return f"Created solicitation '{result.title}' (ID: {result.pk}, status: {result.status})"

    @agent.tool
    async def update_solicitation(
        ctx: RunContext[SolicitationAgentDeps],
        solicitation_id: int,
        title: str | None = None,
        description: str | None = None,
        solicitation_type: str | None = None,
        status: str | None = None,
        is_public: bool | None = None,
        scope_of_work: str | None = None,
        application_deadline: str | None = None,
        expected_start_date: str | None = None,
        expected_end_date: str | None = None,
        estimated_scale: str | None = None,
        contact_email: str | None = None,
    ) -> str:
        """Update an existing solicitation. Only provided fields are changed.

        Args:
            solicitation_id: The solicitation record ID to update.
            title: New title (optional).
            description: New description (optional).
            solicitation_type: New type - 'eoi' or 'rfp' (optional).
            status: New status - 'draft', 'active', 'closed', 'awarded' (optional).
            is_public: Whether to list publicly (optional).
            scope_of_work: New scope of work (optional).
            application_deadline: New deadline - YYYY-MM-DD (optional).
            expected_start_date: New start date - YYYY-MM-DD (optional).
            expected_end_date: New end date - YYYY-MM-DD (optional).
            estimated_scale: New scale description (optional).
            contact_email: New contact email (optional).
        """
        data = {}
        for field_name, value in [
            ("title", title),
            ("description", description),
            ("solicitation_type", solicitation_type),
            ("status", status),
            ("is_public", is_public),
            ("scope_of_work", scope_of_work),
            ("application_deadline", application_deadline),
            ("expected_start_date", expected_start_date),
            ("expected_end_date", expected_end_date),
            ("estimated_scale", estimated_scale),
            ("contact_email", contact_email),
        ]:
            if value is not None:
                data[field_name] = value

        if not data:
            return "No fields provided to update."

        da = _get_data_access(ctx)
        result = da.update_solicitation(solicitation_id, data)
        return f"Updated solicitation '{result.title}' (ID: {result.pk}). Changed fields: {list(data.keys())}"

    @agent.tool
    async def list_responses(
        ctx: RunContext[SolicitationAgentDeps],
        solicitation_id: int,
    ) -> str:
        """List all responses submitted for a solicitation.

        Args:
            solicitation_id: The solicitation record ID.
        """
        da = _get_data_access(ctx)
        results = da.get_responses_for_solicitation(solicitation_id)
        if not results:
            return f"No responses found for solicitation {solicitation_id}."
        serialized = [
            {
                "id": r.pk,
                "status": r.status,
                "submitted_by": r.submitted_by_name,
                "llo_entity": r.llo_entity_name,
            }
            for r in results
        ]
        return f"Found {len(serialized)} responses: {serialized}"

    @agent.tool
    async def fetch_url(
        ctx: RunContext[SolicitationAgentDeps],
        url: str,
    ) -> str:
        """Fetch the text content of a URL. Use this when the user provides a link
        to reference material, a program page, or any web content they want to
        inform the solicitation.

        Args:
            url: The URL to fetch content from.
        """
        from commcare_connect.solicitations.views import _fetch_url_content

        content = _fetch_url_content(url, max_chars=8000)
        if content.startswith("[Failed"):
            return content
        return f"Content from {url} ({len(content)} chars):\n\n{content}"

    return agent


# Legacy function kept for Celery task compatibility
def get_solicitation_agent() -> Agent:
    """Get a solicitation agent with default model."""
    return create_solicitation_agent_with_model("openai:gpt-4o-mini")
