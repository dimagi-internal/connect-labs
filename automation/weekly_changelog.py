#!/usr/bin/env python3
"""
Weekly changelog generator for Connect Labs.

Triggered by .github/workflows/weekly-changelog.yml every Monday (and on
workflow_dispatch for manual runs).

1. Reads merged PRs from --prs-file (JSON array from GitHub API)
2. Extracts ## Product Description from each PR body
3. Skips PRs with empty Product Description (infra/refactors)
4. Asks Claude to generate a user-friendly weekly summary
5. Prepends a new row to the Confluence changelog page
6. Posts a summary message to Slack #connect-labs

Requires env vars:
    ANTHROPIC_API_KEY
    CONFLUENCE_API_TOKEN
    CONFLUENCE_EMAIL
    SLACK_WEBHOOK_URL
"""

import argparse
import html
import json
import os
import re
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import anthropic

sys.path.insert(0, str(Path(__file__).parent))
from confluence_client import ConfluenceClient  # isort: skip  # noqa: E402

CHANGELOG_PAGE_ID = "3918528513"  # Connect Labs Changelog

MARKETING_PATHS = (
    "commcare_connect/prelogin/",
    "commcare_connect/templates/prelogin/",
    "commcare_connect/static/prelogin/",
)


def classify_pr(files: list[str]) -> str:
    """Return 'marketing', 'app', or 'mixed' based on which paths a PR touched."""
    if not files:
        return "app"
    marketing = [f for f in files if any(f.startswith(p) for p in MARKETING_PATHS)]
    app = [f for f in files if not any(f.startswith(p) for p in MARKETING_PATHS)]
    if marketing and not app:
        return "marketing"
    if app and not marketing:
        return "app"
    return "mixed"


def fetch_pr_files(pr_number: int, repo: str) -> list[str]:
    """Return list of filenames changed in a PR, via the gh CLI."""
    try:
        result = subprocess.run(
            ["gh", "api", f"repos/{repo}/pulls/{pr_number}/files", "--jq", ".[].filename"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except subprocess.TimeoutExpired:
        print(f"  [warn] gh api timed out for PR #{pr_number}", file=sys.stderr)
        return []
    if result.returncode != 0:
        print(f"  [warn] gh api failed for PR #{pr_number}: {result.stderr.strip()}", file=sys.stderr)
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


RANK_SYSTEM_PROMPT = """\
You rank pull requests by user impact for a program management web app. \
Return ONLY a JSON array of PR numbers (integers), highest impact first. \
Rank by: breadth (how many users affected), novelty (new capability > improvement > fix), \
visibility (noticeable to users > silent backend). No explanation, no markdown — \
just the raw JSON array.
"""

WEEKLY_SYSTEM_PROMPT = """\
You write weekly product updates for the Connect Labs web application.
Audience: non-developer program staff who use the app regularly.

Format rules:
- Lead with 1-2 sentences summarizing the week's overall theme
- Then a bullet list, one bullet per significant user-visible change
- Use plain language: no code terms, no GitHub references, no jargon
- "Fixed:" prefix for bug fixes; start other bullets with the capability directly
- Keep each bullet under 25 words
- Maximum 8 bullets total
- Omit infra, refactoring, and developer-tooling changes entirely
- Do NOT include PR numbers or links in the body text (added separately)
- Return only the summary text — no preamble, no trailing notes
- PRs marked [category: marketing]: prefix every bullet from that PR with "[Marketing] "
- PRs marked [category: mixed]: split the description into separate bullets for the app \
changes and the marketing/website changes; prefix only the marketing bullets with "[Marketing] "
- PRs marked [category: app]: no prefix on bullets
"""


def extract_product_description(body: str) -> str:
    """Extract the ## Product Description section from a PR body."""
    if not body:
        return ""
    match = re.search(
        r"##\s*Product Description\s*\n(.*?)(?=\n##|\Z)",
        body,
        re.DOTALL | re.IGNORECASE,
    )
    if not match:
        return ""
    text = match.group(1).strip()
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL).strip()
    return text


def load_user_visible_prs(prs_file: str) -> list[dict]:
    """Load merged PRs, returning only those with a non-empty Product Description."""
    with open(prs_file) as f:
        prs = json.load(f)
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    result = []
    for pr in prs:
        desc = extract_product_description(pr.get("body") or "")
        if desc:
            # Skip file fetching in local dev (no GITHUB_REPOSITORY set)
            files = fetch_pr_files(pr["number"], repo) if repo else []
            result.append(
                {
                    "number": pr["number"],
                    "title": pr["title"],
                    "url": pr.get("html_url", ""),
                    "merged_at": pr.get("merged_at", ""),
                    "description": desc,
                    "category": classify_pr(files),
                }
            )
    return result


RANK_TOP_N = 15  # PRs passed to the summary step


def rank_prs_by_impact(client: anthropic.Anthropic, prs: list[dict]) -> list[dict]:
    """Return prs re-ordered by impact (highest first), capped at RANK_TOP_N."""
    pr_text = "\n\n".join(f"PR #{p['number']}: {p['title']}\n{p['description']}" for p in prs)
    resp = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=300,
        system=RANK_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": pr_text}],
    )
    try:
        numbers = json.loads(resp.content[0].text.strip())
    except Exception as e:
        print(f"  [warn] Ranking parse failed ({e}) — using original order", file=sys.stderr)
        return prs[:RANK_TOP_N]
    pr_by_number = {p["number"]: p for p in prs}
    ranked = [pr_by_number[n] for n in numbers if n in pr_by_number]
    # Append any PRs Claude omitted so we don't silently drop them
    seen = {p["number"] for p in ranked}
    ranked += [p for p in prs if p["number"] not in seen]
    return ranked[:RANK_TOP_N]


