"""Playbooks: what a program's conversations with its Connect Workers are for. (Arrives with the router.)"""

from __future__ import annotations


def for_thread(thread) -> dict | None:
    """The playbook a thread runs under, at the version it opened with, or None."""
    return None
