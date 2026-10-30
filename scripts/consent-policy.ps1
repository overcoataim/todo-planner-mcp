<#
.SYNOPSIS
  Lets non-admin users self-consent to todo-planner-mcp's permissions, and nothing else.

.DESCRIPTION
  Creates a custom app consent policy scoped to ONE client app ID and a fixed list of
  Microsoft Graph delegated permissions, then appends it to the default user role's
  assigned consent policies (existing entries are kept). See docs/consent-policy.md.

  UNTESTED: validate in a test tenant first.

  Requires: Microsoft.Graph.Identity.SignIns module; Privileged Role Administrator or
  Global Administrator.

.EXAMPLE
  ./consent-policy.ps1 -AppId 11111111-2222-3333-4444-555555555555
  ./consent-policy.ps1 -AppId 11111111-2222-3333-4444-555555555555 -IncludeLoop -IncludeMemberLookup
  ./consent-policy.ps1 -Remove
#>
[CmdletBinding(SupportsShouldProcess)]
param(
    [Parameter(ParameterSetName = 'Apply', Mandatory)] [guid] $AppId,
    [Parameter(ParameterSetName = 'Apply')] [switch] $IncludeLoop,          # Files.Read.All
    [Parameter(ParameterSetName = 'Apply')] [switch] $IncludeMemberLookup,  # Group.Read.All (may not be allowed; see doc)
    [Parameter(ParameterSetName = 'Remove', Mandatory)] [switch] $Remove,
    [string] $PolicyId = 'todo-planner-mcp'
)
$ErrorActionPreference = 'Stop'

$GraphAppId = '00000003-0000-0000-c000-000000000000'
$Permissions = [ordered]@{
    'openid'          = '37f7f235-527c-4136-accd-4a02d197296e'
    'profile'         = '14dad69e-099b-42c9-810b-d002981feec1'
    'offline_access'  = '7427e0e9-2fba-42fe-b0c0-848c9e6a8182'
    'User.Read'       = 'e1fe6dd8-ba31-4d61-89e7-88639da4683d'
    'Tasks.ReadWrite' = '2219042f-cab5-40cc-b0d2-16b1540b4c5f'
    'Notes.Read'      = '371361e4-b9e2-4a3f-8315-2a301a3b0a3d'
}
if ($IncludeLoop)         { $Permissions['Files.Read.All'] = 'df85f4d6-205c-4ac5-a5ea-6bf408dba283' }
if ($IncludeMemberLookup) { $Permissions['Group.Read.All'] = '5f8c59db-677d-491f-a6b8-5f174b11ec1d' }

$Assignment = "ManagePermissionGrantsForSelf.$PolicyId"

Connect-MgGraph -Scopes 'Policy.ReadWrite.PermissionGrant', 'Policy.ReadWrite.Authorization' -NoWelcome

$authz = Get-MgPolicyAuthorizationPolicy
$assigned = @($authz.DefaultUserRolePermissions.PermissionGrantPoliciesAssigned)
Write-Host "Currently assigned to default user role:`n  $($assigned -join "`n  ")"

if ($Remove) {
    $remaining = @($assigned | Where-Object { $_ -ne $Assignment })
    if ($remaining.Count -ne $assigned.Count -and $PSCmdlet.ShouldProcess('default user role', "unassign $Assignment")) {
        Update-MgPolicyAuthorizationPolicy -BodyParameter @{
            defaultUserRolePermissions = @{ permissionGrantPoliciesAssigned = $remaining }
        }
    }
    if ((Get-MgPolicyPermissionGrantPolicy -All | Where-Object Id -eq $PolicyId) -and
        $PSCmdlet.ShouldProcess($PolicyId, 'delete consent policy')) {
        Remove-MgPolicyPermissionGrantPolicy -PermissionGrantPolicyId $PolicyId
    }
    Write-Host "Removed. Existing user grants are NOT revoked (Enterprise apps -> app -> Permissions)."
    return
}

# (Re)create the policy so its include set always matches this script.
if (Get-MgPolicyPermissionGrantPolicy -All | Where-Object Id -eq $PolicyId) {
    if ($PSCmdlet.ShouldProcess($PolicyId, 'replace existing consent policy')) {
        Remove-MgPolicyPermissionGrantPolicy -PermissionGrantPolicyId $PolicyId
    }
}
if ($PSCmdlet.ShouldProcess($PolicyId, 'create consent policy')) {
    New-MgPolicyPermissionGrantPolicy -Id $PolicyId `
        -DisplayName 'todo-planner-mcp user consent' `
        -Description "Users may self-consent to todo-planner-mcp ($AppId) for: $($Permissions.Keys -join ', ')" | Out-Null

    New-MgPolicyPermissionGrantPolicyInclude -PermissionGrantPolicyId $PolicyId -BodyParameter @{
        permissionType       = 'delegated'
        resourceApplication  = $GraphAppId
        permissions          = @($Permissions.Values)
        clientApplicationIds = @("$AppId")
    } | Out-Null
}

if ($assigned -notcontains $Assignment -and $PSCmdlet.ShouldProcess('default user role', "assign $Assignment")) {
    Update-MgPolicyAuthorizationPolicy -BodyParameter @{
        defaultUserRolePermissions = @{ permissionGrantPoliciesAssigned = @($assigned + $Assignment) }
    }
}

$after = (Get-MgPolicyAuthorizationPolicy).DefaultUserRolePermissions.PermissionGrantPoliciesAssigned
Write-Host "Now assigned:`n  $($after -join "`n  ")"
Write-Host "Policy '$PolicyId' allows app $AppId -> $($Permissions.Keys -join ', ')"
