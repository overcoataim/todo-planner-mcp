# Admin consent: what we can offer without it

Checked against Microsoft Learn on 2026-10-07. Admin consent is the main adoption blocker for this
connector, so it needs watching as features are added.

## TL;DR

- **The core feature, tasks (To Do and Planner), needs admin consent in most tenants.**
  `Tasks.ReadWrite` doesn't require admin consent by definition. But the **Microsoft-managed
  default consent policy**, which is the default for new tenants, blocks users from consenting to it
  themselves. The same goes for `Files.Read.All`.
- **Without admin consent, in a default-policy tenant, we can offer:** sign-in, **OneNote read**,
  and **Loop components in the user's own OneDrive**. That's about all.
- **With one admin consent** (one-time, tenant-wide): everything we've built.
- Admin consent only gives the app *permission to act as the user*. It never sees more than the
  signed-in user can already see. That's the key point when asking an admin for approval.

## Two separate gates

1. **Static requirement.** Each Graph permission says whether it needs admin consent. Among ours,
   only `Group.Read.All` and `GroupMember.Read.All` do.
2. **Tenant consent policy.** The tenant decides which permissions users may grant themselves.
   Microsoft documents three main settings:

| Tenant setting | What users can self-consent to |
|---|---|
| **Let Microsoft manage** (`microsoft-user-default-recommended`) — **default for new tenants** | Any user-consentable permission *except* a block list that includes **`Tasks.Read`, `Tasks.ReadWrite`, `Tasks.*.Shared`, `Files.Read.All`, `Sites.Read.All`**, Mail.\*, Calendars.\*, Chat.\*, Contacts.ReadWrite, People.Read. Microsoft updates this list over time. |
| Verified publishers, selected permissions (`microsoft-user-default-low`) | Only permissions the admin classed as "low impact", and only for apps from **verified publishers** or registered in the same tenant |
| Allow user consent for apps (`microsoft-user-default-legacy`) | Anything without static admin consent, from any app |
| User consent disabled | Nothing. Every app needs an admin. Common in enterprises. |

The block list has grown over time: files and sites in Aug 2025, mail, calendar and chat in
Nov 2025, contacts, people and tasks by June 2026. **Expect it to grow further.** `Notes.*` and
`Files.Read` aren't on it today, but there's no guarantee they won't be added.

## Feature tiers

| Tier | Scopes | Tools | Works without an admin? |
|---|---|---|---|
| **0: Basic** | `User.Read` | `whoami` | Yes, unless user consent is disabled |
| **1: Notes** | + `Notes.Read` (or `Notes.Read.All` for org-shared notebooks) | 5 × `onenote_*` (read-only) | Yes under the default policy |
| **1b: Loop (own)** | + `Files.Read` | Loop components the user created *(not built)* | Yes under the default policy |
| **2: Tasks** | + `Tasks.ReadWrite` | 15 × `todo_*`, most `planner_*` | **No** under the default policy; yes under "legacy" |
| **3: Team** | + `Group.Read.All` | `planner_list_plan_members`, `planner_find_member` (needed to *assign* to others) | **No.** Always needs an admin. |
| **3b: Loop (shared)** | + `Files.Read.All` | Loop components shared with the user *(not built)* | **No** under the default policy |
| **Loop workspaces** | SharePoint Embedded | Out of scope: needs container-type permissions, possibly app-only | No |

Trimming Tier 3: Planner tasks can be assigned to anyone whose user ID we already know, for example
from existing task assignments. Only *looking up* group members needs `Group.Read.All`. A
"type an email address, then look up that user" flow would need `User.ReadBasic.All`, which
doesn't require admin consent statically. *Unverified whether the default policy allows it;
check before relying on it.*

## "Selectable features" in a Claude connector

- **Tools can be switched off in Claude.** Users can disable individual connector tools in the
  "Search and tools" menu, and in the connector's settings.
- **Permissions can't be chosen that way.** Sign-in asks for every scope the server requests.
  **If any one of them needs admin approval, sign-in fails for every non-admin user,** including the
  tiers that wouldn't have needed it. That's the real adoption trap.

So the server has to decide which scopes to ask for. Options, simplest first:

1. **One connector per tier.** For example, "M365 Notes" (no admin needed) and "M365 Tasks"
   (admin needed). Users add the ones their tenant allows. Simple, and each sign-in is all-or-nothing.
2. **Per-tenant configuration (hosted version).** The tenant admin, or whoever onboards the tenant,
   picks the tiers. The server only requests those scopes and only registers matching tools.
3. **Incremental consent.** Sign in with Tier 0–1 scopes, then ask for more when a higher-tier tool
   is first used. Entra supports this, but in a remote MCP connector it needs a second browser
   sign-in mid-conversation. *Feasibility in Claude's connector flow is unverified.*

Whichever we choose, **only register tools whose scopes appear in the token's `scp` claim.**
Tools then never fail with a confusing 403. They simply aren't there.

## Making admin consent easier to get

- **Scoped user-consent policy:** design and script in `docs/consent-policy.md` /
  `scripts/consent-policy.ps1`. The admin runs it once, then users self-consent to *this app's*
  scopes only, `Tasks.ReadWrite` included. Untested.

- **One-time and tenant-wide.** An admin clicks "Grant admin consent" once, or uses the
  `/adminconsent` URL.
- **Admin consent workflow.** Tenants can let users *request* approval from inside the consent
  prompt instead of hitting a dead end. Worth recommending to adopting tenants.
- **Publisher verification (hosted, multi-tenant version).** For multi-tenant apps registered
  after Nov 2020, users in other tenants generally can't consent if the publisher is unverified.
  The `microsoft-user-default-low` policy also requires a verified publisher. Verification needs a
  Microsoft AI Cloud Partner Program account and a custom domain (not `*.onmicrosoft.com`), and is
  free. **This must be decided before the hosted build.**
- **Pitch to admins:** delegated-only (never app-only), so the app can't see anything the user
  can't. Notes and Loop are read-only. Tasks are read and write, limited to the user's own and
  shared tasks.

## Keeping an eye on it

- **Test as a non-admin user.** In the Business Basic test tenant, create a normal user and sign in
  as them. That shows what a real user sees. A tenant admin's own sign-in hides the problem.
- Check the test tenant's setting: Entra admin center → Enterprise apps → Consent and permissions →
  User consent settings.
- Re-check Microsoft's block list (link below) before each release. It changes without notice to us.
- Whenever a feature adds a scope, record its tier in this doc and in `docs/graph-reference.md` §1.

## Sources

- [Microsoft-managed (recommended) user consent policy and block list](https://learn.microsoft.com/en-us/entra/identity/enterprise-apps/manage-app-consent-policies#microsoft-recommended-user-consent-policy)
- [Configure user consent settings](https://learn.microsoft.com/en-us/entra/identity/enterprise-apps/configure-user-consent)
- [Publisher verification](https://learn.microsoft.com/en-us/entra/identity-platform/publisher-verification-overview)
- [Graph permissions reference](https://learn.microsoft.com/en-us/graph/permissions-reference)
- [Claude custom connectors](https://support.claude.com/en/articles/11175166-about-custom-connectors-remote-mcp)
- Timeline of block-list additions (third-party summaries of Microsoft message center posts):
  [Aug 2025](https://office365itpros.com/2025/06/19/app-consent-policy-user-app-consent/),
  [Nov 2025](https://blog-en.topedia.com/2025/11/microsoft-managed-default-app-consent-policy-now-blocks-20-additional-permissions/),
  [June 2026](https://blog-en.topedia.com/?p=67179)
