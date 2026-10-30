"""Thin Microsoft Graph HTTP client: auth injection, paging, ETag/If-Match."""
from __future__ import annotations

from typing import Any, Iterable

import httpx

from .auth import Authenticator
from .config import GRAPH_BASE


class GraphError(RuntimeError):
    def __init__(self, status: int, message: str, body: Any = None):
        super().__init__(f"Graph {status}: {message}")
        self.status = status
        self.body = body


class GraphClient:
    def __init__(self, auth: Authenticator, base: str = GRAPH_BASE, timeout: float = 30.0):
        self._auth = auth
        self._base = base.rstrip("/")
        self._http = httpx.Client(timeout=timeout)

    def _headers(self, etag: str | None = None, extra: dict | None = None) -> dict:
        h = {
            "Authorization": f"Bearer {self._auth.get_token()}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if etag:
            h["If-Match"] = etag
        if extra:
            h.update(extra)
        return h

    def _url(self, path: str) -> str:
        if path.startswith("http"):
            return path
        return f"{self._base}/{path.lstrip('/')}"

    @staticmethod
    def _raise(resp: httpx.Response) -> None:
        if resp.status_code >= 400:
            try:
                body = resp.json()
                msg = body.get("error", {}).get("message", resp.text)
            except Exception:
                body, msg = resp.text, resp.text
            raise GraphError(resp.status_code, msg, body)

    def get(self, path: str, params: dict | None = None) -> dict:
        resp = self._http.get(self._url(path), headers=self._headers(), params=params)
        self._raise(resp)
        return resp.json() if resp.content else {}

    def get_text(self, path: str, params: dict | None = None) -> str:
        """GET a non-JSON body (e.g. OneNote page HTML)."""
        resp = self._http.get(self._url(path), headers=self._headers(extra={"Accept": "text/html"}),
                              params=params)
        self._raise(resp)
        return resp.text

    def get_all(self, path: str, params: dict | None = None, max_items: int | None = None) -> list[dict]:
        """Follow @odata.nextLink and return the concatenated 'value' arrays."""
        items: list[dict] = []
        url: str | None = self._url(path)
        first = True
        while url:
            resp = self._http.get(url, headers=self._headers(), params=params if first else None)
            self._raise(resp)
            data = resp.json()
            items.extend(data.get("value", []))
            if max_items is not None and len(items) >= max_items:
                return items[:max_items]
            url = data.get("@odata.nextLink")
            first = False
        return items

    def post(self, path: str, json: dict) -> dict:
        resp = self._http.post(self._url(path), headers=self._headers(), json=json)
        self._raise(resp)
        return resp.json() if resp.content else {}

    def patch(self, path: str, json: dict, etag: str | None = None, extra: dict | None = None) -> dict:
        resp = self._http.patch(self._url(path), headers=self._headers(etag=etag, extra=extra), json=json)
        self._raise(resp)
        return resp.json() if resp.content else {}

    def delete(self, path: str, etag: str | None = None) -> None:
        resp = self._http.delete(self._url(path), headers=self._headers(etag=etag))
        self._raise(resp)

    # --- Planner helpers: ETag is required on PATCH/DELETE, 412 => refetch+retry ---
    def get_etag(self, path: str) -> tuple[dict, str]:
        data = self.get(path)
        etag = data.get("@odata.etag", "")
        return data, etag

    def patch_with_etag(self, path: str, json: dict) -> dict:
        """PATCH a Planner resource, fetching the current ETag and retrying once on 412.
        Planner PATCH returns 204 unless asked for the updated object via Prefer."""
        prefer = {"Prefer": "return=representation"}
        _, etag = self.get_etag(path)
        try:
            return self.patch(path, json=json, etag=etag, extra=prefer)
        except GraphError as e:
            if e.status in (409, 412):
                _, etag = self.get_etag(path)
                return self.patch(path, json=json, etag=etag, extra=prefer)
            raise

    def delete_with_etag(self, path: str) -> None:
        _, etag = self.get_etag(path)
        try:
            self.delete(path, etag=etag)
        except GraphError as e:
            if e.status in (409, 412):
                _, etag = self.get_etag(path)
                self.delete(path, etag=etag)
            else:
                raise
