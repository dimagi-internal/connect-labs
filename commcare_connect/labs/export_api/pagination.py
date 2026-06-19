"""Keyset (cursor) pagination mirroring production Connect's export v2 envelope.

Production (`data_export/pagination.py`) keysets on the DB ``id`` and returns
``{"next": <url|null>, "results": [...]}`` — no ``count``. We mirror that exactly
so an external consumer can page synthetic data with the same loader it uses for
real Connect.

Unlike production we page an in-memory list of fixture dict rows, not a queryset.
The cursor for a row is its ``id`` when present (so ``user_visits`` matches a
resumable loader's ``last_id`` semantics) else its positional index (stable for
static fixtures; full-refresh via ``next`` still works for id-less endpoints like
``completed_works``).
"""
from rest_framework import serializers
from rest_framework.pagination import BasePagination
from rest_framework.response import Response

FORWARD = "forward"
REVERSE = "reverse"


class _ParamsSerializer(serializers.Serializer):
    # No min on last_id: index-mode cursors are 0-based and last_id=0 is a valid cursor.
    last_id = serializers.IntegerField(required=False)
    page_size = serializers.IntegerField(min_value=1, required=False)
    cursor_order = serializers.ChoiceField(choices=[FORWARD, REVERSE], default=FORWARD, required=False)


class IdKeysetPagination(BasePagination):
    default_page_size = 1000
    max_page_size = 5000
    page_size_query_param = "page_size"
    last_id_query_param = "last_id"
    cursor_order_query_param = "cursor_order"

    def paginate_queryset(self, data, request, view=None):
        """``data`` is the full in-memory list of fixture dict rows."""
        self.request = request

        params = _ParamsSerializer(
            data={
                k: request.query_params[k]
                for k in (self.last_id_query_param, self.page_size_query_param, self.cursor_order_query_param)
                if k in request.query_params
            }
        )
        params.is_valid(raise_exception=True)
        self.cursor_order = params.validated_data["cursor_order"]
        self.last_id = params.validated_data.get("last_id")
        raw_page_size = params.validated_data.get("page_size")
        self.page_size = (
            min(raw_page_size, self.max_page_size) if raw_page_size is not None else self.default_page_size
        )

        is_forward = self.cursor_order == FORWARD
        keyed = [(self._cursor(row, i), row) for i, row in enumerate(data)]
        keyed.sort(key=lambda kv: kv[0], reverse=not is_forward)
        if self.last_id is not None:
            keyed = [(c, row) for c, row in keyed if (c > self.last_id if is_forward else c < self.last_id)]

        window = keyed[: self.page_size + 1]
        self.has_next = len(window) > self.page_size
        page = window[: self.page_size]
        self._last_cursor = page[-1][0] if page else None
        return [row for _, row in page]

    @staticmethod
    def _cursor(row, index):
        # Invariant: rows within a single fixture endpoint are homogeneous —
        # either ALL carry an int ``id`` (id-mode, e.g. user_visits) or NONE do
        # (index-mode, e.g. completed_works).  Mixing id-bearing and id-less rows
        # inside one fixture is unsupported: the two cursor spaces could collide
        # and cause a row to be skipped during keyset pagination.
        val = row.get("id") if isinstance(row, dict) else None
        return val if isinstance(val, int) else index

    def get_next_link(self):
        if not self.has_next or self._last_cursor is None:
            return None
        query = self.request.query_params.copy()
        query[self.last_id_query_param] = self._last_cursor
        query[self.page_size_query_param] = self.page_size
        query[self.cursor_order_query_param] = self.cursor_order
        return self.request.build_absolute_uri(f"{self.request.path}?{query.urlencode()}")

    def get_paginated_response(self, data):
        return Response({"next": self.get_next_link(), "results": data})
