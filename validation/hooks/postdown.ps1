#requires -Version 7.2
<#
.SYNOPSIS
Deletes the Entra registrations that preprovision created, after `azd down` removed the resources.
#>
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$environmentName = [Environment]::GetEnvironmentVariable('AZURE_ENV_NAME')
if ([Environment]::GetEnvironmentVariable('VALIDATION_REGISTRATIONS_MANAGED') -ne 'true') {
    Write-Host 'No hook-created registrations to delete'
    return
}
foreach ($name in 'VALIDATION_SPA_APP_ID', 'VALIDATION_API_APP_ID') {
    $appId = [Environment]::GetEnvironmentVariable($name)
    if ($appId) {
        & az ad app delete --id $appId --only-show-errors
        if ($LASTEXITCODE -ne 0) { Write-Warning "Could not delete registration $appId; delete it manually." }
    }
    azd env set $name '' --environment $environmentName | Out-Null
}
azd env set VALIDATION_REGISTRATIONS_MANAGED '' --environment $environmentName | Out-Null
Write-Host 'Deleted the validation registrations; they stay recoverable in Entra for 30 days.'
