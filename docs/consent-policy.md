# Consent policy design: letting users consent without per-app admin consent

Status: **design, untested.** Validate it in the Business Basic test tenant (see "Test plan").
Background: `docs/admin-consent.md`.

## The honest constraint

The app itself can't get around tenant consent rules. **A tenant admin must act once** in every
option below. The aim is to make that one action as small and as easy to approve as possible.

| Option | What the admin does once | What users do | Admin risk surface |
|---|---|---|---|
| **A. Tenant-wide admin consent** | Clicks "Grant admin consent" for the app | Nothing; it just works | Grant covers all users (each still limited to their own data) |
| **A+. Admin consent + assignment required** | As A, and sets "Assignment required = Yes" and assigns a group | Nothing, if they're in the group | Limited to a pilot group |
| **B. Scoped user-consent policy** *(this doc)* | Runs a script: a policy that lets users self-consent to **this app's** listed permissions only | Click "Accept" at their own first sign-in | Nothing is granted until each user opts in, and only this app is covered |
| **C. Admin consent workflow** | Turns on the workflow, then approves requests as they arrive | "Request approval" on the consent screen | As A, once approved |

**Recommendation:** offer **A+** as the default ask, since it's the most familiar to admins and
uses the portal only. Offer **B** to tenants whose policy is "no tenant-wide grants for
third-party apps".

## Option B: how it works

Entra checks user consent against the **app consent policies** assigned to the default user role.
New tenants have the Microsoft-managed `microsoft-user-default-recommended` policy, which blocks
`Tasks.ReadWrite` and `Files.Read.All`. A user may consent if **any one** assigned policy allows it.
So we **add** a narrow custom policy next to Microsoft's, without weakening it:

- **Include:** delegated Microsoft Graph permissions, **only these permission IDs**, **only for
  our client app ID**.
- Assign it as `managePermissionGrantsForSelf.todo-planner-mcp` on the default user role,
  **keeping the existing entries**.
- Effect: users can self-consent to exactly our scopes, for exactly our app. Everything else
  stays as the Microsoft-managed policy decides.

### Permissions in the policy

| Scope | Delegated permission ID | Why |
|---|---|---|
| `openid` | 37f7f235-527c-4136-accd-4a02d197296e | MSAL always requests it |
| `profile` | 14dad69e-099b-42c9-810b-d002981feec1 | MSAL always requests it |
| `offline_access` | 7427e0e9-2fba-42fe-b0c0-848c9e6a8182 | refresh tokens |
| `User.Read` | e1fe6dd8-ba31-4d61-89e7-88639da4683d | `whoami` |
| `Tasks.ReadWrite` | 2219042f-cab5-40cc-b0d2-16b1540b4c5f | To Do + Planner. **The one that's blocked by default.** |
| `Notes.Read` | 371361e4-b9e2-4a3f-8315-2a301a3b0a3d | OneNote |
| `Files.Read.All` *(optional, for Loop)* | df85f4d6-205c-4ac5-a5ea-6bf408dba283 | blocked by default |
| `Group.Read.All` *(optional)* | 5f8c59db-677d-491f-a6b8-5f174b11ec1d | plan member lookup. **Statically admin-only; see open questions.** |

Microsoft Graph resource app ID: `00000003-0000-0000-c000-000000000000`.
IDs taken from the Graph permissions reference on 2026-10-07.

### Applying it

Use `scripts/consent-policy.ps1` (Microsoft Graph PowerShell). It needs a **Privileged Role
Administrator** or Global Administrator, plus the scopes `Policy.ReadWrite.PermissionGrant` and
`Policy.ReadWrite.Authorization`.

```powershell
./scripts/consent-policy.ps1 -AppId <client-id>                 # apply
./scripts/consent-policy.ps1 -AppId <client-id> -IncludeLoop    # + Files.Read.All
./scripts/consent-policy.ps1 -Remove                            # roll back
```

The Entra admin center can't build this kind of policy; it's PowerShell or Graph only.

## Gotchas

- **Don't combine B with "Assignment required = Yes".** Microsoft: apps that require user
  assignment *must* have admin consent, even if a user consent policy would allow it. So you choose
  B *or* A+, not both. To limit B to a pilot group, you'd need a custom role carrying
  `managePermissionGrantsForSelf.{policy}`, and custom roles need **Entra ID P1**. Business Basic
  doesn't include P1; Business Premium does.
- **Keep the existing assigned policies** when updating `authorizationPolicy`. Overwriting the list
  can switch off user consent or developer consent. The script appends instead of replacing.
- **The client app ID is the boundary.** For a hosted multi-tenant app, it's the same ID in every
  tenant, which is fine. Publisher verification still matters for the consent screen and for
  tenants using `microsoft-user-default-low` (see `admin-consent.md`).
- Existing grants survive policy changes. Removing the policy stops *new* consents only. Revoke
  existing ones under Enterprise apps → the app → Permissions.

## Open questions (resolve in the test tenant)

1. **Can a user-consent policy cover a statically admin-only permission (`Group.Read.All`)?**
   Microsoft's docs don't say either way. If it can't, member lookup stays an admin-consent extra.
   The fallback is `User.ReadBasic.All` (not admin-only statically) for "look up a user by email".
2. **Does a consent request need one policy to cover *all* requested scopes, or can different
   policies cover different scopes?** The docs say a user "only needs one policy to approve" per
   consent event. That's why the policy includes `openid`, `profile`, `offline_access` and
   `User.Read` too, so that it covers the whole request on its own.
3. **Is assigning a custom policy to the *default user role* free, or does it need P1?** The P1
   note on Microsoft's page is about custom *roles*. The `authorizationPolicy` route isn't stated.

## Test plan (Business Basic tenant)

1. As admin, register the app **without** granting admin consent.
2. Sign in as the **non-admin** test user and confirm it's blocked ("Need admin approval"). That
   proves the default policy is active.
3. Run `consent-policy.ps1 -AppId ...`.
4. As the non-admin user, sign in again. Expected: a normal consent screen and successful sign-in.
5. Repeat with `Group.Read.All` added, to answer open question 1.
6. Run `-Remove`, confirm a *new* user is blocked again and the existing user still works.

## Sources

- [Manage app consent policies](https://learn.microsoft.com/en-us/entra/identity/enterprise-apps/manage-app-consent-policies)
- [Configure user consent](https://learn.microsoft.com/en-us/entra/identity/enterprise-apps/configure-user-consent) (assignment-required note, authorizationPolicy PATCH)
- [permissionGrantConditionSet](https://learn.microsoft.com/en-us/graph/api/resources/permissiongrantconditionset?view=graph-rest-1.0) (`clientApplicationIds`, `permissions`)
- [App consent permissions for custom roles](https://learn.microsoft.com/en-us/entra/identity/role-based-access-control/custom-consent-permissions) (P1 requirement)
