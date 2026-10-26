#!/usr/bin/env python3
"""
Weekly changelog generator for Connect Labs.

Triggered by .github/workflows/weekly-changelog.yml every Monday (and on
workflow_dispatch for manual runs).

1. Reads merged PRs from --prs-file (JSON array from GitHub API)
2. Extracts ## Product Description from each PR body
3. Skips PRs with empty Product Description (infra/refactors)
4. Asks Claude to group the PRs, pick each group's prod page from the routes
   in the repo's urls.py files, and write a user-friendly weekly summary
5. Prepends a new row to the Confluence changelog page
6. Posts a summary message to Slack #connect-labs

Requires env vars:
    ANTHROPIC_API_KEY
    CONFLUENCE_API_TOKEN
    CONFLUENCE_EMAIL
    SLACK_WEBHOOK_URL
"""

import argparse
import ast
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

LABS_BASE_URL = os.environ.get("LABS_BASE_URL", "https://labs.connect.dimagi.com")
REPO_ROOT = Path(__file__).resolve().parent.parent
ROOT_URLCONF = "config.urls"

# A route is a linkable page only if a person could open it from a bullet: no
# path parameters, and not an API, auth or machine endpoint.
NON_PAGE_ROUTE = re.compile(
    r"(^|/)(api|o|mcp|__debug__|\.well-known)/"
    r"|(^|/)(callback|initiate|logout|login|token|tokens|jwks|probe|health|test-auth|auth-token)/"
    r"|(^|/)(stream|delete|kill|ping|query|download|bulk-download|stats|check-now|fonts|points|resolve_many"
    r"|clear|clear-context|refresh|refresh-org-data|toggle-view-synthetic|self-service|test-access|upload|load"
    r"|dump|areas|countries|geometry|viewport|search)/"
    r"|\.(json|txt|js|xml)$"
)

MARKETING_PATHS = (
    "connect_labs/prelogin/",
    "connect_labs/templates/prelogin/",
    "connect_labs/static/prelogin/",
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


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Name):
            return func.id
        if isinstance(func, ast.Attribute):
            return func.attr
    return ""


def _pattern_lists(tree: ast.Module) -> list[ast.List]:
    """The list literals assigned to module-level `urlpatterns` (=, += or `[...] + x`)."""

    def lists_in(value: ast.AST) -> list[ast.List]:
        if isinstance(value, ast.List):
            return [value]
        if isinstance(value, ast.BinOp):
            return lists_in(value.left) + lists_in(value.right)
        return []

    found = []
    for stmt in tree.body:
        if isinstance(stmt, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "urlpatterns" for t in stmt.targets
        ):
            found += lists_in(stmt.value)
        elif isinstance(stmt, ast.AugAssign) and isinstance(stmt.target, ast.Name) and stmt.target.id == "urlpatterns":
            found += lists_in(stmt.value)
    return found


def _collect_routes(module: str, prefix: str, root: Path, seen: set[str]) -> list[str]:
    path = root / (module.replace(".", "/") + ".py")
    if module in seen or not path.exists():
        return []
    seen = seen | {module}
    routes = []
    for patterns in _pattern_lists(ast.parse(path.read_text())):
        for entry in patterns.elts:
            if _call_name(entry) != "path" or len(entry.args) < 2:
                continue
            route = entry.args[0]
            if not (isinstance(route, ast.Constant) and isinstance(route.value, str)):
                continue
            target = entry.args[1]
            if _call_name(target) == "include":
                included = target.args[0] if target.args else None
                if isinstance(included, ast.Tuple) and included.elts:
                    included = included.elts[0]
                if isinstance(included, ast.Constant) and isinstance(included.value, str):
                    routes += _collect_routes(included.value, prefix + route.value, root, seen)
            else:
                routes.append(prefix + route.value)
    return routes


def collect_page_routes(root: Path = REPO_ROOT, urlconf: str = ROOT_URLCONF) -> list[str]:
    """Every static page path Labs serves, read from the urls.py files in the checkout.

    Parsed rather than imported, so the job needs no Django install. Routes with
    path parameters and API/auth/machine endpoints are dropped: a bullet can only
    link a page anyone can open as-is.
    """
    pages = set()
    for route in _collect_routes(urlconf, "", root, set()):
        if "<" in route or NON_PAGE_ROUTE.search(route):
            continue
        pages.add("/" + route)
    return sorted(pages)


def page_url(path: str | None, pages: set[str]) -> str:
    """The prod URL for a page path the grouping step chose, or "" if it isn't a real page."""
    if not path:
        return ""
    path = "/" + path.strip().lstrip("/")
    return f"{LABS_BASE_URL}{path}" if path in pages and path != "/" else ""


GROUP_MAX = 12  # maximum feature groups passed to the summary step

MODEL_GROUPING = "claude-sonnet-4-6"  # needs to reason across many PRs
MODEL_SUMMARY = "claude-haiku-4-5-20251001"  # short structured task

