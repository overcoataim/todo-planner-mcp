"""Live test of the opt-in guardrail (optin.py), as the default signed-in user.

Checks both confirmation paths against a real task in the test plan:
- confirm-code path (clients without elicitation, e.g. Claude Desktop / claude.ai)
- elicitation path, via a real MCP stdio client that answers the prompt (like Claude Code)

    <venv>/bin/python scripts/guard_test.py

Uses TPM_OPTIN_GROUP from .env. With no group, every other person counts as not signed up.
OPTIN_MEMBER / OUTSIDER pick the test users (UPN prefixes); defaults are test1 / test2.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client

from todo_planner_mcp.server import build_server

PLAN_TITLE = "testplan"
OUTSIDER = os.environ.get("OUTSIDER", "test2")          # never in the opt-in group
OPTIN_MEMBER = os.environ.get("OPTIN_MEMBER", "test1")  # in the group, if one is configured
RESULTS: list[tuple[str, bool, str]] = []

mcp, gc, _ = build_server()
T = {t.name: t.fn for t in mcp._tool_manager.list_tools()}
group = os.environ.get("TPM_OPTIN_GROUP", "")


def check(name: str, ok: bool, note: str = "") -> None:
    RESULTS.append((name, ok, note))


def call(tool: str, **kw):
    out = T[tool](**kw)
    return asyncio.run(out) if asyncio.iscoroutine(out) else out


def assignees(tid: str) -> set[str]:
    return set((gc.get(f"planner/tasks/{tid}").get("assignments") or {}).keys())


def user_id(prefix: str) -> str:
    domain = gc.get("me")["userPrincipalName"].split("@")[1]
    return gc.get(f"users/{prefix}@{domain}", params={"$select": "id"})["id"]


def code_path(pid: str, me: str, outsider: str) -> None:
    tid = call("planner_create_task", plan_id=pid, title="GUARD-own task", assignee_ids=[me])["id"]
    check("own-only task: no confirmation", "status" not in call("planner_update_task", task_id=tid, priority=3))

    r = call("planner_assign_task", task_id=tid, assignee_ids=[outsider])
    check("outsider: asks first", r.get("status") == "needs_confirmation", r.get("message", "")[:70])
    check("outsider: nothing changed yet", outsider not in assignees(tid))
    code = r.get("confirm_code")

    r2 = call("planner_assign_task", task_id=tid, assignee_ids=[outsider], confirm_code="deadbeef")
    check("wrong code rejected", r2.get("status") == "needs_confirmation" and outsider not in assignees(tid))
    code = r2.get("confirm_code")  # a rejected attempt issues a fresh code

    # Same people involved, different change: the code must not carry over.
    r3 = call("planner_assign_task", task_id=tid, assignee_ids=[outsider, me], confirm_code=code)
    check("code for a different change rejected",
          r3.get("status") == "needs_confirmation" and outsider not in assignees(tid))

    r4 = call("planner_assign_task", task_id=tid, assignee_ids=[outsider])
    r5 = call("planner_assign_task", task_id=tid, assignee_ids=[outsider], confirm_code=r4["confirm_code"])
    check("right code: change made", "status" not in r5 and outsider in assignees(tid))

    r6 = call("planner_update_task", task_id=tid, priority=5, confirm_code=r4["confirm_code"])
    check("code is one-time only", r6.get("status") == "needs_confirmation")

    d = call("planner_delete_task", task_id=tid)
    d2 = call("planner_delete_task", task_id=tid, confirm_code=d.get("confirm_code"))
    check("delete involving outsider needs confirm", d.get("status") == "needs_confirmation" and d2 == "deleted")


async def elicitation_path(pid: str, me: str, outsider: str) -> None:
    answers: list[bool] = []
    asked: list[str] = []

    async def on_elicit(_ctx, params: types.ElicitRequestParams):
        asked.append(params.message)
        return types.ElicitResult(action="accept", content={"proceed": answers.pop(0)})

    server = StdioServerParameters(command=sys.executable, args=["-m", "todo_planner_mcp.server"],
                                   env=dict(os.environ))
    async with stdio_client(server) as (r, w), ClientSession(r, w, elicitation_callback=on_elicit) as s:
        await s.initialize()

        async def tool(name, **args):
            res = await s.call_tool(name, args)
            if res.isError:
                raise RuntimeError(res.content[0].text)
            try:
                return json.loads(res.content[0].text)
            except (json.JSONDecodeError, IndexError):
                return {}

        made = await tool("planner_create_task", plan_id=pid, title="GUARD-elicit task", assignee_ids=[me])
        tid = made["id"]

        answers.append(False)
        out = await tool("planner_assign_task", task_id=tid, assignee_ids=[outsider])
        check("elicit: user is asked directly", len(asked) == 1, asked[-1][:70] if asked else "")
        check("elicit: 'no' -> nothing changed", out.get("status") == "cancelled" and outsider not in assignees(tid))

        answers.append(True)
        out = await tool("planner_assign_task", task_id=tid, assignee_ids=[outsider])
        check("elicit: 'yes' -> change made", outsider in assignees(tid))

        answers.append(True)
        await tool("planner_delete_task", task_id=tid)
        check("elicit: no confirm code needed", True, f"{len(asked)} prompts shown")


def group_path(pid: str, me: str, member: str, outsider: str) -> None:
    tid = call("planner_create_task", plan_id=pid, title="GUARD-group task", assignee_ids=[me])["id"]
    r = call("planner_assign_task", task_id=tid, assignee_ids=[member])
    check("signed-up colleague: no confirmation", "status" not in r and member in assignees(tid))
    r = call("planner_assign_task", task_id=tid, assignee_ids=[outsider])
    check("not-signed-up colleague: asks first", r.get("status") == "needs_confirmation")
    d = call("planner_delete_task", task_id=tid)
    if isinstance(d, dict):
        call("planner_delete_task", task_id=tid, confirm_code=d["confirm_code"])


def cleanup(pid: str) -> None:
    """Remove GUARD- tasks left by an earlier failed run (deletes directly, bypassing the tools)."""
    for t in call("planner_list_tasks", plan_id=pid):
        if (t.get("title") or "").startswith("GUARD-"):
            gc.delete_with_etag(f"planner/tasks/{t['id']}")


def main() -> None:
    pid = next(p["id"] for p in call("planner_list_plans") if p["title"] == PLAN_TITLE)
    cleanup(pid)
    me, outsider = gc.get("me")["id"], user_id(OUTSIDER)
    code_path(pid, me, outsider)
    asyncio.run(elicitation_path(pid, me, outsider))
    if group:
        group_path(pid, me, user_id(OPTIN_MEMBER), outsider)
    else:
        check("opt-in group checks", True, "SKIPPED - TPM_OPTIN_GROUP not set")

    width = max(len(n) for n, _, _ in RESULTS)
    for name, ok, note in RESULTS:
        print(f"{name.ljust(width)}  {'PASS' if ok else 'FAIL'}  {note}")
    print(f"\n{sum(ok for _, ok, _ in RESULTS)}/{len(RESULTS)} passed")


if __name__ == "__main__":
    main()
