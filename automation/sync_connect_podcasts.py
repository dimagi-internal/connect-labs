#!/usr/bin/env python3
"""Sync the Connect podcast cards on the Insights page from dimagi.com/podcast.

The High-Impact Growth podcast page tags each episode with the product(s) it
covers (``data-product="…connect…"``). That page is the single source of truth
for "which episodes are Connect episodes". Rather than hand-mirror those cards
onto the Connect Insights page, this script scans the live page and regenerates
them.

It rewrites only the block between these markers in the Insights template:

    <!-- CONNECT-PODCASTS:START … -->
        …generated cards…
    <!-- CONNECT-PODCASTS:END -->

Run it whenever you want to refresh (or wire it into CI with --check):

    python3 automation/sync_connect_podcasts.py            # rewrite the template
    python3 automation/sync_connect_podcasts.py --check     # exit 1 if out of date

Only the podcast cards are touched. Titles, episode numbers, dates, and links
come straight from the source. Excerpts use a hand-polished override when one
exists for that episode (see EXCERPT_OVERRIDES), otherwise the episode's own
description with light house-style cleanup (Connect naming, no em dashes) that
you can refine afterwards.
"""

from __future__ import annotations

import argparse
import html
import re
import urllib.request
from pathlib import Path

PODCAST_URL = "https://dimagi.com/podcast/"
DIMAGI_ORIGIN = "https://dimagi.com"
TEMPLATE = Path(__file__).resolve().parent.parent / "connect_labs/templates/prelogin/home.html"
START = "<!-- CONNECT-PODCASTS:START"
END = "<!-- CONNECT-PODCASTS:END -->"
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126 Safari/537.36"
)

MONTHS = {
    "jan": "01",
    "feb": "02",
    "mar": "03",
    "apr": "04",
    "may": "05",
    "jun": "06",
    "jul": "07",
    "aug": "08",
    "sep": "09",
    "oct": "10",
    "nov": "11",
    "dec": "12",
}

# Hand-polished excerpts, keyed by the episode's dimagi.com slug. Keeps curated
# copy stable across re-runs; any episode not listed here falls back to the
# cleaned-up source description (which you can then polish and promote up here).
EXCERPT_OVERRIDES = {
    "improving-health-worker-jobs": (
        "A roundtable on Connect, the platform built on four pillars, learn, "
        "deliver, verify, and pay, that lets community health workers opt into "
        "additional work and get paid for it."
    ),
    "financing-community-health-africa": (
        "Nan Chen of Africa Frontline First on the financing challenge behind "
        "community health worker programs, and how smarter money and "
        "coordination are moving them forward."
    ),
}


def clean_copy(text: str) -> str:
    """Apply the Connect site's house style to a snippet of source copy."""
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    text = text.replace("CommCare Connect", "Connect")
    # No em/en dashes (see feedback_no_em_dashes): turn them into commas.
    text = re.sub(r"\s*[—–]\s*", ", ", text)
    return text


def esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def parse_episodes(page_html: str) -> list[dict]:
    """Return the Connect-tagged episodes, newest first as the page lists them."""
    episodes = []
    for art in re.findall(r'<article class="episode-card"[^>]*>.*?</article>', page_html, re.S):
        product = re.search(r'data-product="([^"]*)"', art)
        if not product or "connect" not in product.group(1).split():
            continue

        def field(cls: str) -> str | None:
            m = re.search(r'<div class="%s">(.*?)</div>' % cls, art, re.S)
            return re.sub("<[^>]+>", "", m.group(1)).strip() if m else None

        num_date = field("episode-num") or ""
        m = re.match(r"Episode\s+(\d+)\s*(?:&middot;|·)\s*(\w+)\s+(\d{4})", num_date)
        if not m:
            print(f"  ! skipping unparseable episode header: {num_date!r}")
            continue
        number, mon, year = m.group(1), m.group(2)[:3].lower(), m.group(3)
        link = re.search(r'<a class="episode-link" href="([^"]+)"', art)
        # Require whitespace after "p" so this matches the description <p …>
        # and not <polygon …> in the play-icon SVG.
        excerpt_m = re.search(r"<p\s[^>]*>(.*?)</p>", art, re.S)
        raw_excerpt = re.sub("<[^>]+>", "", excerpt_m.group(1)) if excerpt_m else ""
        href = link.group(1) if link else ""
        slug = href.strip("/").split("/")[-1]
        episodes.append(
            {
                "number": number,
                "datetime": f"{year}-{MONTHS.get(mon, '01')}",
                "display": f"{mon.upper()} {year}",
                "title": clean_copy(field("episode-title") or ""),
                "url": DIMAGI_ORIGIN + href if href.startswith("/") else href,
                "slug": slug,
                "excerpt": EXCERPT_OVERRIDES.get(slug, clean_copy(raw_excerpt)),
            }
        )
    return episodes


