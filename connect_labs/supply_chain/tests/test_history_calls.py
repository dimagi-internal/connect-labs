"""Every write runs as a recorded, attributed and idempotent OperationCall.

THIS REPOSITORY IS PUBLIC. Every id, name and figure below is invented.

Exercises `call_operation` end to end with a real write operation
(`tender_create`), so the OperationCall row, the Revisions its writes produce,
and the replay of a duplicate `source.ref` are all real ORM rows. See
docs/superpowers/specs/2026-09-26-supply-sophie-history-design.md §3.3-§3.4.
"""

import json
import threading

import pytest
from django.core.serializers.json import DjangoJSONEncoder
from django.db import IntegrityError, connection
from django.test import RequestFactory

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.history.models import OperationCall, Revision
from connect_labs.supply_chain.models import Tender
from connect_labs.supply_chain.operations import SOURCE_SCHEMA, all_operations, call_operation

PROGRAM = 10998


def _payload(label="Tender R2", **extra):
    return {"data": {"label": label, "delivery_point": {"name": "Central store"}}, **extra}


def _source(ref="<msg-1@example.test>", excerpt="Please find our quote"):
    return {"ref": ref, "excerpt": excerpt}


@pytest.fixture
def access():
    return SupplyDataAccess(program_id=PROGRAM, caller=SYSTEM)


@pytest.fixture
def access_as_ace(django_user_model):
    user = django_user_model.objects.create_user(username="ace", email="ACE@dimagi-ai.com", password="x")
    return SupplyDataAccess(program_id=PROGRAM, user=user, caller=SYSTEM)


@pytest.mark.django_db
class TestRecordingAWrite:
    def test_write_records_an_operation_call_with_channel(self, access):
        result = call_operation("tender_create", access, _payload(), channel="mcp")

        call = OperationCall.objects.get(operation="tender_create")
        assert call.channel == "mcp" and call.program_id == access.program_id
        assert Revision.objects.filter(call=call, action="create").exists()
        assert call.result == json.loads(json.dumps(result, cls=DjangoJSONEncoder))
        assert "replayed" not in result

    def test_source_is_stored_on_the_call_and_never_reaches_the_handler(self, access):
        call_operation("tender_create", access, _payload(source=_source()), channel="mcp")

        call = OperationCall.objects.get()
        assert (call.source_ref, call.source_excerpt) == ("<msg-1@example.test>", "Please find our quote")

    def test_a_caller_with_no_user_and_no_request_is_a_command(self, access):
        call_operation("tender_create", access, _payload())
        assert OperationCall.objects.get().channel == "command"

    def test_a_caller_with_a_request_is_the_web(self, django_user_model):
        user = django_user_model.objects.create_user(username="sophie", password="x")
        request = RequestFactory().post("/")
        request.user = user
        web = SupplyDataAccess(program_id=PROGRAM, request=request, user=user, caller=SYSTEM)

        call_operation("tender_create", web, _payload())

        call = OperationCall.objects.get()
        assert call.channel == "web" and call.actor == user and call.actor_is_agent is False

    def test_agent_email_marks_call(self, access_as_ace):
        call_operation("tender_create", access_as_ace, _payload(), channel="mcp")

        call = OperationCall.objects.get()
        assert call.actor_is_agent is True and call.actor == access_as_ace.user

    def test_reads_record_nothing(self, access):
        call_operation("tender_list", access, {})
        assert not OperationCall.objects.exists()

    def test_a_failed_write_leaves_no_call_behind(self, access, monkeypatch):
        def boom(data):
            raise ValueError("refused")

        monkeypatch.setattr(access, "create_tender", boom)
        with pytest.raises(ValueError):
            call_operation("tender_create", access, _payload(source=_source()))
        assert not OperationCall.objects.exists()


