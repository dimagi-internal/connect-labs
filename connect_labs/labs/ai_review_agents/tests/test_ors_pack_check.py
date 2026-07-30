"""
Tests for ORSPackCheckAgent -- mocks the Anthropic SDK call so these run
without real credentials or network access, covering the request/response
plumbing (match/no_match/error mapping, code-fence stripping, missing-image
validation) that a live model call can't exercise deterministically.
"""

from unittest.mock import MagicMock, patch

from connect_labs.labs.ai_review_agents.agents.ors_pack_check import ORSPackCheckAgent
from connect_labs.labs.ai_review_agents.types import ReviewContext


def _mock_response(text: str):
    resp = MagicMock()
    resp.content = [MagicMock(text=text)]
    return resp


class TestORSPackCheckAgent:
    def test_agent_registers_with_expected_id(self):
        agent = ORSPackCheckAgent()
        assert agent.agent_id == "ors_pack_check"
        assert agent.requires_reading is False

    def test_missing_image_errors_without_calling_api(self):
        agent = ORSPackCheckAgent()
        context = ReviewContext(images={})
        result = agent.review(context)
        assert result.status.value == "error"
        assert "Missing photo" in result.errors[0]

    @patch("anthropic.Anthropic")
    def test_match_true_returns_passed_with_numeric_confidence(self, mock_anthropic_cls):
        mock_client = MagicMock()
        mock_client.messages.create.return_value = _mock_response(
            '{"match": true, "confidence_percent": 88, "reason": "Clearly the ORASURE Kit box"}'
        )
        mock_anthropic_cls.return_value = mock_client

        agent = ORSPackCheckAgent()
        result = agent.review(ReviewContext(images={"photo": b"fake-jpeg-bytes"}))

        assert result.passed
        assert result.confidence == 0.88
        assert result.details["confidence_percent"] == 88
        assert "88%" in result.details["pass_label"]
        assert "please confirm" in result.details["pass_label"]
        assert result.details["reason"] == "Clearly the ORASURE Kit box"

    @patch("anthropic.Anthropic")
    def test_match_false_returns_failed_with_soft_suggestion_badge(self, mock_anthropic_cls):
        mock_client = MagicMock()
        mock_client.messages.create.return_value = _mock_response(
            '{"match": false, "confidence_percent": 72, "reason": "Different branded product (CHASTEE)"}'
        )
        mock_anthropic_cls.return_value = mock_client

        agent = ORSPackCheckAgent()
        result = agent.review(ReviewContext(images={"photo": b"fake-jpeg-bytes"}))

        assert result.failed
        assert result.confidence == 0.72
        assert "72%" in result.details["badge_label"]
        assert "please confirm" in result.details["badge_label"]

    @patch("anthropic.Anthropic")
    def test_strips_markdown_code_fence_before_parsing(self, mock_anthropic_cls):
        mock_client = MagicMock()
        mock_client.messages.create.return_value = _mock_response(
            '```json\n{"match": true, "confidence_percent": 55, "reason": "ok"}\n```'
        )
        mock_anthropic_cls.return_value = mock_client

        agent = ORSPackCheckAgent()
        result = agent.review(ReviewContext(images={"photo": b"fake-jpeg-bytes"}))

        assert result.passed

    @patch("anthropic.Anthropic")
    def test_malformed_json_returns_error(self, mock_anthropic_cls):
        mock_client = MagicMock()
        mock_client.messages.create.return_value = _mock_response("not json at all")
        mock_anthropic_cls.return_value = mock_client

        agent = ORSPackCheckAgent()
        result = agent.review(ReviewContext(images={"photo": b"fake-jpeg-bytes"}))

        assert result.status.value == "error"

    @patch("anthropic.Anthropic")
    def test_sends_image_and_rules_in_message_content(self, mock_anthropic_cls):
        mock_client = MagicMock()
        mock_client.messages.create.return_value = _mock_response(
            '{"match": true, "confidence_percent": 90, "reason": "ok"}'
        )
        mock_anthropic_cls.return_value = mock_client

        agent = ORSPackCheckAgent()
        agent.review(ReviewContext(images={"photo": b"fake-jpeg-bytes"}))

        _, kwargs = mock_client.messages.create.call_args
        content = kwargs["messages"][0]["content"]
        assert content[0]["type"] == "image"
        assert content[0]["source"]["media_type"] == "image/jpeg"
        assert content[1]["type"] == "text"
        assert "ORASURE" in content[1]["text"]