# The media block is identical for every card; keeping it as a constant keeps
# the long <img>/<svg> lines out of the f-string and under the line limit.
CARD_MEDIA = (
    '          <div class="blog-card-media">\n'
    "            <img src=\"{% static 'prelogin/images/blog/"
    'podcast-cover.jpg\' %}" alt="High-Impact Growth podcast '
    'cover art" loading="lazy">\n'
    '            <span class="blog-card-play" aria-hidden="true">'
    '<svg viewBox="0 0 24 24" fill="currentColor">'
    '<polygon points="6 4 20 12 6 20 6 4"/></svg></span>\n'
    "          </div>"
)


def render_cards(episodes: list[dict]) -> str:
    cards = []
    for ep in episodes:
        open_tag = (
            '        <a class="blog-card blog-card--podcast" '
            f'href="{ep["url"]}" target="_blank" rel="noopener" '
            'data-type="podcast" data-program="">'
        )
        body = "\n".join(
            [
                '          <div class="blog-card-body">',
                '            <div class="blog-card-meta">',
                '              <span class="blog-tag">' f'Podcast &middot; Episode {ep["number"]}</span>',
                f'              <time datetime="{ep["datetime"]}">' f'{ep["display"]}</time>',
                "            </div>",
                f"            <h3>{esc(ep['title'])}</h3>",
                '            <p class="blog-card-excerpt">' f"{esc(ep['excerpt'])}</p>",
                '            <span class="blog-card-more">' "Listen on the Dimagi podcast ↗</span>",
                "          </div>",
            ]
        )
        cards.append(f"{open_tag}\n{CARD_MEDIA}\n{body}\n        </a>")
    return "\n\n".join(cards)


def build_block(episodes: list[dict]) -> str:
    return (
        f"{START} (generated by automation/sync_connect_podcasts.py — do not edit by hand) -->\n"
        f"{render_cards(episodes)}\n"
        f"        {END}"
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--check",
        action="store_true",
        help="don't write; exit 1 if the template is out of sync",
    )
    args = ap.parse_args()

    print(f"Fetching {PODCAST_URL} …")
    req = urllib.request.Request(PODCAST_URL, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as resp:
        page_html = resp.read().decode("utf-8", "replace")

    episodes = parse_episodes(page_html)
    print(f"Connect-tagged episodes: {len(episodes)}")
    for ep in episodes:
        note = "" if ep["slug"] in EXCERPT_OVERRIDES else "  (auto excerpt)"
        print(f"  - Ep {ep['number']} ({ep['display']}) {ep['title']}{note}")
    if not episodes:
        print("No Connect episodes found — refusing to blank the section.")
        return 2

    source = TEMPLATE.read_text(encoding="utf-8")
    m = re.search(re.escape(START) + r".*?" + re.escape(END), source, re.S)
    if not m:
        print(
            f"ERROR: markers not found in {TEMPLATE}.\n" f"Add a {START} … {END} block around the podcast cards first."
        )
        return 2

    new_block = build_block(episodes)
    updated = source[: m.start()] + new_block + source[m.end() :]

    if updated == source:
        print("Already in sync — no changes.")
        return 0
    if args.check:
        print("OUT OF SYNC — run without --check to update.")
        return 1
    TEMPLATE.write_text(updated, encoding="utf-8")
    print(f"Updated {TEMPLATE.relative_to(TEMPLATE.parents[3])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
