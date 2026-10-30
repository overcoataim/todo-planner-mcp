"""Scenario test: a simulated work week for three users sharing one Planner plan.

Each step runs as a specific user (their own token cache) and checks the result from
another user's point of view. Data is prefixed WEEK- and LEFT IN PLACE so it can be
inspected in the To Do / Planner web apps.

    <venv>/bin/python scripts/scenario_week.py            # build + play the week
    <venv>/bin/python scripts/scenario_week.py --cleanup  # delete all WEEK- data

Token caches (sign each user in once with TPM_TOKEN_CACHE=... server --login):
    admin  -> ~/.todo_planner_mcp/token_cache.bin
    test1 -> ~/.todo_planner_mcp/test1.bin
    test2 -> ~/.todo_planner_mcp/test2.bin
"""
from __future__ import annotations

import os
import sys

from todo_planner_mcp.server import build_server

CACHES = {"admin": "~/.todo_planner_mcp/token_cache.bin",
          "test1": "~/.todo_planner_mcp/test1.bin",
          "test2": "~/.todo_planner_mcp/test2.bin"}
PLAN_TITLE = "testplan"
P = "WEEK-"
MON, TUE, WED, THU, FRI = (f"2026-10-{d}" for d in (12, 13, 14, 15, 16))

STEPS: list[tuple[str, str, str, str]] = []  # (day, user, step, PASS/FAIL + note)


class User:
    """One signed-in user: their tools + Graph client."""

    def __init__(self, name: str):
        os.environ["TPM_TOKEN_CACHE"] = CACHES[name]
        mcp, self.gc, _ = build_server()
        self.t = {t.name: t.fn for t in mcp._tool_manager.list_tools()}
        self.name = name
        self.id = self.t["whoami"]()["id"]

    def __getattr__(self, tool):
        return self.t[tool]


def step(day: str, user: User, what: str, fn, check=None):
    """Run fn() as user; optionally check(result) -> (ok, note). Records the outcome."""
    try:
        out = fn()
        ok, note = check(out) if check else (True, "")
    except Exception as e:
        out, ok, note = None, False, str(e).splitlines()[0][:80]
    STEPS.append((day, user.name, what, ("PASS" if ok else "FAIL") + (f"  {note}" if note else "")))
    return out


def plan_id(u: User) -> str:
    return next(p["id"] for p in u.planner_list_plans() if p["title"] == PLAN_TITLE)


def bucket(u: User, pid: str, name: str) -> str:
    for b in u.planner_list_buckets(plan_id=pid):
        if b.get("name") == P + name:
            return b["id"]
    return u.planner_create_bucket(plan_id=pid, name=P + name)["id"]


def assignees(u: User, tid: str) -> set[str]:
    return set((u.planner_get_task(task_id=tid)["task"].get("assignments") or {}).keys())


def cleanup(users: dict[str, User]) -> None:
    admin = users["admin"]
    pid = plan_id(admin)
    n = 0
    for t in admin.planner_list_tasks(plan_id=pid):
        if (t.get("title") or "").startswith(P):
            admin.planner_delete_task(task_id=t["id"]); n += 1
    for b in admin.planner_list_buckets(plan_id=pid):
        if (b.get("name") or "").startswith(P):
            admin.gc.delete_with_etag(f"planner/buckets/{b['id']}"); n += 1
    for u in users.values():
        for l in u.todo_list_lists():
            if (l.get("displayName") or "").startswith(P):
                u.todo_delete_list(list_id=l["id"]); n += 1
    print(f"cleanup: deleted {n} WEEK- items")


