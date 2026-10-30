"""Live smoke test: call every tool against the real tenant and print a check matrix.

Creates its own data (prefixed SMOKE-) and deletes it again. Only point this at a test
tenant/account -- the tools write real data.

    <venv>/bin/python scripts/smoke_live.py
"""
from __future__ import annotations

import asyncio
import time
import traceback

from todo_planner_mcp.server import build_server

mcp, gc, auth = build_server()
TOOLS = {t.name: t.fn for t in mcp._tool_manager.list_tools()}
RESULTS: list[tuple[str, str, str, str]] = []  # (tool, kind, status, note)
TAG = f"SMOKE-{time.strftime('%H%M%S')}"


def call(tool: str, **kwargs):
    """Call a tool directly (sync or async). Confirms opt-in prompts as if the user said yes;
    returns (result, confirmed_flag)."""
    out = TOOLS[tool](**kwargs)
    if asyncio.iscoroutine(out):
        out = asyncio.run(out)
    if isinstance(out, dict) and out.get("status") == "needs_confirmation":
        out2 = TOOLS[tool](**kwargs, confirm_code=out["confirm_code"])
        return (asyncio.run(out2) if asyncio.iscoroutine(out2) else out2), True
    return out, False


def run(tool: str, kind: str, **kwargs):
    """Call a tool, record PASS/FAIL, return its result (or None on failure)."""
    try:
        out, confirmed = call(tool, **kwargs)
        if isinstance(out, dict) and out.get("status") in ("needs_confirmation", "cancelled"):
            raise RuntimeError(f"guard: {out['message'][:70]}")
        note = f"{len(out)} items" if isinstance(out, list) else "ok"
        if confirmed:
            note += " (opt-in confirm needed + given)"
        RESULTS.append((tool, kind, "PASS", note))
        return out
    except Exception as e:  # record and keep going
        msg = str(e).splitlines()[0][:90]
        RESULTS.append((tool, kind, "FAIL", msg))
        return None


def skip(tool: str, kind: str, why: str) -> None:
    RESULTS.append((tool, kind, "SKIP", why))


def todo() -> None:
    run("todo_list_lists", "R")
    lst = run("todo_create_list", "W", display_name=TAG)
    if not lst:
        for t in ("todo_find_list", "todo_update_list", "todo_create_task", "todo_list_tasks",
                  "todo_get_task", "todo_update_task", "todo_add_checklist_item",
                  "todo_list_checklist_items", "todo_check_checklist_item",
                  "todo_delete_checklist_item", "todo_complete_task", "todo_delete_task",
                  "todo_delete_list"):
            skip(t, "-", "no test list")
        return
    lid = lst["id"]
    run("todo_find_list", "R", name=TAG)
    run("todo_update_list", "W", list_id=lid, display_name=TAG + "-renamed")
    task = run("todo_create_task", "W", list_id=lid, title=f"{TAG} task",
               due_date="2026-12-01", importance="high", body="smoke test")
    if task:
        tid = task["id"]
        run("todo_list_tasks", "R", list_id=lid)
        run("todo_get_task", "R", list_id=lid, task_id=tid)
        run("todo_update_task", "W", list_id=lid, task_id=tid, title=f"{TAG} task (edited)")
        item = run("todo_add_checklist_item", "W", list_id=lid, task_id=tid, display_name="step 1")
        run("todo_list_checklist_items", "R", list_id=lid, task_id=tid)
        if item:
            run("todo_check_checklist_item", "W", list_id=lid, task_id=tid, item_id=item["id"])
            run("todo_delete_checklist_item", "D", list_id=lid, task_id=tid, item_id=item["id"])
        run("todo_complete_task", "W", list_id=lid, task_id=tid)
        run("todo_delete_task", "D", list_id=lid, task_id=tid)
    run("todo_delete_list", "D", list_id=lid)


