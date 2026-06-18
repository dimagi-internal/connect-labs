"""Pagination that emits the production Connect export envelope.

Real Connect returns ``{"results": [...], "next": "<url|null>", "count": N}``.
We match that shape exactly (dropping DRF's default ``previous``) so an external
consumer can page synthetic data with the same loader it uses for real Connect.
"""
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response


class ExportPageNumberPagination(PageNumberPagination):
    page_size = 2500
    page_size_query_param = "page_size"

    def get_paginated_response(self, data):
        return Response(
            {
                "results": data,
                "next": self.get_next_link(),
                "count": self.page.paginator.count,
            }
        )
