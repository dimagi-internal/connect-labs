"""What every channel's first message says, and how outbound text is written.

The first message labs sends on a channel tells the worker that an AI may answer and how to
stop the messages. It is added by `service.send` to whichever message is first, whoever
wrote it, so no handler can forget it.

Text is Markdown on the worker's phone, where a bare URL is not tappable: `linkify` turns
each into a Markdown link.
"""

from __future__ import annotations

import re

DISCLOSURE = (
    "_Messages here come from {sender} through Connect. Some replies may be written by an AI "
    "assistant, and people on the team can read this conversation. To stop these messages, reply "
    "STOP, or turn this channel off in your app._"
)

#: A reply that is exactly one of these (any case, surrounding space and punctuation ignored)
#: stops messages on that channel; one of `START_WORDS` turns them back on.
STOP_WORDS = frozenset({"stop", "unsubscribe", "stop all", "end", "quit"})
START_WORDS = frozenset({"start", "unstop", "resume"})


def with_disclosure(text: str, sender: str) -> str:
    line = DISCLOSURE.format(sender=sender or "your program")
    return f"{text}\n\n{line}" if text else line


def _keyword(text: str) -> str:
    return re.sub(r"[\s.!]+$", "", (text or "").strip()).lower()


def is_stop(text: str) -> bool:
    return _keyword(text) in STOP_WORDS


def is_start(text: str) -> bool:
    return _keyword(text) in START_WORDS


# A URL not already part of a Markdown link (`[x](url)`, `<url>`).
_BARE_URL = re.compile(r"(?<![(<\[])\bhttps?://[^\s<>()\[\]]+")
_TRAILING = ".,;:!?'\""


def linkify(text: str) -> str:
    def repl(match: re.Match) -> str:
        url = match.group(0)
        tail = ""
        while url and url[-1] in _TRAILING:
            url, tail = url[:-1], url[-1] + tail
        return f"[{url}]({url}){tail}"

    return _BARE_URL.sub(repl, text or "")
