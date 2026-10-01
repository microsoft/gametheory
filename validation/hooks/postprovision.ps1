#requires -Version 7.2
<#
.SYNOPSIS
Finishes a validation environment after `azd provision`; safe to rerun.

.DESCRIPTION
Runs the setup job (migrations, table-scoped runtime grants, and the first administrator for
every database), registers each app's origin with the validation SPA, and prints next steps.
See docs/live-acceptance.md.
#>
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
Import-Module (Join-Path $PSScriptRoot '..' '..' 'scripts' 'acceptance' 'AcceptanceCommon.psm1') -Force

function Get-EnvValue([string] $Name) { [Environment]::GetEnvironmentVariable($Name) ?? '' }

$group = Get-EnvValue 'AZURE_RESOURCE_GROUP'
$setupJob = Get-EnvValue 'SETUP_JOB_NAME'
if (-not ($group -and $setupJob)) { throw 'Provisioning outputs are missing; run azd provision again.' }

# A new job can start before its registry role assignment propagates, so retry briefly.
$status = 'NotStarted'
foreach ($attempt in 1..3) {
    Write-Host "Preparing databases (attempt $attempt)"
    $execution = Start-ContainerAppJob -ResourceGroup $group -Name $setupJob
    $status = Wait-ContainerAppJob -ResourceGroup $group -Name $setupJob -Execution $execution -TimeoutMinutes 30
    if ($status -eq 'Succeeded') { break }
    Write-Warning "Setup execution $execution ended $status"
    Start-Sleep -Seconds 60
}
if ($status -ne 'Succeeded') {
    throw "Database setup failed. Query ContainerAppConsoleLogs_CL where ContainerJobName_s == '$setupJob'."
}

$urls = @(
    'BASELINE_URL', 'SQL_OUTAGE_URL', 'BLOB_OUTAGE_URL', 'SCHEDULER_OUTAGE_URL', 'MODEL_DENIED_URL'
) | ForEach-Object { Get-EnvValue $_ } | Where-Object { $_ }
if ((Get-EnvValue 'VALIDATION_REGISTRATIONS_MANAGED') -eq 'true') {
    # The studio signs in with its own origin as the redirect URI.
    $spaObjectId = Get-AzText ad app show --id (Get-EnvValue 'VALIDATION_SPA_APP_ID') --query id
    Invoke-GraphRequest -Method PATCH -Path "applications/$spaObjectId" -Body @{ spa = @{ redirectUris = @($urls) } } |
        Out-Null
}

Write-Host ''
Write-Host 'Validation environment ready:'
foreach ($url in $urls) { Write-Host "  $url" }
Write-Host 'Run the fault scenarios with: pwsh ./scripts/Invoke-ValidationScenario.ps1'
