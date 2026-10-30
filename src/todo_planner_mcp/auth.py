"""Delegated authentication via MSAL with a persistent token cache.

Two modes, chosen by whether TPM_CLIENT_SECRET is set:

- Public client (no secret): device-code flow. Headless-friendly, works over SSH
  and for personal Microsoft accounts. Needs "Allow public client flows = Yes".
- Confidential client (secret): auth-code flow via a one-shot localhost listener
  at TPM_REDIRECT_URI (register it under the app's *Web* platform). This is the
  same model the hosted multi-user version will use.

Prompts are written to STDERR so they never corrupt the MCP stdio channel
(which uses STDOUT).
"""
from __future__ import annotations

import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import msal

from .config import Settings

_LOGIN_TIMEOUT_S = 300


class TokenError(RuntimeError):
    pass


def _banner(*lines: str) -> None:
    print("\n" + "=" * 60, file=sys.stderr)
    for line in lines:
        print(line, file=sys.stderr)
    print("=" * 60 + "\n", file=sys.stderr, flush=True)


class Authenticator:
    def __init__(self, settings: Settings):
        self._s = settings
        self._lock = threading.Lock()
        self._cache = msal.SerializableTokenCache()
        self._cache_path: Path = settings.token_cache
        if self._cache_path.exists():
            self._cache.deserialize(self._cache_path.read_text(encoding="utf-8"))
        self._app_obj: msal.ClientApplication | None = None

    @property
    def _app(self) -> "msal.ClientApplication":
        # Built lazily: constructing MSAL performs network discovery, so we defer
        # it until the first token call (lets the MCP server start offline).
        if self._app_obj is None:
            if self._s.is_confidential:
                self._app_obj = msal.ConfidentialClientApplication(
                    client_id=self._s.client_id,
                    client_credential=self._s.client_secret,
                    authority=self._s.authority_url,
                    token_cache=self._cache,
                )
            else:
                self._app_obj = msal.PublicClientApplication(
                    client_id=self._s.client_id,
                    authority=self._s.authority_url,
                    token_cache=self._cache,
                )
        return self._app_obj

    def _persist(self) -> None:
        if self._cache.has_state_changed:
            self._cache_path.write_text(self._cache.serialize(), encoding="utf-8")
            try:
                self._cache_path.chmod(0o600)
            except OSError:
                pass  # best-effort on Windows

    def get_token(self) -> str:
        """Return a valid access token, refreshing or prompting as needed."""
        with self._lock:
            result = None
            accounts = self._app.get_accounts()
            if accounts:
                result = self._app.acquire_token_silent(self._s.scopes, account=accounts[0])
            if not result:
                if self._s.is_confidential:
                    result = self._auth_code_flow()
                else:
                    result = self._device_code_flow()
            self._persist()
            if not result or "access_token" not in result:
                err = (result or {}).get("error_description", "unknown error")
                raise TokenError(f"Failed to acquire token: {err}")
            return result["access_token"]

    def _device_code_flow(self) -> dict:
        flow = self._app.initiate_device_flow(scopes=self._s.scopes)
        if "user_code" not in flow:
            raise TokenError(f"Device flow failed to start: {flow.get('error_description', flow)}")
        _banner("Microsoft sign-in required:", flow["message"])
        return self._app.acquire_token_by_device_flow(flow)  # blocks until done/expired

    def _auth_code_flow(self) -> dict:
        redirect = urlparse(self._s.redirect_uri)
        if redirect.hostname not in ("localhost", "127.0.0.1") or not redirect.port:
            raise TokenError(
                f"TPM_REDIRECT_URI must be http://localhost:<port>/... for local sign-in; "
                f"got {self._s.redirect_uri!r}"
            )
        # select_account: always show the account picker, so a browser already signed in
        # as someone else doesn't silently reuse that account.
        flow = self._app.initiate_auth_code_flow(
            scopes=self._s.scopes, redirect_uri=self._s.redirect_uri, prompt="select_account")
        if "auth_uri" not in flow:
            raise TokenError(f"Auth-code flow failed to start: {flow.get('error_description', flow)}")

        captured: dict[str, str] = {}

        class _Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                url = urlparse(self.path)
                if url.path != (redirect.path or "/"):
                    self.send_response(404)
                    self.end_headers()
                    return
                params = {k: v[0] for k, v in parse_qs(url.query).items()}
                if params.get("state") != flow.get("state"):
                    # A stale tab from an earlier sign-in attempt; ignore it and keep waiting.
                    self.send_response(409)
                    self.send_header("Content-Type", "text/plain; charset=utf-8")
                    self.end_headers()
                    self.wfile.write(b"This sign-in link is out of date. Use the newest link.")
                    return
                captured.update(params)
                ok = "code" in captured
                self.send_response(200 if ok else 400)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write(
                    b"Signed in. You can close this tab." if ok
                    else f"Sign-in failed: {captured.get('error_description', captured)}".encode())

            def log_message(self, *args):  # keep STDOUT/STDERR clean
                pass

        try:
            httpd = HTTPServer(("127.0.0.1", redirect.port), _Handler)
        except OSError as e:
            raise TokenError(f"Cannot listen on {self._s.redirect_uri}: {e}") from e
        httpd.timeout = 1  # handle_request() poll interval

        _banner("Microsoft sign-in required. Open this URL in a browser:",
                "", flow["auth_uri"], "",
                f"Waiting up to {_LOGIN_TIMEOUT_S // 60} min for the redirect to {self._s.redirect_uri} ...")
        deadline = time.monotonic() + _LOGIN_TIMEOUT_S
        try:
            # Loop so stray requests (e.g. /favicon.ico) don't end the wait.
            while not captured and time.monotonic() < deadline:
                httpd.handle_request()
        finally:
            httpd.server_close()

        if not captured:
            raise TokenError("Timed out waiting for sign-in redirect.")
        if "code" not in captured:
            raise TokenError(f"Sign-in failed: {captured.get('error_description', captured)}")
        return self._app.acquire_token_by_auth_code_flow(flow, captured)

    def sign_out(self) -> None:
        with self._lock:
            for acct in self._app.get_accounts():
                self._app.remove_account(acct)
            self._persist()
