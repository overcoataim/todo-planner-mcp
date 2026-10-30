# todo-planner-mcp — project context for Claude Code

> Personal notes (test tenant, accounts, plans, working preferences) go in `CLAUDE.local.md`,
> which is git-ignored. Never put tenant/client IDs, secrets, usernames or tenant names in
> committed files — they live in `.env` only.

> **Graph API facts live in `docs/graph-reference.md`** (verified against Microsoft Learn).
> Read it before touching Graph code.
> **Admin consent is the #1 adoption blocker — see `docs/admin-consent.md`** (feature tiers,
> MS-managed default policy blocks user consent to `Tasks.ReadWrite`). Update it when adding scopes.
> Scoped consent policy (admin runs once, users self-consent): `docs/consent-policy.md` +
> `scripts/consent-policy.ps1` — UNTESTED.

## 1. What this is
An MCP server that exposes **Microsoft To Do**, **Microsoft Planner** and **OneNote (read-only)**
over the Microsoft Graph API with **delegated** auth, so an assistant can read *and write*
personal tasks (To Do) and assignable team tasks (Planner).

Why it exists:
- The built-in Microsoft 365 connector is **read-only** and has **no Tasks/Planner** scope.
- To Do = personal catch-all. Planner = shared, assignable, cross-plan work.
- It runs as a **local stdio MCP server** in Claude Code config — no hosting needed.

## 2. Status
- **38 tools** (15 To Do, 17 Planner, 5 OneNote read-only, `whoami`), **live-tested**: 38/38 pass
  for an admin and two non-admin users on a Business Basic test tenant.
- **Live findings (fixed):** `/me/planner/plans` and `/me/onenote` miss **group-owned**
  plans/notebooks → both also walk `/me/memberOf` groups. Plan members come back as bare ids
  without `User.ReadBasic.All`. `/me/onenote` returns **401 (code 40001)** until the user's
  OneDrive is provisioned; OneNote tools skip a failing root. Planner has a few seconds of
  cross-user read lag after a write.
- **Opt-in guardrail** (`optin.py`, `TPM_OPTIN_GROUP`): every Planner change that involves someone
  outside the opt-in Entra group (assignees, creator, new assignees) needs the **user's explicit
  confirmation** — MCP elicitation prompt where the client supports it (Claude Code ≥2.1.76; not
  Claude Desktop/claude.ai yet), else a one-time `confirm_code` bound to the exact change.
  Pair it with Entra "Assignment required" on the enterprise app as the sign-in gate
  (non-assigned users get AADSTS50105). **Assigning a group to an app needs Entra ID P1**; on
  basic plans assign users individually. It's a guardrail in our code, not Microsoft security;
  only the elicitation prompt can't be bypassed by the model.
- Login forces the account picker (`prompt=select_account`) and ignores stale-tab redirects.
- **Parked:** 429/503 throttling (honour Retry-After, else backoff) in `graph.py`; trimming noisy
  fields in tool output; Loop components (OneDrive `.loop` via `?format=html`, needs `Files.Read.All`).

## 3. Architecture & files
```
todo-planner-mcp/
  src/todo_planner_mcp/
    config.py        # .env loader (no dep) -> Settings (client_id, authority, scopes, cache, opt-in group)
    auth.py          # MSAL public (device code) or confidential (secret, auth-code) client; LAZY init
    graph.py         # httpx client: auth header, paging (get_all), ETag If-Match + 412 retry
    todo_tools.py    # To Do CRUD: lists, tasks, checklist items
    planner_tools.py # Planner: plans/buckets/tasks CRUD, assign/unassign, details, member lookup
    onenote_tools.py # OneNote READ-ONLY: notebooks/sections/pages, page HTML -> plain text
    optin.py         # opt-in guardrail: group lookup, elicitation, one-time confirm codes
    server.py        # FastMCP server; build_server(); main() with --check / --login / --logout
  scripts/
    smoke_live.py    # calls every tool live, cleans up, prints a pass/fail matrix
    guard_test.py    # live checks of the opt-in guardrail (both confirmation paths)
    scenario_week.py # 3-user simulated work week on a shared plan (WEEK- data; --cleanup)
  .env.example / .mcp.json.example
```

## 4. Auth model
- Secret set → **confidential client**, auth-code flow, localhost redirect listener.
  No secret → **public client**, **device-code flow** (not yet live-tested).
- Prompts print to **STDERR** (STDOUT is the MCP channel — do not break this).
- Token cached at `TPM_TOKEN_CACHE` (default `~/.todo_planner_mcp/token_cache.bin`), silent refresh
  after first sign-in. For several test users, give each its own cache via `TPM_TOKEN_CACHE=...`.
- `.env` keys: `TPM_CLIENT_ID`, `TPM_TENANT_ID`, `TPM_CLIENT_SECRET`, `TPM_REDIRECT_URI`,
  `TPM_AUTHORITY`, `TPM_SCOPES`, `TPM_TOKEN_CACHE`, `TPM_OPTIN_GROUP`.
- Entra app registration: secret mode needs a **Web** redirect URI matching `TPM_REDIRECT_URI`;
  public mode needs **Allow public client flows = Yes**.

## 5. Scopes & account constraints
- Delegated: `User.Read`, `User.ReadBasic.All` (member names), `Tasks.ReadWrite`,
  `Group.Read.All` (plan members + opt-in group check; **admin consent**), `Notes.Read.All`.
- **Planner requires a work/school account.** A personal Microsoft account gets To Do (and its
  own OneNote) only.

## 6. How to run (WSL gotchas)
- Bare WSL Ubuntu may lack venv/pip: `sudo apt install -y python3-venv python3-pip`.
- **Don't build the venv on `/mnt/c`** — flaky under WSL. Keep it in the Linux home.
- `.mcp.json` is git-ignored (holds a local path): copy `.mcp.json.example`, point `command` at
  `<venv>/bin/python`.
- `cp .env.example .env`, fill it in, then `--check` → `--login` → `scripts/smoke_live.py`.

## 7. Decisions / known issues
- **mcp pinned `<2`**: 2.x renames FastMCP → MCPServer; migration is isolated to `server.py`.
- **MSAL lazy init**: `Authenticator._app` is deferred to first token use. Keep it lazy.
- **Planner concurrency**: PATCH/DELETE need the current `@odata.etag`; `patch_with_etag` /
  `delete_with_etag` fetch it and retry once on 409/412.
- To Do attachments / linkedResources are not implemented.
- OneNote (and future Loop) are read-only by design.
- Tools write real data — only point live scripts at a test tenant/account.

## 8. Roadmap
- **Next: a more user-friendly version** (branch `user-friendly`): each company deploys it in its own
  tenant, per-person opt-in, ideally feeling like the M365 connector (pick → SSO → authorise).
  Options: company-hosted stateless Azure Function behind a Claude custom connector, or a Claude
  Desktop extension (`.mcpb`). Also: a groups claim as a sign-in gate that works without Entra ID P1.
