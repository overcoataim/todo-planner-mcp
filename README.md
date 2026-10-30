# todo-planner-mcp

An MCP (Model Context Protocol) server that exposes **Microsoft To Do** and
**Microsoft Planner** over the Microsoft Graph API with **delegated** auth, so an
assistant (e.g. a PA skill) can read *and* write your personal tasks (To Do) and your
assignable team tasks (Planner).

## Why

- The built-in Microsoft 365 connector is **read-only** and has **no Tasks/Planner** scope.
- To Do = personal catch-all. Planner = shared, assignable, cross-plan work.
- This server covers full CRUD on both, including **assigning Planner tasks to other people**.

## Requirements

- Python 3.10+
- A Microsoft Entra (Azure AD) **app registration**. Two supported modes:
  - **With a client secret** (confidential client, browser sign-in): add a **Web** platform
    redirect URI `http://localhost:8400/callback` (or whatever `TPM_REDIRECT_URI` is).
  - **Without a secret** (public client, device code): Authentication →
    **Allow public client flows: Yes**.
- Delegated API permissions: `User.Read`, `User.ReadBasic.All` (member names),
  `Tasks.ReadWrite` (To Do + Planner tasks), `Group.Read.All` (plan member lookup for
  assignment; needs tenant-admin consent), `Notes.Read.All` (OneNote, incl. team notebooks).
  See `docs/graph-reference.md` for the verified per-endpoint permissions.
  - Planner needs a **work/school account** (plans live on Microsoft 365 groups).
    A personal account can use To Do only.

## Setup

`<repo>` is your clone, `<venv>` wherever you keep the virtualenv
(on WSL, keep it in the Linux home, not on `/mnt/c` — it's flaky there).

```bash
python3 -m venv <venv>
<venv>/bin/pip install -e <repo>      # Windows: <venv>\Scripts\pip
cd <repo>
cp .env.example .env                  # set TPM_CLIENT_ID, TPM_TENANT_ID, TPM_CLIENT_SECRET
cp .mcp.json.example .mcp.json        # set "command" to <venv>/bin/python
<venv>/bin/python -m todo_planner_mcp.server --check   # offline sanity check
<venv>/bin/python -m todo_planner_mcp.server --login   # one-time sign-in
```

`--login` prints a sign-in URL (secret mode: open it, and the browser redirects back to
the localhost listener; WSL2 forwards localhost from Windows) or a device code (public mode).
The token is cached at `TPM_TOKEN_CACHE` and refreshed silently afterwards.
`.env` is read from the project root regardless of the working directory.

## Run as an MCP server

`.mcp.json` in the project root (git-ignored; copy from `.mcp.json.example`) registers the server with Claude Code (stdio). Claude Code
asks you to approve it the first time you start a session here. Run `--login` first,
because an interactive sign-in can't happen cleanly inside the MCP process.

## Tools

**To Do:** `todo_list_lists`, `todo_find_list`, `todo_create_list`, `todo_update_list`,
`todo_delete_list`, `todo_list_tasks`, `todo_get_task`, `todo_create_task`,
`todo_update_task`, `todo_complete_task`, `todo_delete_task`,
`todo_list_checklist_items`, `todo_add_checklist_item`, `todo_check_checklist_item`,
`todo_delete_checklist_item`.

**Planner:** `planner_my_tasks`, `planner_list_plans`, `planner_get_plan`,
`planner_list_buckets`, `planner_create_bucket`, `planner_list_tasks`,
`planner_get_task`, `planner_create_task`, `planner_update_task`,
`planner_assign_task`, `planner_unassign_task`, `planner_complete_task`,
`planner_delete_task`, `planner_set_description`, `planner_add_checklist_item`,
`planner_list_plan_members`, `planner_find_member`.

**OneNote (read-only, `Notes.Read`):** `onenote_list_notebooks`, `onenote_list_sections`,
`onenote_list_pages`, `onenote_search_pages` (title only), `onenote_get_page` (content as plain text).

**Health:** `whoami`.

## Notes

- Planner PATCH/DELETE use optimistic concurrency; the client fetches the current
  `@odata.etag` and retries once on 409/412 automatically.
- Secrets: `.env` and the token cache are git-ignored. Keep them private.

## License

MIT. See [LICENSE](LICENSE). Provided as-is, without warranty.
