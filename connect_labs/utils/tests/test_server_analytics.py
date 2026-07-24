from unittest.mock import patch

from connect_labs.utils import server_analytics


def test_send_event_noop_when_unconfigured(settings):
    settings.UMAMI_HOST_URL = ""
    settings.UMAMI_WEBSITE_ID = ""
    with patch.object(server_analytics.send_event_task, "delay") as delay:
        server_analytics.send_event("login", {"x": 1})
    delay.assert_not_called()


def test_send_event_queues_when_configured(settings):
    settings.UMAMI_HOST_URL = "https://labs.example.com/umami"
    settings.UMAMI_WEBSITE_ID = "site-123"
    with patch.object(server_analytics.send_event_task, "delay") as delay:
        server_analytics.send_event("login", {"is_dimagi": True})
    delay.assert_called_once_with("login", {"is_dimagi": True}, "/server")


def test_send_event_task_posts_payload(settings):
    settings.UMAMI_HOST_URL = "https://labs.example.com/umami"
    settings.UMAMI_WEBSITE_ID = "site-123"
    with patch("connect_labs.utils.server_analytics.httpx.post") as post:
        post.return_value.raise_for_status.return_value = None
        server_analytics.send_event_task("workflow_run_created", {"opp": 42})
    args, kwargs = post.call_args
    assert args[0] == "https://labs.example.com/umami/api/send"
    payload = kwargs["json"]["payload"]
    assert payload["website"] == "site-123"
    assert payload["name"] == "workflow_run_created"
    assert payload["hostname"] == "labs.example.com"
    assert kwargs["headers"]["User-Agent"] == server_analytics.SERVER_USER_AGENT
