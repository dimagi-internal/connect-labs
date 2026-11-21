"""A fake Connect export API for tests: an ExportAPIClient whose transport answers from dicts.

No network. Paths map to lists of rows (served as one v2 page, `next: null`) or, for
non-list endpoints, to a dict. `calls` records every request so tests can assert cost.
"""

from __future__ import annotations

import json

import httpx

from connect_labs.labs.integrations.connect.export_client import ExportAPIClient

BASE = "https://connect.example"


class FakeConnect:
    def __init__(self, *, lists=None, objects=None, accessible=None, missing=()):
        self.lists = dict(lists or {})  # "/export/opportunity/1/user_data/" -> [rows]
        self.objects = dict(objects or {})  # "/export/opp_org_program_list/" -> {...}
        self.accessible = set(accessible or ())  # ids opportunity_access says yes to
        self.missing = set(missing)  # paths that 404
        self.calls: list[tuple[str, str, dict]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        params = dict(request.url.params)
        self.calls.append((request.method, path, params))
        if path in self.missing:
            return httpx.Response(404, json={"detail": "Not found."})
        if request.method == "POST" and path == "/export/opportunity_access/":
            ids = json.loads(request.content)["opportunity_ids"]
            return httpx.Response(200, json={"opportunities": {str(i): i in self.accessible for i in ids}})
        if path in self.lists:
            rows = self.lists[path]
            if "username" in params:
                rows = [r for r in rows if r.get("username") == params["username"]]
            return httpx.Response(200, json={"next": None, "results": rows})
        if path in self.objects:
            return httpx.Response(200, json=self.objects[path])
        return httpx.Response(404, json={"detail": "Not found."})

    def client(self) -> ExportAPIClient:
        client = ExportAPIClient(BASE, "token")
        client.http_client.close()
        client.http_client = httpx.Client(transport=httpx.MockTransport(self.handler), follow_redirects=True)
        return client

    def count(self, path_part: str) -> int:
        return sum(1 for _, p, _ in self.calls if path_part in p)


def opp_path(opp_id: int, kind: str) -> str:
    return f"/export/opportunity/{opp_id}/{kind}/"
