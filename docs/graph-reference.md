# Microsoft Graph reference (project-scoped)

Only what this project touches. Each claim is checked against Microsoft Learn (or PyPI/GitHub for
the MCP SDK) on **2026-10-07**. Base URL: `https://graph.microsoft.com/v1.0`.
Re-verify anything marked *unverified* during the first live run.

## 1. Permissions (delegated)

Two separate gates — see `docs/admin-consent.md` for what this means for adoption:
- **Static:** "AdminConsentRequired" on the permission itself (delegated column).
- **Default policy:** the Microsoft-managed user consent policy — *the default for new tenants* —
  blocks user self-consent to some permissions that are statically user-consentable.

| Permission | Static admin consent | Blocked by MS-managed default policy | Used for |
|---|---|---|---|
| `User.Read` | No | No | `GET /me` (`whoami`) |
| `Tasks.ReadWrite` | No | **Yes** | To Do CRUD; Planner task/plan/bucket CRUD |
| `Group.Read.All` | **Yes** | — | `groups/{id}/members` (plan member lookup) |
| `GroupMember.Read.All` | **Yes** | — | alternative for members |
| `Notes.Read` | No | No | OneNote read (user's notebooks) |
| `Notes.Read.All` | No | No | OneNote read of notebooks the user can access in the org |
| `Files.Read` | No | No | Loop components in user's own OneDrive |
| `Files.Read.All` | No | **Yes** | Loop files shared with the user / in SharePoint |
| `FileStorageContainer.Selected` | No | No | Loop *workspaces* (SharePoint Embedded) — see §5; also needs container-type permissions |

Sources: [permissions reference](https://learn.microsoft.com/en-us/graph/permissions-reference)
(raw: `microsoft-graph-docs-contrib/concepts/permissions-reference.md`; table columns are
Application | Delegated — an earlier version of this doc misread them),
[Microsoft-managed consent policy](https://learn.microsoft.com/en-us/entra/identity/enterprise-apps/manage-app-consent-policies#microsoft-recommended-user-consent-policy).
Planner is **work/school only** — every Planner endpoint lists personal accounts as "Not supported".

## 2. To Do (`/me/todo`)

| Op | Endpoint |
|---|---|
| Lists | `GET/POST /me/todo/lists`, `PATCH/DELETE /me/todo/lists/{id}` |
| Tasks | `GET/POST /me/todo/lists/{id}/tasks`, `GET/PATCH/DELETE .../tasks/{taskId}` |
| Checklist | `GET/POST .../tasks/{taskId}/checklistItems`, `PATCH/DELETE .../checklistItems/{itemId}` |

- Dates are `dateTimeTimeZone` objects: `{"dateTime": "...", "timeZone": "UTC"}`.
- `status`: `notStarted | inProgress | completed | waitingOnOthers | deferred`.
- No ETag/If-Match needed (unlike Planner).
- Not implemented here: attachments, linkedResources.

## 3. Planner (`/planner`)

| Op | Endpoint | Least-privileged perm |
|---|---|---|
| My tasks | `GET /me/planner/tasks` | Tasks.Read |
| **My plans** | `GET /me/planner/plans` | Tasks.Read |
| Group's plans | `GET /groups/{id}/planner/plans` | Tasks.Read |
| Plan / buckets / tasks | `GET /planner/plans/{id}`, `.../buckets`, `.../tasks` | Tasks.Read |
| Create task / bucket | `POST /planner/tasks`, `POST /planner/buckets` | Tasks.ReadWrite |
| Update / delete | `PATCH/DELETE /planner/tasks/{id}`, `.../details` | Tasks.ReadWrite |
| Plan members | `GET /groups/{groupId}/members` | GroupMember.ReadBasic.All* / Group.Read.All |

\* `GroupMember.ReadBasic.All` is listed on the API page but not (yet) in the permissions reference.

**Concurrency:** `PATCH`/`DELETE` require `If-Match: <@odata.etag>`. Common errors: 400, 403, 404, **409, 412**.

**Response:** `PATCH` returns **`204 No Content`** by default. Send `Prefer: return=representation`
to get `200` + the updated object.

**Assignments** (open type, keyed by user id):
```json
{ "<userId>": { "@odata.type": "#microsoft.graph.plannerAssignment", "orderHint": " !" },
  "<otherUserId>": null }
```
Object = assign; `null` = unassign.

**Checklist** (on `plannerTaskDetails`): open type keyed by a **GUID** the client generates;
value is a `plannerChecklistItem` (`title`, `isChecked`); `null` removes it.

**Priority:** 0–10. Planner UI writes 1 = urgent, 3 = important, 5 = medium, 9 = low
(ranges: 0–1 / 2–4 / 5–7 / 8–10).

**Dates:** `dueDateTime` / `startDateTime` are UTC ISO 8601, e.g. `2026-10-07T00:00:00Z`.

Sources: [update plannerTask](https://learn.microsoft.com/en-us/graph/api/plannertask-update?view=graph-rest-1.0),
[list my plans](https://learn.microsoft.com/en-us/graph/api/planneruser-list-plans?view=graph-rest-1.0),
[group plans](https://learn.microsoft.com/en-us/graph/api/plannergroup-list-plans?view=graph-rest-1.0),
[plannerAssignments](https://learn.microsoft.com/en-us/graph/api/resources/plannerassignments?view=graph-rest-1.0),
[plannerChecklistItems](https://learn.microsoft.com/en-us/graph/api/resources/plannerchecklistitems?view=graph-rest-1.0).

## 4. OneNote (read-only — `onenote_tools.py`)

| Op | Endpoint |
|---|---|
| Notebooks | `GET /me/onenote/notebooks` |
| Sections | `GET /me/onenote/notebooks/{id}/sections` (or `/me/onenote/sections`) |
| Pages | `GET /me/onenote/sections/{id}/pages` (or `/me/onenote/pages`) |
| Page metadata | `GET /me/onenote/pages/{id}` |
| **Page content** | `GET /me/onenote/pages/{id}/content` → **HTML** (convert to text) |
| Group / site notebooks | `/groups/{id}/onenote/...`, `/sites/{id}/onenote/...` |

- `/me/onenote` covers notebooks the user **owns and that are shared with them**.
- Page list default: top 20 by `lastModifiedTime desc`, `$top` max **100**, page via `@odata.nextLink`.
- **No app-only auth** for the OneNote API — delegated only (fine for us).
- Works with personal accounts (`Notes.Read`).

Sources: [OneNote overview](https://learn.microsoft.com/en-us/graph/api/resources/onenote-api-overview?view=graph-rest-1.0),
[list pages](https://learn.microsoft.com/en-us/graph/api/onenote-list-pages?view=graph-rest-1.0),
[get page](https://learn.microsoft.com/en-us/graph/api/page-get?view=graph-rest-1.0).

## 5. Loop (read-only, needs a spike)

There is **no Loop API** in Graph (Microsoft Q&A, Feb 2026: "no Loop API or Graph endpoints currently exist").
What *does* exist:

- **Loop components** (shared in Teams/Outlook) are `.loop` files in the creator's **OneDrive**.
  Find them via drive search, then read with
  `GET /drives/{driveId}/items/{itemId}/content?format=html` — `html` conversion officially supports
  `loop`, `fluid`, `wbtx`, `whiteboard`. Response is a **302** to a short-lived pre-authenticated URL
  (follow redirects, don't forward the bearer token). Perms: `Files.Read` (own) / `Files.Read.All` (shared).
- **Loop workspace pages** live in **SharePoint Embedded** containers. Microsoft's docs say SPE needs
  `FileStorageContainer.Selected` **plus container-type permissions** for the app. A July 2026
  third-party write-up reports SPE rejects delegated tokens for content download (app-only only) —
  *unverified*; if true, workspaces are out of scope for a delegated-only design.

Sources: [convert to other formats](https://learn.microsoft.com/en-us/graph/api/driveitem-get-content-format?view=graph-rest-1.0),
[Q&A: Loop API](https://learn.microsoft.com/en-us/answers/questions/5760312/can-ms-provide-a-microsoft-loop-api-for-programmat),
[dev.to: read-only Loop access](https://dev.to/denizv/giving-an-ai-assistant-read-only-access-to-microsoft-loop-without-breaking-permissions-3adc).

## 6. Directory lookups

- `GET /me/memberOf` — least privileged **`User.Read`**; higher: `GroupMember.Read.All`, `Directory.Read.All`.
  `Group.Read.All` is **not** listed. Without a group-read permission, group entries come back with only `id`/`@odata.type`.
- OData cast (`/me/memberOf/microsoft.graph.group`) is documented as needing `ConsistencyLevel: eventual` + `$count=true`.
- `GET /groups/{id}/members` — default page 100, max 999.

Sources: [user memberOf](https://learn.microsoft.com/en-us/graph/api/user-list-memberof?view=graph-rest-1.0),
[group members](https://learn.microsoft.com/en-us/graph/api/group-list-members?view=graph-rest-1.0).

## 7. Auth (Entra)

- Secret mode: **Web** platform redirect URI. `http://` is allowed **only for localhost**.
  For localhost the **port is ignored** when matching, so `http://localhost/callback` matches any port.
  Microsoft recommends `127.0.0.1` over `localhost`, but the portal won't accept `http://127.0.0.1` (manifest only).
- Device-code mode needs no redirect URI; app reg needs **Allow public client flows = Yes**.
- Redirect URIs are case-sensitive.

Source: [redirect URI rules](https://learn.microsoft.com/en-us/entra/identity-platform/reply-url).

## 8. Throttling

No published per-service limits for Planner, To Do or OneNote; OneDrive/SharePoint follow SharePoint's
throttling rules. Handle **429** (and 503) by honouring `Retry-After`, else exponential backoff.

Source: [throttling limits](https://learn.microsoft.com/en-us/graph/throttling-limits).

## 9. MCP Python SDK

- Installed: `mcp 1.30.0` (latest 1.x). Latest overall: **2.3.0** (2026-10-02); 2.0 GA'd Aug 2026.
- 2.x renames `FastMCP` → `MCPServer`, imported as `from mcp.server import MCPServer` (per SDK README).
- We pin `mcp<2`; migration is isolated to `server.py`.

## 10. Audit of the original (AI-written) research — 2026-10-07

| Claim in code / CLAUDE.md | Verdict |
|---|---|
| `Tasks.ReadWrite` covers To Do + Planner | ✅ Correct |
| Planner needs work/school account | ✅ Correct |
| `Group.Read.All` needs admin consent | ✅ Correct |
| (this doc, first version) `Files.Read.All` / `Notes.Read.All` need admin consent | ❌ Misread table — statically user-consentable; `Files.Read.All` is blocked by the MS-managed default policy though |
| `Tasks.ReadWrite` is user-consentable | ⚠️ Statically yes, but **blocked by the MS-managed default policy** (default for new tenants) |
| Planner PATCH/DELETE need If-Match, retry on 409/412 | ✅ Correct |
| Assign via object, unassign via `null` | ✅ Correct |
| Priority mapping 1/3/5/9 | ✅ Correct |
| `Group.Read.All` is needed for plan discovery | ⚠️ **Not needed** — **fixed:** now uses `GET /me/planner/plans`. Still needed for member lookup. Verify live that it returns all expected plans. |
| `/me/memberOf` works with `Group.Read.All` | ❌ Not a listed permission — **fixed:** memberOf no longer used |
| Scopes `Tasks.ReadWrite Group.Read.All` are sufficient | ❌ `User.Read` missing — **fixed:** always requested |
| Planner update tools return the updated task | ❌ Returned `{}` — **fixed:** `patch_with_etag` sends `Prefer: return=representation` |
| Checklist key = `uuid4().hex` | ⚠️ Docs say GUID — **fixed:** `str(uuid4())` |
| memberOf cast without ConsistencyLevel | n/a — memberOf no longer used |
| 429 throttling handled | ❌ Not handled in `graph.py` |
| mcp 2.x import is `mcp.server.mcpserver.MCPServer` | ⚠️ README shows `from mcp.server import MCPServer` |