def planner() -> None:
    run("planner_my_tasks", "R")
    plans = run("planner_list_plans", "R")
    rest = ["planner_get_plan", "planner_list_buckets", "planner_create_bucket",
            "planner_list_tasks", "planner_create_task", "planner_get_task",
            "planner_update_task", "planner_set_description", "planner_add_checklist_item",
            "planner_list_plan_members", "planner_find_member", "planner_assign_task",
            "planner_unassign_task", "planner_complete_task", "planner_delete_task"]
    if not plans:
        for t in rest:
            skip(t, "-", "no Planner plan in tenant")
        return
    # Prefer the plan with the most members, so assignment to others gets exercised.
    sizes = {p["id"]: len(TOOLS["planner_list_plan_members"](plan_id=p["id"])) for p in plans}
    pid = max(sizes, key=sizes.get)
    print(f"using plan {next(p['title'] for p in plans if p['id'] == pid)!r} ({sizes[pid]} members)")
    run("planner_get_plan", "R", plan_id=pid)
    run("planner_list_buckets", "R", plan_id=pid)
    bucket = run("planner_create_bucket", "W", plan_id=pid, name=TAG)
    if bucket:  # no delete-bucket tool, so clean up directly
        gc.delete_with_etag(f"planner/buckets/{bucket['id']}")
    run("planner_list_tasks", "R", plan_id=pid)
    task = run("planner_create_task", "W", plan_id=pid, title=f"{TAG} task", due_date="2026-12-01")
    members = run("planner_list_plan_members", "R", plan_id=pid) or []
    run("planner_find_member", "R", plan_id=pid, query="test")
    if not task:
        for t in rest[5:]:
            if t not in ("planner_list_plan_members", "planner_find_member"):
                skip(t, "-", "no test task")
        return
    tid = task["id"]
    me = gc.get("me")["id"]
    run("planner_get_task", "R", task_id=tid)
    run("planner_update_task", "W", task_id=tid, title=f"{TAG} task (edited)")
    run("planner_set_description", "W", task_id=tid, description="smoke test")
    run("planner_add_checklist_item", "W", task_id=tid, title="step 1")
    others = [m["id"] for m in members if m["id"] != me]
    who = others[:2] or [me]
    note = f"{len(who)} other user(s)" if others else "self only (no other members)"
    if run("planner_assign_task", "W", task_id=tid, assignee_ids=who) is not None:
        got = set(TOOLS["planner_get_task"](task_id=tid)["task"].get("assignments", {}))
        RESULTS[-1] = (*RESULTS[-1][:3], note + ("" if set(who) <= got else " - NOT APPLIED"))
    run("planner_unassign_task", "W", task_id=tid, assignee_ids=who)
    run("planner_complete_task", "W", task_id=tid)
    run("planner_delete_task", "D", task_id=tid)


def onenote() -> None:
    nbs = run("onenote_list_notebooks", "R")
    run("onenote_list_sections", "R")
    pages = run("onenote_list_pages", "R")
    run("onenote_search_pages", "R", query="a")
    if pages:
        run("onenote_get_page", "R", page_id=pages[0]["id"])
    else:
        skip("onenote_get_page", "R", "no pages" if nbs is not None else "listing failed")


if __name__ == "__main__":
    run("whoami", "R")
    for section in (todo, planner, onenote):
        try:
            section()
        except Exception:
            traceback.print_exc()
    width = max(len(r[0]) for r in RESULTS)
    print(f"\n{'tool'.ljust(width)}  R/W/D  result  note")
    for tool, kind, status, note in RESULTS:
        print(f"{tool.ljust(width)}  {kind:^5}  {status:<6}  {note}")
    counts = {s: sum(1 for r in RESULTS if r[2] == s) for s in ("PASS", "FAIL", "SKIP")}
    untested = sorted(set(TOOLS) - {r[0] for r in RESULTS})
    print(f"\n{counts}  untested: {untested or 'none'}")
