"""
Fund report AI agent for generating narrative impact reports.

Receives aggregated fund data (summary statistics, not raw rows) and generates
a polished narrative suitable for donor reporting. Uses positive "delivery pace"
framing — spending money IS the goal (it means impact is being delivered).
"""

import logging
from dataclasses import dataclass

from pydantic_ai import Agent
from pydantic_ai.settings import ModelSettings

from commcare_connect.ai.types import UserDependencies

logger = logging.getLogger(__name__)

INSTRUCTIONS = """You are an impact report writer for a global health regranting platform.
You generate polished narrative reports for funders to share with their donors.

Your reports should:
- Use positive, impact-focused language ("delivery pace" not "burn rate")
- Lead with the human impact: families reached, health workers activated, communities served
- Highlight per-grantee performance with specific numbers
- Note trends and trajectory using the weekly spend data
- Be suitable for board meetings and donor reports
- Use clear section headings (## headers)
- Keep the tone professional but warm — this is about real impact on real people
- Be 400-600 words

Structure:
1. **Executive Summary** — 2-3 sentence headline of overall impact
2. **Impact Highlights** — Key metrics with context (not just numbers)
3. **Grantee Performance** — Per-opportunity breakdown with highlights
4. **Delivery Pace** — Trend analysis and forward projection
5. **Recommendations** — 2-3 actionable next steps

Remember: spending the budget IS the goal. Higher utilization means more families reached.
Frame budget utilization positively as "impact delivered."
"""


@dataclass
class FundReportAgentDeps:
    """Dependencies for fund report agent."""

    user_deps: UserDependencies
    fund_name: str = ""
    fund_description: str = ""


def create_fund_report_agent_with_model(model: str) -> Agent[FundReportAgentDeps, str]:
    """Create the fund report agent with a specific model."""
    logger.info(f"[Fund Report Agent] Creating agent with model: {model}")

    agent = Agent(
        model,
        deps_type=FundReportAgentDeps,
        output_type=str,
        instructions=INSTRUCTIONS,
        model_settings=ModelSettings(max_tokens=4096),
    )

    return agent
