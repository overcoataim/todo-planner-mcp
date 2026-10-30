"""Microsoft Planner tools (plans, buckets, tasks, assignment) over /planner.

Planner requires a work/school account (plans are owned by Microsoft 365 groups).
Updates/deletes use optimistic concurrency: GraphClient.patch_with_etag handles
fetching the current @odata.etag and retrying once on 409/412.
"""
from __future__ import annotations

import json
import uuid
from typing import Any

from mcp.server.fastmcp import Context

from .graph import GraphClient, GraphError
from .optin import OptInGuard

# percentComplete convention: 0 = notStarted, 1-99 = inProgress, 100 = completed
_ASSIGN_TYPE = "#microsoft.graph.plannerAssignment"
_DEFAULT_ORDER_HINT = " !"


def register(mcp, gc: GraphClient, optin_group: str = "") -> None:
    guard = OptInGuard(gc, optin_group)

    # ---------- discovery ----------
    @mcp.tool()
    def planner_my_tasks() -> list[dict]:
        """List Planner tasks assigned to me across all plans (the 'Assigned to me' view)."""
        tasks = gc.get_all("me/planner/tasks")
        return [_task_summary(t) for t in tasks]

    @mcp.tool()
    def planner_list_plans() -> list[dict]:
        """List Planner plans I can see: shared with me, plus plans in my M365 groups."""
        # /me/planner/plans misses group-owned plans (verified live 2026-10-12),
        # so also walk my groups.
        plans = {p["id"]: p for p in gc.get_all("me/planner/plans")}
        groups = gc.get_all("me/memberOf", params={"$select": "id"})
        for g in groups:
            if g.get("@odata.type") != "#microsoft.graph.group":
                continue
            try:
                for p in gc.get_all(f"groups/{g['id']}/planner/plans"):
                    plans.setdefault(p["id"], p)
            except GraphError:
                continue  # e.g. security groups with no Planner
        return [{"id": p["id"], "title": p.get("title"), "groupId": _plan_group_id(p)}
                for p in plans.values()]

    @mcp.tool()
    def planner_get_plan(plan_id: str) -> dict:
        """Get a plan's metadata."""
        return gc.get(f"planner/plans/{plan_id}")

    @mcp.tool()
    def planner_list_buckets(plan_id: str) -> list[dict]:
        """List the buckets (columns) of a plan."""
        return [{"id": b["id"], "name": b.get("name"), "orderHint": b.get("orderHint")}
                for b in gc.get_all(f"planner/plans/{plan_id}/buckets")]

    @mcp.tool()
    def planner_create_bucket(plan_id: str, name: str) -> dict:
        """Create a bucket in a plan."""
        return gc.post("planner/buckets", {"name": name, "planId": plan_id, "orderHint": _DEFAULT_ORDER_HINT})

    # ---------- tasks ----------
    @mcp.tool()
    def planner_list_tasks(plan_id: str) -> list[dict]:
        """List all tasks in a plan."""
        return [_task_summary(t) for t in gc.get_all(f"planner/plans/{plan_id}/tasks")]

    @mcp.tool()
    def planner_get_task(task_id: str) -> dict:
        """Get a Planner task plus its details (checklist, description, references)."""
        task = gc.get(f"planner/tasks/{task_id}")
        try:
            details = gc.get(f"planner/tasks/{task_id}/details")
        except GraphError:
            details = {}
        return {"task": task, "details": details}

    # ---------- tasks: changes (opt-in guarded) ----------
    def _involved(task_id: str, extra: list[str] | None = None) -> tuple[dict, set[str]]:
        """The task plus everyone it involves: assignees, creator, and any new assignees."""
        task = gc.get(f"planner/tasks/{task_id}")
        people = set((task.get("assignments") or {}).keys())
        creator = ((task.get("createdBy") or {}).get("user") or {}).get("id")
        if creator:
            people.add(creator)
        people.update(extra or [])
        return task, people

    async def _guard(ctx, tool: str, task_id: str, verb: str, args: dict,
                     confirm_code: str | None, extra: list[str] | None = None) -> dict | None:
        task, people = _involved(task_id, extra)
        key = f"{tool}|{task_id}|{json.dumps(args, sort_keys=True, default=str)}"
        return await guard.check(ctx, f"{verb} '{task.get('title')}'", key, people, confirm_code)

    @mcp.tool()
    async def planner_create_task(
        plan_id: str,
        title: str,
        bucket_id: str | None = None,
        due_date: str | None = None,
        start_date: str | None = None,
        assignee_ids: list[str] | None = None,
        priority: int | None = None,
        confirm_code: str | None = None,
        ctx: Context | None = None,
    ) -> dict:
        """Create a Planner task. due_date/start_date: 'YYYY-MM-DD' or ISO datetime.
        assignee_ids: AAD user ids to assign. priority: 0-10 (1 urgent, 3 important, 5 medium, 9 low).
        Assigning people who haven't signed up needs the user's confirmation (see confirm_code)."""
        args = {"plan_id": plan_id, "title": title, "bucket_id": bucket_id, "due_date": due_date,
                "start_date": start_date, "assignee_ids": sorted(assignee_ids or []), "priority": priority}
        stop = await guard.check(ctx, f"Creating '{title}' assigned to them",
                                 f"planner_create_task|{json.dumps(args, sort_keys=True)}",
                                 assignee_ids or [], confirm_code)
        if stop:
            return stop
        payload: dict[str, Any] = {"planId": plan_id, "title": title}
        if bucket_id:
            payload["bucketId"] = bucket_id
        if due_date:
            payload["dueDateTime"] = _iso(due_date)
        if start_date:
            payload["startDateTime"] = _iso(start_date)
        if priority is not None:
            payload["priority"] = priority
        if assignee_ids:
            payload["assignments"] = _assignments(assignee_ids)
        return gc.post("planner/tasks", payload)

    @mcp.tool()
    async def planner_update_task(
        task_id: str,
        title: str | None = None,
        bucket_id: str | None = None,
        due_date: str | None = None,
        start_date: str | None = None,
        percent_complete: int | None = None,
        priority: int | None = None,
        confirm_code: str | None = None,
        ctx: Context | None = None,
    ) -> dict:
        """Update task fields (ETag handled automatically). percent_complete: 0|1-99|100.
        Changing a task that involves people who haven't signed up needs the user's confirmation."""
        payload: dict[str, Any] = {}
        if title is not None:
            payload["title"] = title
        if bucket_id is not None:
            payload["bucketId"] = bucket_id
        if due_date is not None:
            payload["dueDateTime"] = _iso(due_date)
        if start_date is not None:
            payload["startDateTime"] = _iso(start_date)
        if percent_complete is not None:
            payload["percentComplete"] = percent_complete
        if priority is not None:
            payload["priority"] = priority
        if not payload:
            raise ValueError("no fields to update")
        if stop := await _guard(ctx, "planner_update_task", task_id, "Updating", payload, confirm_code):
            return stop
        return gc.patch_with_etag(f"planner/tasks/{task_id}", payload)

    @mcp.tool()
    async def planner_assign_task(task_id: str, assignee_ids: list[str], confirm_code: str | None = None,
                                  ctx: Context | None = None) -> dict:
        """Assign a task to one or more users (AAD user ids). Adds to existing assignees.
        Involving people who haven't signed up needs the user's confirmation (see confirm_code)."""
        if stop := await _guard(ctx, "planner_assign_task", task_id, "Assigning",
                                {"assignee_ids": sorted(assignee_ids)}, confirm_code, extra=assignee_ids):
            return stop
        return gc.patch_with_etag(f"planner/tasks/{task_id}", {"assignments": _assignments(assignee_ids)})

    @mcp.tool()
    async def planner_unassign_task(task_id: str, assignee_ids: list[str], confirm_code: str | None = None,
                                    ctx: Context | None = None) -> dict:
        """Remove one or more users from a task's assignees.
        Involving people who haven't signed up needs the user's confirmation (see confirm_code)."""
        if stop := await _guard(ctx, "planner_unassign_task", task_id, "Unassigning people from",
                                {"assignee_ids": sorted(assignee_ids)}, confirm_code):
            return stop
        assignments = {uid: None for uid in assignee_ids}
        return gc.patch_with_etag(f"planner/tasks/{task_id}", {"assignments": assignments})

    @mcp.tool()
    async def planner_complete_task(task_id: str, confirm_code: str | None = None,
                                    ctx: Context | None = None) -> dict:
        """Mark a Planner task complete (percentComplete=100).
        Involving people who haven't signed up needs the user's confirmation (see confirm_code)."""
        if stop := await _guard(ctx, "planner_complete_task", task_id, "Completing", {}, confirm_code):
            return stop
        return gc.patch_with_etag(f"planner/tasks/{task_id}", {"percentComplete": 100})

    @mcp.tool()
    async def planner_delete_task(task_id: str, confirm_code: str | None = None,
                                  ctx: Context | None = None) -> dict | str:
        """Delete a Planner task.
        Involving people who haven't signed up needs the user's confirmation (see confirm_code)."""
        if stop := await _guard(ctx, "planner_delete_task", task_id, "Deleting", {}, confirm_code):
            return stop
        gc.delete_with_etag(f"planner/tasks/{task_id}")
        return "deleted"

    @mcp.tool()
    async def planner_set_description(task_id: str, description: str, confirm_code: str | None = None,
                                      ctx: Context | None = None) -> dict:
        """Set the task's description (on plannerTaskDetails).
        Involving people who haven't signed up needs the user's confirmation (see confirm_code)."""
        if stop := await _guard(ctx, "planner_set_description", task_id, "Changing the description of",
                                {"description": description}, confirm_code):
            return stop
        return gc.patch_with_etag(f"planner/tasks/{task_id}/details", {"description": description})

    @mcp.tool()
    async def planner_add_checklist_item(task_id: str, title: str, confirm_code: str | None = None,
                                         ctx: Context | None = None) -> dict:
        """Add a checklist item to a Planner task (stored on task details).
        Involving people who haven't signed up needs the user's confirmation (see confirm_code)."""
        if stop := await _guard(ctx, "planner_add_checklist_item", task_id, "Adding a checklist item to",
                                {"title": title}, confirm_code):
            return stop
        item_id = str(uuid.uuid4())
        body = {"checklist": {item_id: {
            "@odata.type": "#microsoft.graph.plannerChecklistItem",
            "title": title, "isChecked": False}}}
        return gc.patch_with_etag(f"planner/tasks/{task_id}/details", body)

    # ---------- member resolution (for assignment) ----------
    @mcp.tool()
    def planner_list_plan_members(plan_id: str) -> list[dict]:
        """List the members of a plan's owning M365 group (to get user ids for assignment)."""
        plan = gc.get(f"planner/plans/{plan_id}")
        group_id = _plan_group_id(plan)
        if not group_id:
            return []
        members = gc.get_all(f"groups/{group_id}/members",
                             params={"$select": "id,displayName,mail,userPrincipalName"})
        return [{"id": m.get("id"), "displayName": m.get("displayName"),
                 "mail": m.get("mail") or m.get("userPrincipalName")} for m in members]

    @mcp.tool()
    def planner_find_member(plan_id: str, query: str) -> list[dict]:
        """Find plan members by name or email substring (returns user ids for assignment)."""
        q = query.lower()
        out = []
        for m in planner_list_plan_members(plan_id):
            hay = f"{m.get('displayName','')} {m.get('mail','')}".lower()
            if q in hay:
                out.append(m)
        return out


# ---------- helpers ----------
def _iso(value: str) -> str:
    v = value.strip()
    if len(v) == 10:
        v = f"{v}T00:00:00Z"
    elif not v.endswith("Z") and "+" not in v:
        v = f"{v}Z"
    return v


def _assignments(user_ids: list[str]) -> dict:
    return {uid: {"@odata.type": _ASSIGN_TYPE, "orderHint": _DEFAULT_ORDER_HINT} for uid in user_ids}


def _plan_group_id(plan: dict) -> str | None:
    container = plan.get("container") or {}
    if container.get("type") == "group" and container.get("containerId"):
        return container["containerId"]
    return plan.get("owner")


def _task_summary(t: dict) -> dict:
    return {
        "id": t.get("id"),
        "title": t.get("title"),
        "planId": t.get("planId"),
        "bucketId": t.get("bucketId"),
        "percentComplete": t.get("percentComplete"),
        "priority": t.get("priority"),
        "due": t.get("dueDateTime"),
        "assignees": list((t.get("assignments") or {}).keys()),
    }