def main() -> None:
    users = {n: User(n) for n in CACHES}
    admin, t1, t2 = users["admin"], users["test1"], users["test2"]
    if "--cleanup" in sys.argv:
        cleanup(users)
        return
    cleanup(users)  # start from a clean slate each run

    # ---------- Monday: Admin sets up the week ----------
    pid = step(MON, admin, "find shared plan", lambda: plan_id(admin))
    backlog = step(MON, admin, "create bucket Backlog", lambda: bucket(admin, pid, "Backlog"))
    doing = step(MON, admin, "create bucket In progress", lambda: bucket(admin, pid, "In progress"))
    done = step(MON, admin, "create bucket Done", lambda: bucket(admin, pid, "Done"))

    plan = [  # title, assignees, due, priority, description, checklist
        ("Draft launch email", [t1], WED, 3, "Announce v2 to existing customers.", ["Outline", "First draft", "Admin review"]),
        ("Update pricing page", [t2], TUE, 5, "New tiers go live Thursday.", ["Copy", "Screenshots"]),
        ("QA checkout flow", [t1, t2], THU, 1, "Card, invoice and trial paths.", ["Card", "Invoice", "Trial"]),
        ("Book venue for demo day", [admin], WED, 5, "", []),
        ("Prepare sales deck", [t2], FRI, 3, "Ten slides max.", ["Story", "Pricing slide"]),
        ("Write release notes", [t1], FRI, 5, "", ["Features", "Fixes"]),
    ]
    tasks: dict[str, str] = {}
    for title, who, due, prio, desc, items in plan:
        tid = step(MON, admin, f"create '{title}' -> {','.join(u.name for u in who)}",
                   lambda: admin.planner_create_task(plan_id=pid, title=P + title, bucket_id=backlog,
                                                    due_date=due, priority=prio,
                                                    assignee_ids=[u.id for u in who])["id"])
        tasks[title] = tid
        if desc:
            step(MON, admin, f"  describe '{title}'", lambda: admin.planner_set_description(task_id=tid, description=desc))
        for it in items:
            step(MON, admin, f"  checklist '{it}'", lambda: admin.planner_add_checklist_item(task_id=tid, title=it))

    # Each person's own To Do for the week.
    personal = {
        admin: [("1:1 with test1", TUE, "normal"), ("Approve launch budget", MON, "high"), ("Expenses", FRI, "low")],
        t1: [("Read QA test plan", MON, "high"), ("Dentist", WED, "normal")],
        t2: [("Gather pricing screenshots", MON, "high"), ("Timesheet", FRI, "normal")],
    }
    todo: dict[tuple[str, str], tuple[str, str]] = {}
    for u, items in personal.items():
        lid = step(MON, u, "create To Do list 'Work week'",
                   lambda: u.todo_create_list(display_name=P + "Work week")["id"])
        for title, due, imp in items:
            tid = step(MON, u, f"  todo '{title}'", lambda: u.todo_create_task(
                list_id=lid, title=title, due_date=due, importance=imp)["id"])
            todo[(u.name, title)] = (lid, tid)

    # Cross-user visibility: assignees see the admin's tasks in "Assigned to me".
    def sees(u: User, titles: list[str]):
        mine = {t["title"] for t in u.planner_my_tasks()}
        missing = [t for t in titles if P + t not in mine]
        return (not missing, f"missing {missing}" if missing else f"sees {len(titles)}")
    step(MON, t1, "my tasks show the admin's assignments", lambda: None,
         lambda _: sees(t1, ["Draft launch email", "QA checkout flow", "Write release notes"]))
    step(MON, t2, "my tasks show the admin's assignments", lambda: None,
         lambda _: sees(t2, ["Update pricing page", "QA checkout flow", "Prepare sales deck"]))

    # ---------- Tuesday ----------
    step(TUE, t2, "start pricing page", lambda: t2.planner_update_task(
        task_id=tasks["Update pricing page"], bucket_id=doing, percent_complete=50))
    step(TUE, t2, "finish pricing page", lambda: (
        t2.planner_update_task(task_id=tasks["Update pricing page"], bucket_id=done),
        t2.planner_complete_task(task_id=tasks["Update pricing page"])))
    step(TUE, admin, "sees pricing page done", lambda: admin.planner_get_task(task_id=tasks["Update pricing page"]),
         lambda r: (r["task"]["percentComplete"] == 100 and r["task"]["bucketId"] == done,
                    f"{r['task']['percentComplete']}%"))
    step(TUE, t1, "start launch email", lambda: t1.planner_update_task(
        task_id=tasks["Draft launch email"], bucket_id=doing, percent_complete=50))
    step(TUE, admin, "tick off 1:1", lambda: admin.todo_complete_task(*todo[("admin", "1:1 with test1")]))
    step(TUE, t2, "tick off screenshots", lambda: t2.todo_complete_task(*todo[("test2", "Gather pricing screenshots")]))

    # ---------- Wednesday ----------
    step(WED, t1, "finish launch email", lambda: (
        t1.planner_update_task(task_id=tasks["Draft launch email"], bucket_id=done),
        t1.planner_complete_task(task_id=tasks["Draft launch email"])))
    step(WED, admin, "book venue", lambda: admin.planner_complete_task(task_id=tasks["Book venue for demo day"]))
    step(WED, t1, "add a To Do from the plan", lambda: t1.todo_create_task(
        list_id=todo[("test1", "Dentist")][0], title="Chase QA test accounts", due_date=THU, importance="high"))

    # ---------- Thursday: handover ----------
    qa = tasks["QA checkout flow"]
    step(THU, t1, "hand QA over to test2 (unassign self)", lambda: t1.planner_unassign_task(task_id=qa, assignee_ids=[t1.id]),
         lambda _: (assignees(t1, qa) == {t2.id}, "now test2 only"))
    step(THU, t2, "QA still in test2's tasks", lambda: None, lambda _: sees(t2, ["QA checkout flow"]))
    step(THU, t1, "QA gone from test1's tasks", lambda: None,
         lambda _: (P + "QA checkout flow" not in {t["title"] for t in t1.planner_my_tasks()}, ""))
    step(THU, t2, "finish QA", lambda: t2.planner_complete_task(task_id=qa))
    step(THU, admin, "push release notes to Monday", lambda: admin.planner_update_task(
        task_id=tasks["Write release notes"], due_date="2026-10-19"))

    # ---------- Friday: review ----------
    def review(_):
        ts = [t for t in admin.planner_list_tasks(plan_id=pid) if t["title"].startswith(P)]
        open_ = sorted(t["title"][len(P):] for t in ts if t["percentComplete"] != 100)
        return (len(ts) == len(plan), f"{len(ts) - len(open_)}/{len(ts)} done; open: {open_}")
    step(FRI, admin, "review the plan", lambda: None, review)
    step(FRI, admin, "tick off expenses", lambda: admin.todo_complete_task(*todo[("admin", "Expenses")]))
    step(FRI, t2, "tick off timesheet", lambda: t2.todo_complete_task(*todo[("test2", "Timesheet")]))
    step(FRI, t1, "can't see the admin's personal To Do", lambda: None,
         lambda _: (not any(l["displayName"] == P + "Work week" and l["id"] == todo[("admin", "Expenses")][0]
                            for l in t1.todo_list_lists()), "private, as expected"))

    width = max(len(s[2]) for s in STEPS)
    print(f"\n{'day':<10} {'user':<6} {'step'.ljust(width)}  result")
    for day, who, what, res in STEPS:
        print(f"{day:<10} {who:<6} {what.ljust(width)}  {res}")
    fails = sum(1 for s in STEPS if s[3].startswith("FAIL"))
    print(f"\n{len(STEPS) - fails} passed, {fails} failed")


if __name__ == "__main__":
    main()
