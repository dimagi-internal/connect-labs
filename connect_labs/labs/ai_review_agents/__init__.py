"""
AI Review Agents Package.

Provides a framework for AI-powered review agents that can validate
and analyze various types of data (images, form submissions, etc.).

Usage:
    from connect_labs.labs.ai_review_agents import get_agent, register, list_agents

    # Get an agent by ID
    agent = get_agent("scale_validation")
    result = agent.review(context)

    # Register a new agent
    @register
    class MyAgent(BaseAIReviewAgent):
        agent_id = "my_agent"
        ...

    # List all available agents
    for agent_id, agent_cls in list_agents():
        print(f"{agent_id}: {agent_cls.name}")
"""

from connect_labs.labs.ai_review_agents.base import AIReviewAgentError, BaseAIReviewAgent
from connect_labs.labs.ai_review_agents.registry import get_agent, list_agents, register, registry
from connect_labs.labs.ai_review_agents.types import ReviewContext, ReviewResult, ReviewStatus

__all__ = [
    # Base classes
    "BaseAIReviewAgent",
    "AIReviewAgentError",
    # Types
    "ReviewContext",
    "ReviewResult",
    "ReviewStatus",
    # Registry
    "registry",
    "register",
    "get_agent",
    "list_agents",
]
