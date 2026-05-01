#!/usr/bin/env python3
"""
Regenerate all Confluence summary pages from current user_docs/*.md files.

Run this after updating documentation or improving the summary format to
refresh all 8 feature pages with paragraph + bullet-point summaries.

Usage:
    python automation/regenerate_confluence.py

Requires env vars:
    ANTHROPIC_API_KEY
    CONFLUENCE_API_TOKEN
    CONFLUENCE_EMAIL
"""

import os
import sys
from pathlib import Path

import anthropic

sys.path.insert(0, str(Path(__file__).parent))
from confluence_client import ConfluenceClient  # isort: skip  # noqa: E402
from update_docs import (  # isort: skip  # noqa: E402
    FEATURE_PAGE_IDS,
    FEATURE_TO_DOC_FILE,
    GITHUB_PAGES_BASE,
    _generate_confluence_body,
)


def main() -> None:
    ai_client = anthropic.Anthropic()
    confluence = ConfluenceClient()

    email = os.environ.get("CONFLUENCE_EMAIL", "(not set)")
    print(f"Authenticating as: {email}")
    try:
        me = confluence._get("/rest/api/user/current")
        print(f"Confluence user: {me.get('displayName')} / {me.get('emailAddress')} (accountId={me.get('accountId')})")
    except Exception as e:
        print(f"  [warn] Could not fetch current Confluence user: {e}")
    print("Regenerating Confluence summary pages from user_docs/...")
    print()

    errors = []
    for feature, page_id in FEATURE_PAGE_IDS.items():
        doc_path = Path(FEATURE_TO_DOC_FILE[feature])
        doc_slug = doc_path.stem
        github_url = f"{GITHUB_PAGES_BASE}/{doc_slug}/"

        if not doc_path.exists():
            print(f"  [skip] {doc_path} not found — {feature}")
            continue

        doc_content = doc_path.read_text(encoding="utf-8")
        print(f"  {feature}...", end=" ", flush=True)
        try:
            body = _generate_confluence_body(ai_client, feature, doc_content, github_url)
            page = confluence.get_page(page_id)
            confluence.update_page(
                page_id=page_id,
                title=page["title"],
                body_storage=body,
                version_number=page["version_number"],
            )
            print("✓")
        except Exception as e:
            print(f"✗ {e}")
            errors.append(f"{feature}: {e}")

    print()
    if errors:
        print("Errors:")
        for err in errors:
            print(f"  {err}")
        sys.exit(1)
    else:
        print("Done.")


if __name__ == "__main__":
    main()
