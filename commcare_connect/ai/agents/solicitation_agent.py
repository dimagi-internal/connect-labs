from datetime import date

from pydantic import BaseModel
from pydantic_ai import Agent, RunContext
from pydantic_ai.toolsets import FunctionToolset

from commcare_connect.ai.types import UserDependencies
from commcare_connect.solicitations.data_access import SolicitationsDataAccess
from commcare_connect.solicitations.models import SolicitationRecord

INSTRUCTIONS = """
You are a helpful assistant for working with solicitations.
You can help users list and find solicitations. Be concise and helpful.

If the user does not specify a filter, just look up all of the solicitations you have access to.
"""


class SolicitationData(BaseModel):
    """Solicitation information."""

    id: int
    title: str
    description: str
    scope_of_work: str
    solicitation_type: str  # 'eoi' or 'rfp'
    status: str  # 'active', 'closed', 'draft'
    is_public: bool
    program_name: str
    application_deadline: date | None = None
    expected_start_date: date | None = None
    expected_end_date: date | None = None
    estimated_scale: str
    contact_email: str
    date_created: str | None = None
    date_modified: str | None = None

    @classmethod
    def from_solicitation_record(cls, record: SolicitationRecord) -> "SolicitationData":
        """Create SolicitationData from a SolicitationRecord."""
        return cls(
            id=record.id,
            title=record.title,
            description=record.description,
            scope_of_work=record.scope_of_work,
            solicitation_type=record.solicitation_type,
            status=record.status,
            is_public=record.is_public,
            program_name=record.program_name,
            application_deadline=record.application_deadline,
            expected_start_date=record.expected_start_date,
            expected_end_date=record.expected_end_date,
            estimated_scale=record.estimated_scale,
            contact_email=record.contact_email,
        )


async def list_solicitations(
    ctx: RunContext["UserDependencies"],
    status: str | None = None,
    solicitation_type: str | None = None,
) -> list[SolicitationData]:
    """List solicitations with optional filters.

    Args:
        ctx: The run context with user dependencies.
        status: Filter by status ('active', 'closed', 'draft').
        solicitation_type: Filter by type ('eoi', 'rfp').
    """
    if not ctx.deps.request:
        raise ValueError("Request object is required to access solicitations")

    # program_id is required in UserDependencies and validated at initialization
    data_access = SolicitationsDataAccess(request=ctx.deps.request, program_id=str(ctx.deps.program_id))

    solicitations = data_access.get_solicitations(
        status=status,
        solicitation_type=solicitation_type,
    )

    return [SolicitationData.from_solicitation_record(sol) for sol in solicitations]


class ProgramData(BaseModel):
    """Program information."""

    id: int
    name: str
    organization: str
    currency: str | None = None
    delivery_type: str | None = None


class OrganizationData(BaseModel):
    """Organization information."""

    id: int | None = None
    slug: str
    name: str


class OpportunityData(BaseModel):
    """Opportunity information."""

    id: int
    name: str
    program: int | None = None


async def get_program_details(ctx: RunContext["UserDependencies"]) -> ProgramData:
    """Get details about the current program.

    Args:
        ctx: The run context with user dependencies.

    Returns:
        ProgramData with program information.
    """
    if not ctx.deps.request:
        raise ValueError("Request object is required to access program details")

    # Get program from user's OAuth data
    user = ctx.deps.user
    program_id = ctx.deps.program_id

    # Check if user has programs data (available from session org data)
    if hasattr(user, "programs"):
        for program in user.programs:
            if program.get("id") == program_id:
                return ProgramData(
                    id=program_id,
                    name=program.get("name", "Unknown Program"),
                    organization=program.get("organization", "Unknown Organization"),
                    currency=program.get("currency"),
                    delivery_type=program.get("delivery_type"),
                )

    # Fallback: if program not found in user's programs, raise error
    raise ValueError(f"Program {program_id} not found in user's accessible programs")


async def list_programs(ctx: RunContext["UserDependencies"]) -> list[ProgramData]:
    """List all programs the user has access to.

    Args:
        ctx: The run context with user dependencies.

    Returns:
        List of ProgramData with program information.
    """
    user = ctx.deps.user

    # Check if user has programs data (available from session org data)
    if not hasattr(user, "programs"):
        return []

    programs = []
    for program in user.programs:
        programs.append(
            ProgramData(
                id=program.get("id"),
                name=program.get("name", "Unknown Program"),
                organization=program.get("organization", "Unknown Organization"),
                currency=program.get("currency"),
                delivery_type=program.get("delivery_type"),
            )
        )

    return programs


async def list_organizations(ctx: RunContext["UserDependencies"]) -> list[OrganizationData]:
    """List all organizations the user is a member of.

    Args:
        ctx: The run context with user dependencies.

    Returns:
        List of OrganizationData with organization information.
    """
    user = ctx.deps.user

    # Check if user has organizations data (available from session org data)
    if not hasattr(user, "organizations"):
        return []

    organizations = []
    for org in user.organizations:
        organizations.append(
            OrganizationData(
                id=org.get("id"),
                slug=org.get("slug", ""),
                name=org.get("name", "Unknown Organization"),
            )
        )

    return organizations


async def list_opportunities(ctx: RunContext["UserDependencies"]) -> list[OpportunityData]:
    """List all opportunities the user has access to.

    Args:
        ctx: The run context with user dependencies.

    Returns:
        List of OpportunityData with opportunity information.
    """
    user = ctx.deps.user

    # Check if user has opportunities data (available from session org data)
    if not hasattr(user, "opportunities"):
        return []

    opportunities = []
    for opp in user.opportunities:
        opportunities.append(
            OpportunityData(
                id=opp.get("id"),
                name=opp.get("name", "Unknown Opportunity"),
                program=opp.get("program"),
            )
        )

    return opportunities


# TODO: Implement create_solicitation function
# def create_solicitation(
#     ctx: RunContext["UserDependencies"], solicitation_data: SolicitationData
# ) -> SolicitationData:
#     """Create a solicitation.
#
#     Args:
#         solicitation_data: The solicitation data.
#     """
#     pass


# TODO: Implement update_solicitation function
# def update_solicitation(
#     ctx: RunContext["UserDependencies"], solicitation_data: SolicitationData
# ) -> SolicitationData:
#     """Update a solicitation.
#
#     Args:
#         solicitation_data: The solicitation data.
#     """
#     pass


# TODO: Implement delete_solicitation function
# def delete_solicitation(
#     ctx: RunContext["UserDependencies"], solicitation_id: int
# ) -> SolicitationData:
#     """Delete a solicitation.
#
#     Args:
#         solicitation_id: The solicitation ID.
#     """
#     pass


solicitation_toolset = FunctionToolset(
    tools=[
        list_solicitations,
        get_program_details,
        list_programs,
        list_organizations,
        list_opportunities,
        # TODO: Add create_solicitation, update_solicitation, delete_solicitation
    ]
)

# Lazy-loaded agent instance
_agent_instance = None


def get_solicitation_agent() -> Agent:
    """
    Get or create the solicitation agent instance.

    This function lazy-loads the agent to avoid requiring OPENAI_API_KEY
    at import time. The agent is only created when actually needed.

    Returns:
        Agent: The solicitation agent instance

    Raises:
        ValueError: If OPENAI_API_KEY is not set when the agent is first accessed
    """
    global _agent_instance
    if _agent_instance is None:
        _agent_instance = Agent(
            "openai:gpt-4o-mini",
            instructions=INSTRUCTIONS,
            deps_type=UserDependencies,
            toolsets=[solicitation_toolset],
            retries=2,
        )
    return _agent_instance
