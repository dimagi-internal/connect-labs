"""Render the walkthrough's specimen documents from their Markdown sources to PDF.

The walkthrough FILES these documents -- the seeder uploads the quotation and
the certificate of analysis, and the programme lead uploads the registration
certificate on camera -- so they must be files a person would really hold: a
PDF, not a Markdown source. The `.md` files stay the source of truth (they
diff); run this after editing one and commit both.

Every document is a SPECIMEN: invented organisation, product, number and price.

    ~/emdash/repositories/connect-labs/.venv/bin/python \
        scripts/walkthroughs/supply-chlorine-stopgap/make_specimens.py
"""

from __future__ import annotations

from pathlib import Path

import markdown
from playwright.sync_api import sync_playwright

HERE = Path(__file__).with_name("documents")

STYLE = """
body { font-family: Georgia, 'Times New Roman', serif; color: #1f2937; margin: 48px 56px; font-size: 12pt; }
h1 { font-size: 18pt; border-bottom: 2px solid #1f2937; padding-bottom: 6px; }
blockquote { margin: 0 0 18px; padding: 6px 12px; background: #fef3c7; border-left: 4px solid #d97706;
             font-family: Helvetica, Arial, sans-serif; font-size: 9pt; color: #78350f; }
table { border-collapse: collapse; width: 100%; margin: 14px 0; font-size: 11pt; }
th, td { border: 1px solid #9ca3af; padding: 6px 8px; text-align: left; vertical-align: top; }
th { background: #f3f4f6; }
"""


def main() -> None:
    sources = sorted(HERE.glob("*.md"))
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        for source in sources:
            body = markdown.markdown(source.read_text(), extensions=["tables"])
            page.set_content(f"<html><head><style>{STYLE}</style></head><body>{body}</body></html>")
            target = source.with_suffix(".pdf")
            page.pdf(path=str(target), format="A4", print_background=True)
            print(target.relative_to(HERE.parent.parent.parent.parent))
        browser.close()


if __name__ == "__main__":
    main()
