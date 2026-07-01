"""
Session store for AI demo message history.

Uses Redis (via django cache) to store message history per session.
"""
import json
import logging
from datetime import datetime, timezone

from django.core.cache import cache

logger = logging.getLogger(__name__)

# Redis key prefix for sessions
SESSION_KEY_PREFIX = "ai_demo_session"
# TTL in seconds (7 days)
SESSION_TTL = 7 * 24 * 60 * 60


def get_session_key(session_id: str) -> str:
    """Get Redis key for a session."""
    return f"{SESSION_KEY_PREFIX}:{session_id}"


def get_message_history(session_id: str) -> list[dict]:
    """
    Retrieve message history for a session.

    Args:
        session_id: The session identifier

    Returns:
        List of message dictionaries with 'role' and 'content' keys
    """
    if not session_id:
        return []

    try:
        key = get_session_key(session_id)
        data = cache.get(key)

        if data is None:
            return []

        # Handle both string (JSON) and dict formats
        if isinstance(data, str):
            session_data = json.loads(data)
        else:
            session_data = data

        messages = session_data.get("messages", [])
        return messages
    except Exception as e:
        logger.error(f"Error retrieving message history for session {session_id}: {e}")
        return []


def save_message_history(session_id: str, messages: list[dict], extend_ttl: bool = True) -> bool:
    """
    Save message history for a session.

    Args:
        session_id: The session identifier
        messages: List of message dictionaries with 'role' and 'content' keys
        extend_ttl: If True, extend the TTL (default: True)

    Returns:
        True if successful, False otherwise
    """
    if not session_id:
        return False

    try:
        key = get_session_key(session_id)
        now = datetime.now(timezone.utc).isoformat()

        # Try to get existing data to preserve metadata
        existing_data = cache.get(key)
        if existing_data:
            if isinstance(existing_data, str):
                session_data = json.loads(existing_data)
            else:
                session_data = existing_data
            created_at = session_data.get("created_at", now)
        else:
            created_at = now

        session_data = {
            "messages": messages,
            "created_at": created_at,
            "last_accessed": now,
        }

        # Store in Redis with TTL
        cache.set(key, json.dumps(session_data), timeout=SESSION_TTL)
        return True
    except Exception as e:
        logger.error(f"Error saving message history for session {session_id}: {e}")
        return False


def add_message_to_history(session_id: str, role: str, content: str) -> bool:
    """
    Add a single message to the session history.

    Args:
        session_id: The session identifier
        role: Message role ('user' or 'assistant')
        content: Message content

    Returns:
        True if successful, False otherwise
    """
    messages = get_message_history(session_id)
    messages.append({"role": role, "content": content})
    return save_message_history(session_id, messages)


def clear_session_history(session_id: str) -> bool:
    """
    Clear message history for a session.

    Args:
        session_id: The session identifier

    Returns:
        True if successful, False otherwise
    """
    if not session_id:
        return False

    try:
        key = get_session_key(session_id)
        cache.delete(key)
        return True
    except Exception as e:
        logger.error(f"Error clearing session history for {session_id}: {e}")
        return False
