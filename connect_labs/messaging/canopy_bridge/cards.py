"""A card in the agent's reply: a fenced ``card-html`` block becomes a PersonalID card.

The agent writes its answer as prose; when a picture of the answer helps (a payment summary, a
progress bar), it adds ONE fenced block whose info string is ``card-html``::

    Here is where your payments stand.

    ```card-html
    <!doctype html><html>...</html>
    ```

Labs sends that block as the message's ``index.html`` (``format: card-html``), which the app
shows inline, and the prose around it as the card's caption (and older apps' fallback). Only the
first block is a card; any further one is dropped, so a reply is still one message.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_FENCE = re.compile(r"^[ \t]*(`{3,}|~{3,})[ \t]*card-html[ \t]*\n(.*?)\n[ \t]*\1[ \t]*$", re.M | re.S)


@dataclass(frozen=True)
class Reply:
    text: str
    card_html: str = ""


def split(text: str) -> Reply:
    """The reply's caption and its card (empty when the reply has none)."""
    text = text or ""
    blocks = list(_FENCE.finditer(text))
    if not blocks:
        return Reply(text.strip())
    if len(blocks) > 1:
        logger.info("canopy bridge: a reply carried %d cards; only the first is sent", len(blocks))
    caption = _FENCE.sub("", text)
    caption = re.sub(r"\n{3,}", "\n\n", caption).strip()
    return Reply(caption, blocks[0].group(2).strip())
