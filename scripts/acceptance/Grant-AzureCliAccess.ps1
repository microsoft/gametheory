#requires -Version 7.2
<#
.SYNOPSIS
Preauthorizes Azure CLI for a studio API registration, or removes that preauthorization.

.DESCRIPTION
Afterward, `az account get-access-token --scope api://<app>/access_as_user` returns a token for
the signed-in user without a consent prompt, so the live tests can sign in from a terminal.
Workspace and administrator authorization in the application still apply. Changing the
registration needs an Entra role that can update it. See docs/live-acceptance.md.

.EXAMPLE
pwsh ./scripts/acceptance/Grant-AzureCliAccess.ps1 -ApiAppId <api-application-id>
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string] $ApiAppId,
    [switch] $Remove
)
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSScriptRoot 'AcceptanceCommon.psm1') -Force

Grant-AzureCliPreAuthorization -ApiAppId $ApiAppId -Remove:$Remove
if ($Remove) {
    Write-Host "Azure CLI is no longer preauthorized for $ApiAppId."
}
else {
    Write-Host "Azure CLI can now request api://$ApiAppId/access_as_user tokens for the signed-in user."
}
