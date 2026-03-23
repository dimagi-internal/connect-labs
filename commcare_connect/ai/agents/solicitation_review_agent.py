"""
Solicitation review AI agent for comparative analysis of all responses.

Reviews all responses to a solicitation and generates a comparative ranking
with strengths/weaknesses, risk flags, and a recommended shortlist.
"""

import logging
from dataclasses import dataclass

from pydantic_ai import Agent, RunContext
from pydantic_ai.settings import ModelSettings

from commcare_connect.ai.types import UserDependencies

logger = logging.getLogger(__name__)

INSTRUCTIONS = """You are an expert grant reviewer for a global health regranting platform.
You help funders evaluate solicitation responses by providing objective, comparative analysis.

When reviewing responses, you should:
- Compare all applicants side by side on the evaluation criteria
- Identify specific strengths and weaknesses for each applicant
- Flag risks (inexperience, capacity concerns, vague answers, missing information)
- Recommend a shortlist with clear rationale
- Be fair and evidence-based — cite specific answers
- Use clear section headings (## headers)

Structure your review as:
1. **Executive Summary** — 2-3 sentences on the overall applicant pool quality
2. **Comparative Analysis** — Table or ranked list of applicants with scores per criterion
3. **Risk Flags** — Specific concerns per applicant that the funder should investigate
4. **Recommended Shortlist** — Top applicants with rationale for each
5. **Next Steps** — What the funder should ask/verify before making final decisions

Be direct but constructive. Every applicant deserves fair consideration.
"""


@dataclass
class SolicitationReviewAgentDeps:
    """Dependencies for solicitation review agent."""

    user_deps: UserDependencies
    access_token: str | None = None


def create_solicitation_review_agent_with_model(model: str) -> Agent[SolicitationReviewAgentDeps, str]:
    """Create the solicitation review agent with a specific model."""
    logger.info(f"[Solicitation Review Agent] Creating agent with model: {model}")

    agent = Agent(
        model,
        deps_type=SolicitationReviewAgentDeps,
        output_type=str,
        instructions=INSTRUCTIONS,
        model_settings=ModelSettings(max_tokens=8192),
    )

    @agent.tool
    async def get_solicitation_data(
        ctx: RunContext[SolicitationReviewAgentDeps],
        solicitation_id: int,
    ) -> str:
        """Fetch complete solicitation data including all responses and evaluation criteria.

        Args:
            solicitation_id: ID of the solicitation to review
        """
        from commcare_connect.solicitations.data_access import SolicitationsDataAccess

        try:
            da = SolicitationsDataAccess(
                access_token=ctx.deps.access_token,
                program_id=str(ctx.deps.user_deps.program_id) if ctx.deps.user_deps.program_id else None,
            )

            solicitation = da.get_solicitation_by_id(solicitation_id)
            if not solicitation:
                return f"Solicitation {solicitation_id} not found."

            responses = da.get_responses_for_solicitation(solicitation_id)

            # Format solicitation info
            result = f"# Solicitation: {solicitation.title}\n\n"
            result += f"**Type:** {solicitation.solicitation_type}\n"
            result += f"**Description:** {solicitation.description}\n"
            if solicitation.scope_of_work:
                result += f"**Scope of Work:** {solicitation.scope_of_work}\n"

            # Evaluation criteria
            criteria = solicitation.evaluation_criteria
            if criteria:
                result += "\n## Evaluation Criteria\n"
                for c in criteria:
                    name = c.get("name", "")
                    weight = c.get("weight", "")
                    desc = c.get("description", "")
                    result += f"- **{name}** (weight: {weight}): {desc}\n"

            # Questions
            questions = solicitation.questions
            if questions:
                result += "\n## Questions Asked\n"
                for i, q in enumerate(questions, 1):
                    q_text = q.get("question_text", q.get("text", ""))
                    result += f"{i}. {q_text}\n"

            # Responses
            result += f"\n## Responses ({len(responses)} total)\n\n"
            for resp in responses:
                result += f"### Applicant: {resp.submitted_by_name or resp.org_name or 'Anonymous'}\n"
                result += f"**Organization:** {resp.org_name or 'Not specified'}\n"
                result += f"**Status:** {resp.status}\n"
                answers = resp.responses or {}
                if answers:
                    result += "**Answers:**\n"
                    for key, val in answers.items():
                        result += f"  - {key}: {val}\n"
                result += "\n"

            return result

        except Exception as e:
            logger.error(f"[Solicitation Review] Error fetching data: {e}", exc_info=True)
            return f"Error fetching solicitation data: {str(e)}"

    return agent
