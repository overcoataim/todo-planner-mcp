"""Opt-in guardrail: confirm before changing work that involves people who haven't signed up.

"Signed up" = member of the Entra group named by TPM_OPTIN_GROUP (display name or object id).
If no group is configured, every other person counts as not signed up, so any change that
touches someone else needs confirmation.

Nothing is blocked. When a change involves non-members, the user must confirm it:
- clients that support MCP elicitation (e.g. Claude Code) get a direct yes/no prompt;
- other clients get a 'needs_confirmation' result with a one-time code bound to that exact
  action, which only works if the tool is called again with it (after asking the user).

This is a guardrail in our code, not Microsoft-enforced security.
"""
from __future__ import annotations

import hashlib
import re
import secrets
import time
from typing import Any, Iterable

from mcp import types
from pydantic import BaseModel, Field

from .graph import GraphClient, GraphError

_GUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
_MEMBER_TTL_S = 300
_CODE_TTL_S = 600


class _Confirm(BaseModel):
    proceed: bool = Field(description="Go ahead with this change?")


class OptInGuard:
    def __init__(self, gc: GraphClient, group: str = ""):
        self._gc = gc
        self._group = group.strip()
        self._members: tuple[float, set[str]] | None = None
        self._pending: dict[str, tuple[str, float]] = {}  # code -> (fingerprint, expiry)
        self._me: str | None = None

    # ---------- who is signed up ----------
    def me(self) -> str:
        if self._me is None:
            self._me = self._gc.get("me", params={"$select": "id"})["id"]
        return self._me

    def members(self) -> set[str]:
        """Ids of signed-up users (cached briefly). Empty set if no group or lookup fails."""
        if not self._group:
            return set()
        now = time.monotonic()
        if self._members and now - self._members[0] < _MEMBER_TTL_S:
            return self._members[1]
        try:
            gid = self._group if _GUID.match(self._group) else self._group_id_by_name()
            ids = {m["id"] for m in self._gc.get_all(f"groups/{gid}/members", params={"$select": "id"})}
        except (GraphError, LookupError):
            ids = set()  # fail safe: nobody counts as signed up
        self._members = (now, ids)
        return ids

    def _group_id_by_name(self) -> str:
        name = self._group.replace("'", "''")
        found = self._gc.get_all("groups", params={"$filter": f"displayName eq '{name}'", "$select": "id"})
        if not found:
            raise LookupError(f"opt-in group {self._group!r} not found")
        return found[0]["id"]

    def _name(self, uid: str) -> str:
        try:
            u = self._gc.get(f"users/{uid}", params={"$select": "displayName,mail"})
            return u.get("displayName") or u.get("mail") or uid
        except GraphError:
            return uid

    def outsiders(self, user_ids: Iterable[str]) -> list[str]:
        """People involved (other than me) who haven't signed up."""
        me, members = self.me(), self.members()
        return sorted({u for u in user_ids if u and u != me and u not in members})

    # ---------- the check ----------
    async def check(self, ctx: Any, action: str, key: str, user_ids: Iterable[str],
                    confirm_code: str | None = None) -> dict | None:
        """Return None to proceed, or a dict for the tool to return instead of acting.

        action: human description ("Assigning 'Draft launch email'").
        key: exact identity of the change (tool + arguments) that a confirm code is bound to.
        """
        outside = self.outsiders(user_ids)
        if not outside:
            return None
        names = ", ".join(self._name(u) for u in outside)
        verb = "hasn't" if len(outside) == 1 else "haven't"
        question = (f"{action} involves {names}, who {verb} signed up to use the Claude task "
                    f"connector. Go ahead anyway?")

        if _can_elicit(ctx):
            result = await ctx.elicit(message=question, schema=_Confirm)
            if result.action == "accept" and result.data and result.data.proceed:
                return None
            return {"status": "cancelled", "message": f"Not done: the user declined. ({question})"}

        fingerprint = _fingerprint(key, outside)
        if confirm_code:
            stored = self._pending.pop(confirm_code, None)
            if stored and stored[0] == fingerprint and stored[1] > time.monotonic():
                return None
            return {"status": "needs_confirmation", "message": "That confirmation code is invalid, "
                    "expired or for a different change. Ask the user again.",
                    **self._new_code(fingerprint, question)}
        return {"status": "needs_confirmation", "message": question,
                **self._new_code(fingerprint, question)}

    def _new_code(self, fingerprint: str, question: str) -> dict:
        now = time.monotonic()
        self._pending = {c: v for c, v in self._pending.items() if v[1] > now}
        code = secrets.token_hex(4)
        self._pending[code] = (fingerprint, now + _CODE_TTL_S)
        return {"confirm_code": code,
                "instructions": "Ask the user this exact question and wait for their answer: "
                                f"\"{question}\" Only if they clearly say yes, call this tool again "
                                "with the same arguments plus confirm_code. Never confirm on your own."}


def _fingerprint(key: str, outside: list[str]) -> str:
    return hashlib.sha256(f"{key}|{','.join(outside)}".encode()).hexdigest()


def _can_elicit(ctx: Any) -> bool:
    try:
        return ctx.session.check_client_capability(
            types.ClientCapabilities(elicitation=types.ElicitationCapability()))
    except Exception:  # no context (direct call) or no session
        return False
