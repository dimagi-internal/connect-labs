#!/usr/bin/env python3
"""
Per-merge documentation updater.

Triggered by .github/workflows/docs-update.yml on every push to main.
Reads PR metadata from environment/files, determines which feature areas
changed, and uses Claude to update both the GitHub Pages markdown and
the Confluence summary page for each affected area.

Requires env vars:
    ANTHROPIC_API_KEY
    CONFLUENCE_API_TOKEN
    CONFLUENCE_EMAIL
    PR_DATA_FILE      (path to JSON file with PR data)
    CHANGED_FILES_FILE (path to newline-separated changed files list)
    DIFF_FILE         (path to truncated PR diff)
"""

import html
import json
import os
import re
import sys
from pathlib import Path

import anthropic

sys.path.insert(0, str(Path(__file__).parent))
from confluence_client import ConfluenceClient  # isort: skip  # noqa: E402

# Maps source path prefixes to feature documentation names
PREFIX_TO_FEATURE = {
    "connect_labs/audit/": "Audit & QA Review",
    "connect_labs/workflow/": "Workflow Engine",
    "connect_labs/tasks/": "Task Management",
    "connect_labs/solicitations/": "Solicitations",
    "connect_labs/custom_analysis/": "Custom Analysis",
    "connect_labs/coverage/": "Coverage Maps",
    "connect_labs/ai/": "AI Features",
    "docs/WORKFLOW_EDITOR_QUICKSTART.md": "Connect MCP & Safe Mode",
    "docs/SAFE_MODE.md": "Connect MCP & Safe Mode",
}

# Confluence page IDs for each feature summary page (created by bootstrap)
FEATURE_PAGE_IDS = {
    "Audit & QA Review": "3927900187",
    "Workflow Engine": "3927801864",
    "Task Management": "3928293395",
    "Solicitations": "3927179271",
    "Custom Analysis": "3928817669",
    "Coverage Maps": "3927867398",
    "AI Features": "3928817690",
    "Connect MCP & Safe Mode": "3927801885",
}

# Maps feature names to their GitHub Pages markdown file paths in user_docs/
FEATURE_TO_DOC_FILE = {
    "Audit & QA Review": "user_docs/audit.md",
    "Workflow Engine": "user_docs/workflow-engine.md",
    "Task Management": "user_docs/task-management.md",
    "Solicitations": "user_docs/solicitations.md",
    "Custom Analysis": "user_docs/custom-analysis.md",
    "Coverage Maps": "user_docs/coverage-maps.md",
    "AI Features": "user_docs/ai-features.md",
    "Connect MCP & Safe Mode": "user_docs/connect-mcp-safe-mode.md",
}

GITHUB_PAGES_BASE = "https://dimagi-internal.github.io/connect-labs/docs"

CONFLUENCE_SUMMARY_SYSTEM_PROMPT = """\
You write concise Confluence summary pages for Connect Labs features.
Audience: non-developer program staff who use the app daily for field program management.

Rules:
- Plain English only — no code, no GitHub references, no technical jargon
- 2–3 sentence overview paragraph describing what the feature does and who it is for
- 4–5 bullet points highlighting the key tasks or capabilities users get from this feature
- Return valid JSON with exactly two keys: "paragraph" (string) and "bullets" (array of strings)
- No preamble, no trailing explanation — return only the JSON object
"""

DOC_SYSTEM_PROMPT = """\
You maintain user help documentation for Connect Labs at labs.connect.dimagi.com.
Audience: non-developer program staff who use the app daily for field program management.

Rules:
- Plain English only — no code, no GitHub references, no technical jargon
- Make minimal targeted changes: add or update only the section(s) directly
  affected by the new change; preserve everything else exactly as-is
- Keep all existing Mermaid diagrams, tables, admonitions, and formatting
- If adding a new capability, insert it in the most logical existing section
  rather than always appending to the end
- Do not add or remove major sections unless the feature fundamentally changed
- Return valid MkDocs-flavored Markdown (admonitions use !!! syntax)
- Do not include explanatory wrapper text — return only the updated document
"""

UI_CHANGE_KEYWORDS = {
    "screen",
    "page",
    "button",
    "display",
    "view",
    "shows",
    "added a",
    "now shows",
    "interface",
    "tab",
    "modal",
    "panel",
    "column",
    "table",
    "dashboard",
    "icon",
    "label",
    "menu",
    "form",
    "filter",
    "toggle",
}


def extract_product_description(pr_body: str) -> str:
    """Extract the ## Product Description section from a PR body."""
    match = re.search(
        r"##\s*Product Description\s*\n(.*?)(?=\n##|\Z)",
        pr_body,
        re.DOTALL | re.IGNORECASE,
    )
    if not match:
        return ""
    text = match.group(1).strip()
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL).strip()
    return text