@pytest.mark.django_db
class TestIdempotency:
    def test_same_source_ref_replays_without_writing(self, access):
        payload = _payload(source=_source())

        first = call_operation("tender_create", access, payload, channel="mcp")
        second = call_operation("tender_create", access, payload, channel="mcp")

        assert second["replayed"] is True and second["id"] == first["id"]
        assert "replayed" not in first
        assert Tender.objects.count() == 1 and OperationCall.objects.count() == 1

    def test_same_ref_different_operation_writes(self, access):
        made = call_operation("tender_create", access, _payload(source=_source()))
        updated = call_operation(
            "tender_update",
            access,
            {"tender_id": made["id"], "data": {"label": "Round 2"}, "source": _source()},
        )

        assert "replayed" not in updated and updated["label"] == "Round 2"
        assert sorted(OperationCall.objects.values_list("operation", flat=True)) == ["tender_create", "tender_update"]

    def test_no_source_never_deduplicates(self, access):
        call_operation("tender_create", access, _payload())
        call_operation("tender_create", access, _payload())

        assert Tender.objects.count() == 2 and OperationCall.objects.count() == 2

    def test_a_handlers_own_integrity_error_still_raises(self, access, monkeypatch):
        """The fallback lookup must not swallow a constraint the HANDLER hit."""

        def clash(data):
            raise IntegrityError("duplicate key in the handler's own table")

        monkeypatch.setattr(access, "create_tender", clash)
        with pytest.raises(IntegrityError, match="handler's own table"):
            call_operation("tender_create", access, _payload(source=_source()))
        assert not OperationCall.objects.exists()


def test_source_is_in_every_write_schema_and_no_read_schema():
    for op in all_operations().values():
        assert ("source" in op.input_schema["properties"]) == op.is_write, op.name
        if op.is_write:
            assert op.input_schema["properties"]["source"] == SOURCE_SCHEMA


@pytest.mark.django_db(transaction=True)
def test_concurrent_duplicate_returns_winner():
    payload = _payload(source=_source("<race@example.test>"))
    barrier = threading.Barrier(2)
    results, errors = [], []

    def run():
        try:
            barrier.wait()
            results.append(
                call_operation("tender_create", SupplyDataAccess(program_id=PROGRAM, caller=SYSTEM), dict(payload))
            )
        except Exception as exc:  # surfaced below; a thread's exception is otherwise lost
            errors.append(exc)
        finally:
            connection.close()

    threads = [threading.Thread(target=run) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors, errors
    assert Tender.objects.count() == 1 and OperationCall.objects.count() == 1
    assert results[0]["id"] == results[1]["id"]
    assert sum(1 for r in results if r.get("replayed")) == 1


class TestEachSurfaceNamesItsChannel:
    """The adapters, not the operations, know which surface a write came through."""

    def test_the_mcp_tools_say_mcp(self):
        from unittest.mock import MagicMock, patch

        from connect_labs.supply_chain.mcp_tools import _make_handler
        from connect_labs.supply_chain.operations import get_operation

        handler = _make_handler(get_operation("tender_create"))
        with (
            patch("connect_labs.supply_chain.mcp_tools.require_connect_token", return_value="tok"),
            patch("connect_labs.supply_chain.mcp_tools.SupplyDataAccess", return_value=MagicMock()),
            patch("connect_labs.supply_chain.mcp_tools.call_operation") as called,
        ):
            handler(user=MagicMock(), program_id=PROGRAM, **_payload())
        assert called.call_args.kwargs == {"channel": "mcp"}

    @pytest.mark.django_db
    def test_the_http_api_says_api(self, client, django_user_model):
        from unittest.mock import patch

        from django.urls import reverse

        client.force_login(django_user_model.objects.create_user(username="sophie", password="x"))
        with patch("connect_labs.supply_chain.api_views.call_operation", return_value={}) as called:
            response = client.post(
                reverse("supply_chain:api_operation", args=["tender_create"]),
                data=json.dumps(_payload()),
                content_type="application/json",
            )
        assert response.status_code == 200
        assert called.call_args.kwargs == {"channel": "api"}