GROUP_SYSTEM_PROMPT = """\
You cluster pull requests into logical feature groups for a program-management web app changelog.
Audience: non-developer program staff.

Return ONLY a JSON array — no markdown fences, no explanation. Each element:
  {
    "title": "short noun phrase naming the feature (user-facing, not a PR title)",
    "description": "1-2 sentences: what the user now sees or can do",
    "pr_numbers": [list of all PR numbers in this group],
    "lead_pr": single most representative PR number,
    "type": "New" | "Improvement" | "Fix" | "Infra",
    "page": the one path from the PAGES list where a user sees this change, or null
  }

Rules:
- PRs working toward the same feature or initiative belong in one group
- Infrastructure, developer tooling, and CI changes all merge into a single Infra group
- Order groups by user impact: broadest/most visible first
- Maximum 12 groups total
- Skip PRs with no user-visible effect
- "page" must be copied exactly from the PAGES list. Use each PR's changed files to
  find the app it touched. Choose null when no listed page shows the change, or when
  the change has no single home (a cross-cutting fix, a background job, an admin tool)
"""

WEEKLY_SYSTEM_PROMPT = """\
You write weekly product updates for the Connect Labs web application.
Audience: non-developer program staff who use the app regularly.

Format rules:
- Lead with 1-2 sentences summarizing the week's overall theme
- Then a bullet list, one bullet per feature group (a group may represent multiple related PRs)
- Use plain language: no code terms, no GitHub references, no jargon
- "Fixed:" prefix for bug-fix-only groups; start other bullets with the capability directly
- Keep each bullet under 30 words
- Maximum 10 bullets total
- Skip "Infra" groups unless the improvement is directly user-visible
- Do NOT include PR numbers or links in the body text (added separately)
- End each bullet with the tag of the group it describes, e.g. {g3}; a bullet that \
merges several groups takes the tag of the main one
- Return only the summary text — no preamble, no trailing notes
- Groups marked [category: marketing]: prefix every bullet from that group with "[Marketing] "
- Groups marked [category: mixed]: split into separate bullets for app and marketing changes; \
prefix only the marketing bullets with "[Marketing] "
- Groups marked [category: app]: no prefix
"""


def _files_summary(files: list[str], limit: int = 15) -> str:
    if not files:
        return ""
    more = f" (+{len(files) - limit} more)" if len(files) > limit else ""
    return "\nChanged files: " + ", ".join(files[:limit]) + more


def group_prs_by_feature(client: anthropic.Anthropic, prs: list[dict], pages: list[str] | None = None) -> list[dict]:
    """Cluster PRs into logical feature groups ordered by user impact, each with its prod page."""
    pages = pages or []
    pr_text = "\n\n".join(
        f"PR #{p['number']} [marketing: {p.get('category', 'app')}]: {p['title']}"
        f"{_files_summary(p.get('files', []))}\n{p['description']}"
        for p in prs
    )
    resp = client.messages.create(
        model=MODEL_GROUPING,
        max_tokens=3000,
        system=GROUP_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": "PAGES:\n" + "\n".join(pages) + "\n\nPRS:\n\n" + pr_text}],
    )
    raw = resp.content[0].text.strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE).strip()
    try:
        groups = json.loads(raw)
        if not isinstance(groups, list):
            raise ValueError(f"expected list, got {type(groups).__name__}")
    except Exception as e:
        print(f"  [warn] Grouping parse failed ({e}) — falling back to one-group-per-PR", file=sys.stderr)
        if len(prs) > 10:
            dropped = [p["number"] for p in prs[10:]]
            print(f"  [warn] Fallback capped at 10 of {len(prs)} PRs — skipped: {dropped}", file=sys.stderr)
        return [
            {
                "title": p["title"],
                "description": p["description"],
                "pr_numbers": [p["number"]],
                "lead_pr": p["number"],
                "lead_url": p.get("url", ""),
                "category": p.get("category", "app"),
                "page_url": "",
            }
            for p in prs[:10]
        ]

    pr_by_number = {p["number"]: p for p in prs}
    for group in groups:
        pr_nums = group.get("pr_numbers") or []
        if "lead_pr" not in group and pr_nums:
            group["lead_pr"] = pr_nums[0]
        lead = pr_by_number.get(group.get("lead_pr", 0), {})
        group["lead_url"] = lead.get("url", "")
        group["page_url"] = page_url(group.get("page"), set(pages))
        # Derive marketing category from constituent PRs
        pr_cats = [pr_by_number.get(n, {}).get("category", "app") for n in pr_nums]
        if "marketing" in pr_cats:
            group["category"] = "marketing"
        elif "mixed" in pr_cats:
            group["category"] = "mixed"
        else:
            group["category"] = "app"

    return groups[:GROUP_MAX]


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
                    "files": files,
                    "category": classify_pr(files),
                }
            )
    return result


def generate_weekly_summary(client: anthropic.Anthropic, groups: list[dict]) -> str:
    """Ask Claude for a user-friendly weekly summary, one bullet per feature group."""
    group_text = "\n\n".join(
        f"Group {{g{i}}} [category: {g.get('category', 'app')}]: {g['title']}\n{g['description']}"
        for i, g in enumerate(groups)
    )
    resp = client.messages.create(
        model=MODEL_SUMMARY,
        max_tokens=1000,
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
                "content": f"Feature groups for this week:\n\n{group_text}\n\nWrite the weekly changelog entry.",
            }
        ],
    )
    return link_bullets(resp.content[0].text.strip(), groups)


