"""
Application coach AI agent for helping applicants strengthen their responses.

Reads the solicitation description, questions, and evaluation criteria (which are
already publicly visible in the form), then provides specific, actionable suggestions
to improve the applicant's draft answers.

Security: MUST NOT expose other applicants' responses. Only sees the current
applicant's draft answers plus solicitation data.
"""

import logging
from dataclasses import dataclass

from pydantic_ai import Agent
from pydantic_ai.settings import ModelSettings

from commcare_connect.ai.types import UserDependencies

logger = logging.getLogger(__name__)

INSTRUCTIONS = """You are a supportive application coach for a global health grant platform.
You help local organizations in low-resource settings write stronger grant applications.

Your role:
- Read the applicant's draft answers alongside the evaluation criteria
- Provide specific, actionable suggestions to strengthen each answer
- Be encouraging but honest — help them put their best foot forward
- Suggest what to add, what to elaborate on, and what to restructure
- Point out where their answer directly addresses (or misses) evaluation criteria
- Use simple, clear language — many applicants may not be native English speakers
- Never write the answers for them — coach, don't ghostwrite
- Never modify or auto-submit their answers

Structure your feedback as:
1. **Overall Impression** — 2-3 sentences on the application's strengths
2. **Per-Question Feedback** — Specific suggestions for each answer
3. **Tips to Stand Out** — 2-3 general tips based on the evaluation criteria

Remember: you're leveling the playing field. Small organizations in rural areas
deserve the same quality coaching that large NGOs get from their grants teams.
Connect doesn't just help funders find partners — it helps partners become fundable.
"""


@dataclass
class ApplicationCoachAgentDeps:
    """Dependencies for application coach agent."""

    user_deps: UserDependencies


def create_application_coach_agent_with_model(model: str) -> Agent[ApplicationCoachAgentDeps, str]:
    """Create the application coach agent with a specific model."""
    logger.info(f"[Application Coach Agent] Creating agent with model: {model}")

    agent = Agent(
        model,
        deps_type=ApplicationCoachAgentDeps,
        output_type=str,
        instructions=INSTRUCTIONS,
        model_settings=ModelSettings(max_tokens=4096),
    )

    return agent