def generate_weekly_summary(client: anthropic.Anthropic, prs: list[dict]) -> str:
    """Ask Claude for a user-friendly weekly summary."""
    pr_text = "\n\n".join(
        f"PR #{p['number']} [category: {p.get('category', 'app')}]: {p['title']}\n{p['description']}" for p in prs
    )
    resp = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=800,
        system=[
            {
                "type": "text",
                "text": WEEKLY_SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[
            {
                "role": "user",
                "content": (f"Changes merged this week:\n\n{pr_text}\n\n" "Write the weekly changelog entry."),
            }
        ],
    )
    return resp.content[0].text.strip()


def markdown_to_storage(text: str) -> str:
    """Convert simple markdown bullets and bold to Confluence storage format."""
    lines = text.split("\n")
    html_lines = []
    in_ul = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("• ") or stripped.startswith("- "):
            content = html.escape(stripped[2:].strip())
            content = re.sub(r"\*\*(.*?)\*\*", r"<strong>\1</strong>", content)
            if not in_ul:
                html_lines.append("<ul>")
                in_ul = True
            html_lines.append(f"  <li>{content}</li>")
        else:
            if in_ul:
                html_lines.append("</ul>")
                in_ul = False
            if stripped:
                content = re.sub(r"\*\*(.*?)\*\*", r"<strong>\1</strong>", html.escape(stripped))
                html_lines.append(f"<p>{content}</p>")
    if in_ul:
        html_lines.append("</ul>")
    return "\n".join(html_lines)


def build_changelog_row(week_date: str, summary: str, prs: list[dict]) -> str:
    """Build a Confluence storage format table row for the changelog."""
    summary_html = markdown_to_storage(summary)
    pr_links = " ".join(f'<a href="{p["url"]}">#{p["number"]}</a>' for p in prs[:10] if p.get("url"))
    return (
        "<tr>"
        f'<td><time datetime="{week_date}" /></td>'
        f"<td><strong>Week of {datetime.strptime(week_date, '%Y-%m-%d').strftime('%b %d, %Y')}</strong>"
        f"{summary_html}</td>"
        f"<td>{pr_links}</td>"
        "</tr>"
    )


def _to_slack_mrkdwn(text: str) -> str:
    """Convert standard Markdown bold (**text**) to Slack mrkdwn bold (*text*)."""
    return re.sub(r"\*\*(.*?)\*\*", r"*\1*", text)


def post_to_slack(webhook_url: str, week_label: str, summary: str, prs: list[dict]) -> None:
    """Post a Slack Block Kit message to #connect-labs."""
    pr_links_text = "  |  ".join(f"<{p['url']}|#{p['number']}>" for p in prs[:10] if p.get("url"))
    changelog_url = "https://dimagi.atlassian.net/wiki/spaces/connect/pages/3918528513"

    payload = {
        "blocks": [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": f"Connect Labs — {week_label}"},
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": _to_slack_mrkdwn(summary)},
            },
            {
                "type": "context",
                "elements": [
                    {"type": "mrkdwn", "text": f"PRs: {pr_links_text}"},
                    {"type": "mrkdwn", "text": f"<{changelog_url}|Full changelog →>"},
                ],
            },
        ]
    }

    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        webhook_url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        if resp.status != 200:
            raise RuntimeError(f"Slack webhook returned {resp.status}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prs-file", required=True, help="Path to merged PRs JSON file")
    args = parser.parse_args()

    today = datetime.now(timezone.utc)
    week_date = today.strftime("%Y-%m-%d")
    week_label = f"Week of {today.strftime('%b %d, %Y')}"

    prs = load_user_visible_prs(args.prs_file)

    if not prs:
        print("No user-visible changes this week.")
        # Still post to Slack so the channel stays active
        slack_url = os.environ.get("SLACK_WEBHOOK_URL", "")
        if slack_url:
            payload = {
                "blocks": [
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"*Connect Labs — {week_label}*\nNo user-visible changes this week.",
                        },
                    }
                ]
            }
            data = json.dumps(payload).encode()
            req = urllib.request.Request(
                slack_url,
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=15)
        return

    print(f"Found {len(prs)} PR(s) with user-visible changes:")
    for pr in prs:
        print(f"  #{pr['number']}: {pr['title']}")

    ai_client = anthropic.Anthropic()
    confluence = ConfluenceClient()

    if len(prs) > RANK_TOP_N:
        print(f"\nRanking {len(prs)} PRs by impact (keeping top {RANK_TOP_N})...")
        prs_for_summary = rank_prs_by_impact(ai_client, prs)
        print(f"  Top {len(prs_for_summary)} selected:")
        for pr in prs_for_summary:
            print(f"    #{pr['number']}: {pr['title']}")
    else:
        prs_for_summary = prs

    print("\nGenerating weekly summary...")
    summary = generate_weekly_summary(ai_client, prs_for_summary)
    print(f"\n{summary}\n")

    print("Updating Confluence changelog...")
    existing = confluence.get_page(CHANGELOG_PAGE_ID)
    if f'datetime="{week_date}"' in existing.get("body_storage", ""):
        print(f"  [skip] Entry for {week_date} already exists — skipping duplicate run.")
        return
    row_html = build_changelog_row(week_date, summary, prs_for_summary)
    confluence.prepend_table_row(CHANGELOG_PAGE_ID, row_html)
    print(f"  ✓ Row prepended to page {CHANGELOG_PAGE_ID}")

    slack_url = os.environ.get("SLACK_WEBHOOK_URL", "")
    if slack_url:
        print("Posting to Slack...")
        try:
            post_to_slack(slack_url, week_label, summary, prs_for_summary)
            print("  ✓ Slack message sent to #connect-labs")
        except Exception as e:
            print(f"  [warn] Slack notification failed (non-fatal): {e}")
    else:
        print("  [warn] SLACK_WEBHOOK_URL not set — skipping Slack notification")

    print("\nDone.")


if __name__ == "__main__":
    main()
