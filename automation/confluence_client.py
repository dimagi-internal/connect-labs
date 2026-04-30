"""Confluence REST API v2 wrapper used by docs automation scripts."""

import os
import re

import requests

CONFLUENCE_BASE = "https://dimagi.atlassian.net/wiki"
CONNECT_SPACE_ID = "2683404306"  # 'connect' space


class ConfluenceClient:
    def __init__(self, user_email: str | None = None, api_token: str | None = None):
        email = user_email or os.environ.get("CONFLUENCE_EMAIL", "")
        token = api_token or os.environ.get("CONFLUENCE_API_TOKEN", "")
        if not email or not token:
            raise ValueError("CONFLUENCE_EMAIL and CONFLUENCE_API_TOKEN must be set")
        self.auth = (email, token)
        self.headers = {"Accept": "application/json", "Content-Type": "application/json"}

    def _get(self, path: str, params: dict | None = None) -> dict:
        resp = requests.get(
            f"{CONFLUENCE_BASE}{path}",
            auth=self.auth,
            headers=self.headers,
            params=params,
            timeout=60,
        )
        try:
            resp.raise_for_status()
        except requests.HTTPError as e:
            status = e.response.status_code if e.response is not None else "unknown"
            raise RuntimeError(f"Confluence GET {path} failed: HTTP {status}") from e
        return resp.json()

    def _put(self, path: str, body: dict) -> dict:
        resp = requests.put(
            f"{CONFLUENCE_BASE}{path}",
            auth=self.auth,
            headers=self.headers,
            json=body,
            timeout=60,
        )
        try:
            resp.raise_for_status()
        except requests.HTTPError as e:
            status = e.response.status_code if e.response is not None else "unknown"
            raise RuntimeError(f"Confluence PUT {path} failed: HTTP {status}") from e
        return resp.json()

    def _post(self, path: str, body: dict) -> dict:
        resp = requests.post(
            f"{CONFLUENCE_BASE}{path}",
            auth=self.auth,
            headers=self.headers,
            json=body,
            timeout=60,
        )
        try:
            resp.raise_for_status()
        except requests.HTTPError as e:
            status = e.response.status_code if e.response is not None else "unknown"
            raise RuntimeError(f"Confluence POST {path} failed: HTTP {status}") from e
        return resp.json()

    def get_page(self, page_id: str) -> dict:
        """Fetch a page; returns {id, title, version_number, body_storage}."""
        data = self._get(
            f"/api/v2/pages/{page_id}",
            params={"body-format": "storage"},
        )
        return {
            "id": data["id"],
            "title": data["title"],
            "version_number": data["version"]["number"],
            "body_storage": data.get("body", {}).get("storage", {}).get("value", ""),
        }

    def update_page(
        self,
        page_id: str,
        title: str,
        body_storage: str,
        version_number: int,
    ) -> dict:
        return self._put(
            f"/api/v2/pages/{page_id}",
            {
                "id": page_id,
                "status": "current",
                "title": title,
                "body": {"representation": "storage", "value": body_storage},
                "version": {"number": version_number + 1},
            },
        )

    def create_child_page(
        self,
        parent_id: str,
        title: str,
        body_storage: str,
    ) -> dict:
        return self._post(
            "/api/v2/pages",
            {
                "spaceId": CONNECT_SPACE_ID,
                "parentId": parent_id,
                "status": "current",
                "title": title,
                "body": {"representation": "storage", "value": body_storage},
            },
        )

    def prepend_table_row(self, page_id: str, row_html: str) -> None:
        """Insert row_html as the first data row (after the header) in the first table."""
        page = self.get_page(page_id)
        body = page["body_storage"]

        # Find the first </tr> (end of header row) and insert after it
        match = re.search(r"</tr>", body, re.IGNORECASE)
        if match:
            insert_pos = match.end()
            new_body = body[:insert_pos] + "\n" + row_html + body[insert_pos:]
        else:
            # No table yet — wrap in a basic table structure
            new_body = (
                "<table><tbody>"
                "<tr><th>Date</th><th>Changes</th><th>PRs</th></tr>"
                f"\n{row_html}"
                "</tbody></table>"
            )

        self.update_page(
            page_id=page_id,
            title=page["title"],
            body_storage=new_body,
            version_number=page["version_number"],
        )
