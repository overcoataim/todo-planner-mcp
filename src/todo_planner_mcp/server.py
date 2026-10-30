"""FastMCP server exposing Microsoft To Do, Planner and OneNote (read-only) over Microsoft Graph.

Run as an MCP stdio server (default), or:
  python -m todo_planner_mcp.server --check    # offline: validate .env and list tools
  python -m todo_planner_mcp.server --login    # interactive sign-in (device code or browser)
  python -m todo_planner_mcp.server --logout   # clear the token cache
"""
from __future__ import annotations

import argparse
import sys

from mcp.server.fastmcp import FastMCP

from . import todo_tools, planner_tools, onenote_tools
from .auth import Authenticator
from .config import load_settings
from .graph import GraphClient


def build_server() -> tuple[FastMCP, GraphClient, Authenticator]:
    settings = load_settings()
    auth = Authenticator(settings)
    gc = GraphClient(auth)
    mcp = FastMCP("todo-planner")

    @mcp.tool()
    def whoami() -> dict:
        """Return the signed-in user (verifies Graph auth is working)."""
        me = gc.get("me")
        return {"id": me.get("id"), "displayName": me.get("displayName"),
                "userPrincipalName": me.get("userPrincipalName"),
                "mail": me.get("mail")}

    todo_tools.register(mcp, gc)
    planner_tools.register(mcp, gc, settings.optin_group)
    onenote_tools.register(mcp, gc)
    return mcp, gc, auth


def main() -> None:
    parser = argparse.ArgumentParser(prog="todo-planner-mcp")
    parser.add_argument("--check", action="store_true",
                        help="Validate config and list tools without touching the network.")
    parser.add_argument("--login", action="store_true",
                        help="Perform interactive sign-in and exit.")
    parser.add_argument("--logout", action="store_true", help="Clear the token cache and exit.")
    args = parser.parse_args()

    if args.logout:
        settings = load_settings()
        Authenticator(settings).sign_out()
        print("Signed out; token cache cleared.", file=sys.stderr)
        return

    mcp, gc, auth = build_server()

    if args.check:
        _check(mcp, auth)
        return

    if args.login:
        auth.get_token()  # triggers device flow if needed
        me = gc.get("me")
        print(f"Signed in as {me.get('displayName')} <{me.get('userPrincipalName')}>", file=sys.stderr)
        return

    mcp.run()  # stdio transport


def _check(mcp: FastMCP, auth: Authenticator) -> None:
    import asyncio
    s = auth._s
    tools = asyncio.run(mcp.list_tools())
    secret = f"set ({len(s.client_secret)} chars)" if s.client_secret else "not set"
    cached = s.token_cache.exists()
    for line in (
        f"client_id:    {s.client_id}",
        f"authority:    {s.authority_url}",
        f"client mode:  {'confidential (auth-code, browser)' if s.is_confidential else 'public (device code)'}",
        f"secret:       {secret}",
        f"redirect_uri: {s.redirect_uri}" + ("" if s.is_confidential else "  (unused in public mode)"),
        f"scopes:       {' '.join(s.scopes)}",
        f"token cache:  {s.token_cache} ({'present' if cached else 'none yet - run --login'})",
        f"opt-in group: {s.optin_group or 'not set - every other person needs confirmation'}",
        f"tools:        {len(tools)} registered",
    ):
        print(line, file=sys.stderr)


if __name__ == "__main__":
    main()
