"""Per-user write rate limit using Django's cache backend.

Counts windowed, not strict rolling-window — good enough for a safety cap,
not a DDoS mitigation. Parseable as '<count>/<m|h|s|d>'.
"""
import re

from django.conf import settings
from django.core.cache import cache

from .tool_registry import MCPToolError

_WINDOW_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}
_PATTERN = re.compile(r"^(\d+)/([smhd])$")


def _parse(spec: str) -> tuple[int, int]:
    m = _PATTERN.match(spec)
    if not m:
        raise ValueError(f"Invalid rate-limit spec: {spec!r}")
    return int(m.group(1)), _WINDOW_SECONDS[m.group(2)]


def enforce_write_limit(user) -> None:
    """Raise MCPToolError(RATE_LIMITED) if user is over the write limit.

    Caller is responsible for only calling this on write tools.
    """
    if user is None or not getattr(user, "is_authenticated", False):
        return

    limit_count, window_sec = _parse(settings.MCP_WRITE_RATE_LIMIT)
    key = f"mcp:writelimit:{user.pk}"
    count = cache.get(key, 0)
    if count >= limit_count:
        raise MCPToolError(
            "RATE_LIMITED",
            f"Write rate limit exceeded ({settings.MCP_WRITE_RATE_LIMIT}). " "Wait and retry.",
        )
    # Increment with expiry. Race is acceptable — off-by-one under contention.
    if count == 0:
        cache.set(key, 1, window_sec)
    else:
        cache.incr(key)