def affected_features(changed_files: list[str]) -> set[str]:
    """Return set of feature names touched by this PR."""
    features = set()
    for path in changed_files:
        for prefix, feature in PREFIX_TO_FEATURE.items():
            if path.startswith(prefix) or path == prefix:
                features.add(feature)
    return features


def has_user_visible_changes(
    client: anthropic.Anthropic,
    pr_title: str,
    product_description: str,
    diff_excerpt: str,
) -> bool:
    """Ask Haiku if this PR has user-visible changes."""
    resp = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=5,
        system=[
            {
                "type": "text",
                "text": (
                    "Classify whether a code change has user-visible effects on a web app. "
                    "Reply only with YES or NO."
                ),
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[
            {
                "role": "user",
                "content": (
                    f"PR title: {pr_title}\n\n"
                    f"Product description: {product_description or '(empty)'}\n\n"
                    f"Diff excerpt:\n{diff_excerpt[:3000]}\n\n"
                    "Does this change anything a non-developer user of the web app would notice?"
                ),
            }
        ],
    )
    return resp.content[0].text.strip().upper().startswith("Y")


def has_ui_changes(product_description: str) -> bool:
    """Heuristic: does this PR likely include screenshot-worthy UI changes?"""
    desc_lower = product_description.lower()
    return any(kw in desc_lower for kw in UI_CHANGE_KEYWORDS)


def update_markdown_file(
    client: anthropic.Anthropic,
    feature: str,
    pr_title: str,
    product_description: str,
) -> bool:
    """Update the GitHub Pages .md file. Returns True if file was changed."""
    doc_path = Path(FEATURE_TO_DOC_FILE[feature])
    if not doc_path.exists():
        print(f"  [warn] {doc_path} not found — skipping markdown update")
        return False

    current_content = doc_path.read_text(encoding="utf-8")

    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        system=[
            {
                "type": "text",
                "text": DOC_SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[
            {
                "role": "user",
                "content": (
                    f"## Current documentation for '{feature}'\n\n"
                    f"{current_content}\n\n"
                    "---\n\n"
                    f"## New change to incorporate\n\n"
                    f"PR title: {pr_title}\n\n"
                    f"What changed (plain English):\n{product_description}\n\n"
                    "Update the documentation to reflect this change. Return only the "
                    "updated markdown — no preamble, no trailing explanation."
                ),
            }
        ],
    )

    new_content = resp.content[0].text.strip()
    if not new_content:
        print(f"  [warn] AI returned empty response for {feature} — skipping")
        return False
    if len(new_content) > 100_000:
        raise ValueError(f"AI response for {feature} is unexpectedly large ({len(new_content):,} chars)")
    if new_content != current_content:
        resolved = doc_path.resolve()
        if not resolved.is_relative_to(Path("user_docs").resolve()):
            raise ValueError(f"Refusing to write outside user_docs/: {doc_path}")
        doc_path.write_text(new_content + "\n", encoding="utf-8")
        return True
    return False


def _generate_confluence_body(
    client: anthropic.Anthropic,
    feature: str,
    doc_content: str,
    github_url: str,
    recent_change: str = "",
) -> str:
    """Generate Confluence storage HTML (paragraph + bullets + link) for a feature summary."""
    user_content = f"Feature: {feature}\n\nDocumentation:\n{doc_content[:6000]}"
    if recent_change:
        user_content += f"\n\nRecent change to incorporate: {recent_change}"
    user_content += '\n\nReturn a JSON object with "paragraph" and "bullets" keys.'

    resp = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=500,
        system=[
            {
                "type": "text",
                "text": CONFLUENCE_SUMMARY_SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": user_content}],
    )
    raw = resp.content[0].text.strip()

    try:
        # Strip markdown code fences if present
        if raw.startswith("```"):
            raw = re.sub(r"^```[a-z]*\n?", "", raw)
            raw = re.sub(r"\n?```$", "", raw.rstrip())
        data = json.loads(raw)
        paragraph = str(data.get("paragraph", "")).strip()
        bullets = [str(b).strip() for b in data.get("bullets", []) if str(b).strip()]
    except (json.JSONDecodeError, AttributeError):
        print(f"  [warn] Failed to parse JSON for '{feature}' — using raw text as paragraph")
        paragraph = raw
        bullets = []

    parts = []
    if paragraph:
        parts.append(f"<p>{html.escape(paragraph)}</p>")
    if bullets:
        items = "".join(f"<li>{html.escape(b)}</li>" for b in bullets)
        parts.append(f"<ul>{items}</ul>")
    parts.append(
        f'<p><a href="{github_url}">📖 Read the full guide with diagrams and step-by-step instructions →</a></p>'
    )
    parts.append("<p><em>Last updated automatically.</em></p>")
    return "".join(parts)


def update_confluence_summary(
    confluence: ConfluenceClient,
    feature: str,
    product_description: str,
    client: anthropic.Anthropic,
) -> None:
    """Update the Confluence summary page for this feature."""
    page_id = FEATURE_PAGE_IDS[feature]
    doc_path = Path(FEATURE_TO_DOC_FILE[feature])
    doc_slug = doc_path.stem
    github_url = f"{GITHUB_PAGES_BASE}/{doc_slug}/"

    doc_content = doc_path.read_text(encoding="utf-8") if doc_path.exists() else ""
    body = _generate_confluence_body(client, feature, doc_content, github_url, recent_change=product_description)
    page = confluence.get_page(page_id)
    confluence.update_page(
        page_id=page_id,
        title=page["title"],
        body_storage=body,
        version_number=page["version_number"],
    )


def post_screenshot_comment(pr_number: str, features: list[str]) -> None:
    """Post a single GitHub PR comment listing all features that need screenshot updates."""
    gh_token = os.environ.get("GH_TOKEN", "")
    repo = os.environ.get("GH_REPO", "")
    if not gh_token or not repo or not pr_number or not features:
        return
    import urllib.request

    feature_list = "\n".join(f"- `{f}`" for f in sorted(features))
    body = (
        "📷 **Screenshot update needed**: The following doc pages may need updated "
        "screenshots to reflect UI changes in this PR. See `user_docs/assets/screenshots/`.\n\n"
        f"{feature_list}"
    )
    data = json.dumps({"body": body}).encode()
    req = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/issues/{pr_number}/comments",
        data=data,
        headers={
            "Authorization": f"Bearer {gh_token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        print(f"  [warn] Could not post GitHub comment: {e}")


def main() -> None:
    # Load inputs
    pr_data_file = os.environ.get("PR_DATA_FILE", "/tmp/pr_data.json")
    changed_files_file = os.environ.get("CHANGED_FILES_FILE", "/tmp/changed_files.txt")
    diff_file = os.environ.get("DIFF_FILE", "/tmp/pr_diff.txt")

    try:
        with open(pr_data_file) as f:
            pr_data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"Could not read PR data: {e}")
        sys.exit(0)  # Don't fail the workflow for missing PR data

    pr_number = str(pr_data.get("number", ""))
    pr_title = pr_data.get("title", "")
    pr_body = pr_data.get("body") or ""

    product_description = extract_product_description(pr_body)
    if not product_description:
        print("No Product Description found — skipping doc update.")
        return

    changed_files = []
    try:
        changed_files = Path(changed_files_file).read_text().splitlines()
    except FileNotFoundError:
        pass

    diff_excerpt = ""
    try:
        diff_excerpt = Path(diff_file).read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        pass

    features = affected_features(changed_files)
    if not features:
        print("No known feature areas affected — skipping doc update.")
        return

    print(f"PR #{pr_number}: {pr_title}")
    print(f"Affected features: {', '.join(sorted(features))}")

    ai_client = anthropic.Anthropic()
    confluence = ConfluenceClient()

    # Validate that all expected Confluence pages are accessible
    missing_pages = []
    for feature in features:
        page_id = FEATURE_PAGE_IDS.get(feature)
        if page_id:
            try:
                confluence.get_page(page_id)
            except Exception:
                missing_pages.append(f"{feature} (id={page_id})")
    if missing_pages:
        print(f"  [error] Confluence pages not accessible: {', '.join(missing_pages)}")
        print("  Cannot update documentation — fix page IDs in FEATURE_PAGE_IDS.")
        sys.exit(1)

    # Single classification call — is this PR user-visible at all?
    if not has_user_visible_changes(ai_client, pr_title, product_description, diff_excerpt):
        print("Classified as non-user-visible — skipping doc update.")
        return

    print("User-visible changes detected. Updating documentation...")
    ui_flag = has_ui_changes(product_description)
    screenshot_features = []

    for feature in sorted(features):
        print(f"\n  Updating: {feature}")
        changed = update_markdown_file(ai_client, feature, pr_title, product_description)
        if changed:
            print(f"    ✓ Markdown updated: {FEATURE_TO_DOC_FILE[feature]}")
        else:
            print("    — No markdown change needed")

        if feature not in [f.split(" (")[0] for f in missing_pages]:
            update_confluence_summary(confluence, feature, product_description, ai_client)
            print(f"    ✓ Confluence summary updated (page {FEATURE_PAGE_IDS[feature]})")

        if ui_flag:
            screenshot_features.append(feature)

    if screenshot_features and pr_number:
        post_screenshot_comment(pr_number, screenshot_features)
        print(f"\n  ✓ Screenshot reminder posted on PR #{pr_number} for: {', '.join(screenshot_features)}")

    print("\nDone.")


if __name__ == "__main__":
    main()