GROUP_TAG = re.compile(r"\s*\{g(\d+)\}")


def link_bullets(summary: str, groups: list[dict]) -> str:
    """Replace each bullet's {gN} tag with a link to that group's prod page.

    A tag with no page (or an out-of-range index) is just removed, so the bullet
    reads as before; the link is never guessed.
    """

    def to_link(match: re.Match) -> str:
        index = int(match.group(1))
        url = groups[index].get("page_url", "") if index < len(groups) else ""
        return f" [Open in Labs →]({url})" if url else ""

    return "\n".join(GROUP_TAG.sub(to_link, line).rstrip() for line in summary.split("\n"))


MD_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")


def _inline_storage(text: str) -> str:
    """Escape one line for Confluence storage format, keeping **bold** and [label](url) links."""
    content = re.sub(r"\*\*(.*?)\*\*", r"<strong>\1</strong>", html.escape(text))
    return MD_LINK.sub(r'<a href="\2">\1</a>', content)


def markdown_to_storage(text: str) -> str:
    """Convert simple markdown bullets and bold to Confluence storage format."""
    lines = text.split("\n")
    html_lines = []
    in_ul = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("• ") or stripped.startswith("- "):
            content = _inline_storage(stripped[2:].strip())
            if not in_ul:
                html_lines.append("<ul>")
                in_ul = True
            html_lines.append(f"  <li>{content}</li>")
        else:
            if in_ul:
                html_lines.append("</ul>")
                in_ul = False
            if stripped:
                content = _inline_storage(stripped)
                html_lines.append(f"<p>{content}</p>")
    if in_ul:
        html_lines.append("</ul>")
    return "\n".join(html_lines)


def build_changelog_row(week_date: str, summary: str, groups: list[dict]) -> str:
    """Build a Confluence storage format table row for the changelog."""
    summary_html = markdown_to_storage(summary)
    pr_links = " ".join(
        f'<a href="{g["lead_url"]}">#{g["lead_pr"]}</a>' for g in groups[:10] if g.get("lead_url") and g.get("lead_pr")
    )
    return (
        "<tr>"
        f'<td><time datetime="{week_date}" /></td>'
        f"<td><strong>Week of {datetime.strptime(week_date, '%Y-%m-%d').strftime('%b %d, %Y')}</strong>"
        f"{summary_html}</td>"
        f"<td>{pr_links}</td>"
        "</tr>"
    )


def _to_slack_mrkdwn(text: str) -> str:
    """Convert Markdown bold (**text**) and links ([label](url)) to Slack mrkdwn."""
    text = re.sub(r"\*\*(.*?)\*\*", r"*\1*", text)
    return MD_LINK.sub(r"<\2|\1>", text)


def post_to_slack(webhook_url: str, week_label: str, summary: str, groups: list[dict]) -> None:
    """Post a Slack Block Kit message to #connect-labs."""
    pr_links_text = "  |  ".join(
        f"<{g['lead_url']}|#{g['lead_pr']}>" for g in groups[:10] if g.get("lead_url") and g.get("lead_pr")
    )
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

    ai_client = anthropic.Anthropic(max_retries=5)  # 529 overloaded is transient; retry more
    confluence = ConfluenceClient()

    print(f"\nGrouping {len(prs)} PR(s) into feature clusters...")
    pages = collect_page_routes()
    print(f"  {len(pages)} linkable page(s) found in the urls.py files")
    groups = group_prs_by_feature(ai_client, prs, pages)
    print(f"  {len(groups)} group(s) identified:")
    for g in groups:
        print(f"    [{g.get('type', '?')}] {g['title']} (PRs: {g['pr_numbers']}) → {g['page_url'] or 'no page'}")

    print("\nGenerating weekly summary...")
    summary = generate_weekly_summary(ai_client, groups)
    print(f"\n{summary}\n")

    print("Updating Confluence changelog...")
    existing = confluence.get_page(CHANGELOG_PAGE_ID)
    if f'datetime="{week_date}"' in existing.get("body_storage", ""):
        print(f"  [skip] Confluence entry for {week_date} already exists — skipping write.")
    else:
        row_html = build_changelog_row(week_date, summary, groups)
        confluence.prepend_table_row(CHANGELOG_PAGE_ID, row_html)
        print(f"  ✓ Row prepended to page {CHANGELOG_PAGE_ID}")

    slack_url = os.environ.get("SLACK_WEBHOOK_URL", "")
    if slack_url:
        print("Posting to Slack...")
        try:
            post_to_slack(slack_url, week_label, summary, groups)
            print("  ✓ Slack message sent to #connect-labs")
        except Exception as e:
            print(f"  [warn] Slack notification failed (non-fatal): {e}")
    else:
        print("  [warn] SLACK_WEBHOOK_URL not set — skipping Slack notification")

    print("\nDone.")


if __name__ == "__main__":
    main()
