import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))
import update_docs  # noqa: E402


def test_no_feature_maps_to_a_developer_doc():
    """Developer docs are written by hand for engineers on other systems; the plain-English
    updater must never be pointed at one."""
    assert not [f for f in update_docs.FEATURE_TO_DOC_FILE.values() if update_docs.is_developer_doc(f)]


def test_a_developer_doc_is_never_rewritten(monkeypatch):
    monkeypatch.setitem(update_docs.FEATURE_TO_DOC_FILE, "Embedding", "user_docs/developers/embedding.md")
    client = MagicMock()
    assert update_docs.update_markdown_file(client, "Embedding", "title", "description") is False
    client.messages.create.assert_not_called()
