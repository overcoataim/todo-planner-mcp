"""Microsoft To Do tools (full CRUD over /me/todo)."""
from __future__ import annotations

from typing import Any

from .graph import GraphClient

_IMPORTANCE = {"low", "normal", "high"}
_STATUS = {"notStarted", "inProgress", "completed", "waitingOnOthers", "deferred"}


def _dt(value: str | None, tz: str = "UTC") -> dict | None:
    """Build a Graph dateTimeTimeZone object from an ISO date/datetime string."""
    if not value:
        return None
    v = value.strip()
    if len(v) == 10:  # date only -> midnight
        v = f"{v}T00:00:00"
    return {"dateTime": v, "timeZone": tz}


def register(mcp, gc: GraphClient) -> None:

    # ---------- lists ----------
    @mcp.tool()
    def todo_list_lists() -> list[dict]:
        """List all Microsoft To Do task lists (id + displayName + wellknownListName)."""
        lists = gc.get_all("me/todo/lists")
        return [
            {"id": l["id"], "displayName": l.get("displayName"),
             "wellknownListName": l.get("wellknownListName")}
            for l in lists
        ]

    @mcp.tool()
    def todo_find_list(name: str) -> dict:
        """Find a To Do list by (case-insensitive) display name. Returns the first match or {}."""
        for l in gc.get_all("me/todo/lists"):
            if (l.get("displayName") or "").lower() == name.lower():
                return {"id": l["id"], "displayName": l.get("displayName")}
        return {}

    @mcp.tool()
    def todo_create_list(display_name: str) -> dict:
        """Create a new To Do list."""
        return gc.post("me/todo/lists", {"displayName": display_name})

    @mcp.tool()
    def todo_update_list(list_id: str, display_name: str) -> dict:
        """Rename a To Do list."""
        return gc.patch(f"me/todo/lists/{list_id}", {"displayName": display_name})

    @mcp.tool()
    def todo_delete_list(list_id: str) -> str:
        """Delete a To Do list."""
        gc.delete(f"me/todo/lists/{list_id}")
        return "deleted"

    # ---------- tasks ----------
    @mcp.tool()
    def todo_list_tasks(list_id: str, status: str | None = None, top: int = 100) -> list[dict]:
        """List tasks in a list. Optional status filter (notStarted|inProgress|completed|waitingOnOthers|deferred)."""
        params: dict[str, Any] = {"$top": min(top, 100)}
        if status:
            params["$filter"] = f"status eq '{status}'"
        tasks = gc.get_all(f"me/todo/lists/{list_id}/tasks", params=params, max_items=top)
        return [_task_summary(t) for t in tasks]

    @mcp.tool()
    def todo_get_task(list_id: str, task_id: str) -> dict:
        """Get a single To Do task with full detail."""
        return gc.get(f"me/todo/lists/{list_id}/tasks/{task_id}")

    @mcp.tool()
    def todo_create_task(
        list_id: str,
        title: str,
        due_date: str | None = None,
        reminder_date: str | None = None,
        importance: str | None = None,
        body: str | None = None,
        time_zone: str = "UTC",
    ) -> dict:
        """Create a To Do task. due_date/reminder_date accept 'YYYY-MM-DD' or ISO datetime.
        importance: low|normal|high. body is the task note text."""
        payload: dict[str, Any] = {"title": title}
        if due_date:
            payload["dueDateTime"] = _dt(due_date, time_zone)
        if reminder_date:
            payload["isReminderOn"] = True
            payload["reminderDateTime"] = _dt(reminder_date, time_zone)
        if importance:
            if importance not in _IMPORTANCE:
                raise ValueError(f"importance must be one of {sorted(_IMPORTANCE)}")
            payload["importance"] = importance
        if body:
            payload["body"] = {"content": body, "contentType": "text"}
        return gc.post(f"me/todo/lists/{list_id}/tasks", payload)

    @mcp.tool()
    def todo_update_task(
        list_id: str,
        task_id: str,
        title: str | None = None,
        due_date: str | None = None,
        reminder_date: str | None = None,
        importance: str | None = None,
        status: str | None = None,
        body: str | None = None,
        time_zone: str = "UTC",
    ) -> dict:
        """Update any subset of a task's fields. status: notStarted|inProgress|completed|waitingOnOthers|deferred."""
        payload: dict[str, Any] = {}
        if title is not None:
            payload["title"] = title
        if due_date is not None:
            payload["dueDateTime"] = _dt(due_date, time_zone)
        if reminder_date is not None:
            payload["isReminderOn"] = True
            payload["reminderDateTime"] = _dt(reminder_date, time_zone)
        if importance is not None:
            if importance not in _IMPORTANCE:
                raise ValueError(f"importance must be one of {sorted(_IMPORTANCE)}")
            payload["importance"] = importance
        if status is not None:
            if status not in _STATUS:
                raise ValueError(f"status must be one of {sorted(_STATUS)}")
            payload["status"] = status
        if body is not None:
            payload["body"] = {"content": body, "contentType": "text"}
        if not payload:
            raise ValueError("no fields to update")
        return gc.patch(f"me/todo/lists/{list_id}/tasks/{task_id}", payload)

    @mcp.tool()
    def todo_complete_task(list_id: str, task_id: str) -> dict:
        """Mark a task completed."""
        return gc.patch(f"me/todo/lists/{list_id}/tasks/{task_id}", {"status": "completed"})

    @mcp.tool()
    def todo_delete_task(list_id: str, task_id: str) -> str:
        """Delete a task."""
        gc.delete(f"me/todo/lists/{list_id}/tasks/{task_id}")
        return "deleted"

    # ---------- checklist items (subtasks) ----------
    @mcp.tool()
    def todo_list_checklist_items(list_id: str, task_id: str) -> list[dict]:
        """List checklist items (subtasks) of a task."""
        return gc.get_all(f"me/todo/lists/{list_id}/tasks/{task_id}/checklistItems")

    @mcp.tool()
    def todo_add_checklist_item(list_id: str, task_id: str, display_name: str) -> dict:
        """Add a checklist item (subtask) to a task."""
        return gc.post(
            f"me/todo/lists/{list_id}/tasks/{task_id}/checklistItems",
            {"displayName": display_name},
        )

    @mcp.tool()
    def todo_check_checklist_item(list_id: str, task_id: str, item_id: str, checked: bool = True) -> dict:
        """Mark a checklist item checked/unchecked."""
        return gc.patch(
            f"me/todo/lists/{list_id}/tasks/{task_id}/checklistItems/{item_id}",
            {"isChecked": checked},
        )

    @mcp.tool()
    def todo_delete_checklist_item(list_id: str, task_id: str, item_id: str) -> str:
        """Delete a checklist item."""
        gc.delete(f"me/todo/lists/{list_id}/tasks/{task_id}/checklistItems/{item_id}")
        return "deleted"


def _task_summary(t: dict) -> dict:
    return {
        "id": t.get("id"),
        "title": t.get("title"),
        "status": t.get("status"),
        "importance": t.get("importance"),
        "due": (t.get("dueDateTime") or {}).get("dateTime"),
        "reminder": (t.get("reminderDateTime") or {}).get("dateTime"),
        "hasChecklist": bool(t.get("checklistItems")),
    }
