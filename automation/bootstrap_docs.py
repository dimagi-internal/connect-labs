#!/usr/bin/env python3
"""
One-time bootstrap: create Confluence summary pages for each feature area.

Usage:
    python scripts/bootstrap_docs.py

Requires env vars:
    CONFLUENCE_EMAIL
    CONFLUENCE_API_TOKEN

Reads user_docs/*.md (already written) and creates a short Confluence summary
page per feature under parent page 3916103691 (Connect Labs Documentation).
Prints page IDs — paste into FEATURE_PAGE_IDS in update_docs.py.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from confluence_client import ConfluenceClient  # isort: skip  # noqa: E402

PARENT_PAGE_ID = "3916103691"  # Connect Labs Documentation
GITHUB_PAGES_BASE = "https://dimagi-internal.github.io/connect-labs/docs"

FEATURES = [
    {
        "title": "Audit & QA Review",
        "slug": "audit",
        "summary": (
            "Review field worker visits for quality assurance. Create audit sessions, "
            "assess images and form responses, and use AI-powered validation to ensure "
            "data accuracy."
        ),
    },
    {
        "title": "Workflow Engine",
        "slug": "workflow-engine",
        "summary": (
            "Configure and run structured dashboards that pull live data from CommCare. "
            "Each workflow shows field worker performance metrics and lets you drill into "
            "individual records."
        ),
    },
    {
        "title": "Task Management",
        "slug": "task-management",
        "summary": (
            "Create and track follow-up tasks for field workers. Assign tasks, leave comments, "
            "monitor status changes, and trigger automated outreach via the OCS bot."
        ),
    },
    {
        "title": "Solicitations",
        "slug": "solicitations",
        "summary": (
            "Manage requests for proposals (RFPs) and expressions of interest (EOIs). "
            "Create solicitations, review responses, score submissions, and award funding."
        ),
    },
    {
        "title": "Custom Analysis",
        "slug": "custom-analysis",
        "summary": (
            "Program-specific dashboards for nutrition monitoring, maternal and child health, "
            "and audit quality reviews. Each dashboard shows aggregated metrics for your program."
        ),
    },
    {
        "title": "Coverage Maps",
        "slug": "coverage-maps",
        "summary": (
            "Visualize delivery unit boundaries and service points on an interactive map. "
            "Filter by field worker or geographic area to understand coverage patterns."
        ),
    },
    {
        "title": "AI Features",
        "slug": "ai-features",
        "summary": (
            "AI assistants embedded throughout Connect Labs — in workflow and pipeline editors, "
            "audit reviews, and solicitation management. Use natural language to make changes "
            "without writing code."
        ),
    },
    {
        "title": "Connect MCP & Safe Mode",
        "slug": "connect-mcp-safe-mode",
        "summary": (
            "Edit Labs workflows using Claude Code from the command line — no coding required. "
            "Safe Mode ensures patient data stays protected by restricting what Claude can access."
        ),
    },
]

SUMMARY_TEMPLATE = """<p>{summary}</p>
<p>
  <a href="{github_pages_url}">📖 Read the full guide with diagrams and step-by-step instructions →</a>
</p>
<p><em>Last updated automatically.</em></p>"""


def main():
    client = ConfluenceClient()

    print("Creating Confluence summary pages under Connect Labs Documentation...")
    print()

    created = {}
    for feature in FEATURES:
        github_url = f"{GITHUB_PAGES_BASE}/{feature['slug']}/"
        body = SUMMARY_TEMPLATE.format(
            summary=feature["summary"],
            github_pages_url=github_url,
        )

        result = client.create_child_page(
            parent_id=PARENT_PAGE_ID,
            title=feature["title"],
            body_storage=body,
        )
        page_id = result["id"]
        created[feature["title"]] = page_id
        print(f"  ✓ {feature['title']} → page ID {page_id}")

    print()
    print("=" * 60)
    print("Paste this into FEATURE_PAGE_IDS in scripts/update_docs.py:")
    print()
    print("FEATURE_PAGE_IDS = {")
    for title, page_id in created.items():
        key = title
        print(f'    "{key}": "{page_id}",')
    print("}")


if __name__ == "__main__":
    main()
