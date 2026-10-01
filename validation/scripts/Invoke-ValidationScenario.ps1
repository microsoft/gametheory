#requires -Version 7.2
<#
.SYNOPSIS
Runs the validation environment's fault scenarios and summarizes the evidence.

.DESCRIPTION
Reads the azd environment created by `azd up`, signs the tests in with Azure CLI (the validation
API preauthorizes it), runs each scenario, restores every fault the runner changes, and writes
evidence under .acceptance/validation/. See docs/live-acceptance.md.

.EXAMPLE
pwsh ./validation/scripts/Invoke-ValidationScenario.ps1

.EXAMPLE
./validation/scripts/Invoke-ValidationScenario.ps1 -Scenario sql-outage, blob-outage

Pass several scenarios from a PowerShell prompt; `pwsh -File` does not parse lists.
#>
[CmdletBinding()]
param(
    [ValidateSet('all', 'baseline', 'probes', 'worker-restart', 'sql-outage', 'blob-outage', 'scheduler-outage', 'model-denied')]
    [string[]] $Scenario = @('all'),
    [string] $Environment
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
Import-Module (Join-Path $PSScriptRoot '..' '..' 'scripts' 'acceptance' 'AcceptanceCommon.psm1') -Force

$arguments = @('env', 'get-values', '--output', 'json', '--cwd', (Join-Path $PSScriptRoot '..'))
if ($Environment) { $arguments += @('--environment', $Environment) }
$values = (& azd @arguments | Out-String) | ConvertFrom-Json -AsHashtable
if ($LASTEXITCODE -ne 0 -or -not $values) { throw 'Cannot read the azd environment; run azd up first.' }

function Get-Value([string] $Name) { if ($values.ContainsKey($Name)) { [string] $values[$Name] } else { '' } }
function Test-Selected([string] $Name) { ($Scenario -contains 'all') -or ($Scenario -contains $Name) }
function Get-ScenarioToken { Get-AccessToken -Scope (Get-Value 'VALIDATION_API_SCOPE') }

$group = Get-Value 'AZURE_RESOURCE_GROUP'
$workspace = Get-Value 'LOG_ANALYTICS_WORKSPACE_ID'
if (Get-Value 'AZURE_SUBSCRIPTION_ID') { Invoke-Az account set --subscription (Get-Value 'AZURE_SUBSCRIPTION_ID') | Out-Null }
$evidence = New-EvidenceDirectory (Join-Path 'validation' (Get-Value 'AZURE_ENV_NAME'))
$since = "datetime($((Get-Date).ToUniversalTime().AddMinutes(-2).ToString('o')))"
$results = [Collections.Generic.List[object]]::new()

function Invoke-ScenarioTest([string] $Label, [string] $Test, [hashtable] $Variables) {
    $settings = @{ GT_SCENARIO_TOKEN = (Get-ScenarioToken); GT_SCENARIO_ARTIFACTS = $evidence } + $Variables
    Invoke-AcceptancePytest -Arguments @('tests/test_live_scenarios.py', '-k', $Test) -Environment $settings `
        -JUnitPath (Join-Path $evidence "$Label.xml")
}

function Read-Artifact([string] $Name) {
    $path = Join-Path $evidence "$Name.json"
    if (Test-Path $path) { Get-Content -Raw $path | ConvertFrom-Json } else { $null }
}

function Add-LogCheck([string] $Check, [string] $Description, [string] $Query, [switch] $ExpectNone) {
    if ($ExpectNone) {
        $count = Get-LogCount -WorkspaceResourceId $workspace -Query $Query
        $outcome = if ($count -eq 0) { 'Passed' } else { 'Failed' }
    }
    else {
        $count = Wait-LogCount -WorkspaceResourceId $workspace -Query $Query
        $outcome = if ($count -gt 0) { 'Passed' } else { 'Failed' }
    }
    $results.Add((New-CheckResult $Check $Description $outcome "$count matching log lines"))
}

# Long-running jobs start first and are collected at the end.
$jobs = [ordered]@{}
if ((Test-Selected 'worker-restart') -and (Get-Value 'RESTART_JOB_NAME')) {
    $jobs['F1'] = @{ Name = Get-Value 'RESTART_JOB_NAME'; Description = 'Worker killed mid-activity resumes and persists one result' }
}
if (Test-Selected 'probes') {
    $probes = Get-Value 'PROBE_JOB_NAMES'
    foreach ($probe in @(if ($probes) { $probes | ConvertFrom-Json })) {
        $jobs["Probe $probe"] = @{ Name = $probe; Description = 'Private DNS, Foundry, Scheduler, and Blob from inside the network' }
    }
}
foreach ($key in $jobs.Keys) {
    $jobs[$key].Execution = Start-ContainerAppJob -ResourceGroup $group -Name $jobs[$key].Name
    Write-Host "Started $($jobs[$key].Name) ($($jobs[$key].Execution))"
}

if (Test-Selected 'baseline') {
    $url = Get-Value 'BASELINE_URL'
    Wait-HttpOk "$url/api/health"
    $token = Get-ScenarioToken
    $selection = 'test_persisted_authoring_and_reviewed_real_planning or test_live_rejects_missing_and_malformed_tokens'
    $cases = Invoke-AcceptancePytest -Arguments @('tests/test_live_api.py', '-k', $selection) `
        -JUnitPath (Join-Path $evidence 'baseline.xml') -Environment @{
        GT_LIVE_API_URL           = $url
        GT_LIVE_API_TOKEN         = $token
        GT_LIVE_API_EXPECTED_USER = (Get-TokenClaim $token).oid
        GT_LIVE_API_ARTIFACT      = (Join-Path $evidence 'baseline.json')
    }
    $results.Add((Merge-TestOutcome $cases 'F0' 'Baseline studio: authoring, planning, and an applied proposal'))
}

if ((Test-Selected 'sql-outage') -and (Get-Value 'SQL_OUTAGE_URL')) {
    $url = Get-Value 'SQL_OUTAGE_URL'
    Wait-HttpOk "$url/api/health"
    $cases = Invoke-ScenarioTest 'sql-outage' 'test_sql_outage_returns_503_with_request_id' @{ GT_SCENARIO_SQL_OUTAGE_URL = $url }
    $results.Add((Merge-TestOutcome $cases 'F2' 'SQL outage: 503 with a request ID; health and config still respond'))
    $artifact = Read-Artifact 'sql-outage'
    if ($artifact) {
        $match = ($artifact.request_ids | ForEach-Object { "Log_s contains $(Format-KqlString $_)" }) -join ' or '
        Add-LogCheck 'F2 log' 'The API failure log carries the same request ID, without SQL text' `
            "ContainerAppConsoleLogs_CL | where TimeGenerated > $since | where ContainerAppName_s == 'fault-sql-api' | where ($match) and Log_s contains 'SQL operation failed' | count"
    }
}

if ((Test-Selected 'blob-outage') -and (Get-Value 'BLOB_OUTAGE_URL')) {
    $url = Get-Value 'BLOB_OUTAGE_URL'
    $app = Get-Value 'BLOB_OUTAGE_APP'
    $variables = @{ GT_SCENARIO_BLOB_OUTAGE_URL = $url }
    Wait-HttpOk "$url/api/health"
    $cases = @(Invoke-ScenarioTest 'blob-outage' 'test_blob_outage_rejects_uploads' $variables)
    try {
        Write-Host 'Pointing the Blob scenario at real storage to seed one asset'
        Set-ContainerAppSetting -ResourceGroup $group -Name $app -Settings @{ GT_BLOB_URL = Get-Value 'BLOB_HEALTHY_URL' }
        Start-Sleep -Seconds 30
        Wait-HttpOk "$url/api/health"
        $cases += Invoke-ScenarioTest 'blob-seed' 'test_blob_seed_asset_while_healthy' ($variables + @{ GT_SCENARIO_PHASE = 'blob-healthy' })
    }
    finally {
        Write-Host 'Restoring the Blob fault'
        Set-ContainerAppSetting -ResourceGroup $group -Name $app -Settings @{ GT_BLOB_URL = Get-Value 'BLOB_FAULT_URL' }
    }
    Start-Sleep -Seconds 30
    Wait-HttpOk "$url/api/health"
    $cases += Invoke-ScenarioTest 'blob-read' 'test_blob_outage_fails_reads' ($variables + @{ GT_SCENARIO_PHASE = 'blob-read' })
    $results.Add((Merge-TestOutcome $cases 'F3' 'Blob outage: uploads and reads fail closed with 503'))
}

if ((Test-Selected 'scheduler-outage') -and (Get-Value 'SCHEDULER_OUTAGE_URL')) {
    $url = Get-Value 'SCHEDULER_OUTAGE_URL'
    $worker = Get-Value 'SCHEDULER_OUTAGE_WORKER'
    $variables = @{ GT_SCENARIO_SCHEDULER_OUTAGE_URL = $url }
    Wait-HttpOk "$url/api/health"
    $cases = @(Invoke-ScenarioTest 'scheduler-outage' 'test_scheduler_outage_keeps_request_queued' $variables)
    try {
        Write-Host 'Pointing the Scheduler scenario worker at the real Scheduler'
        Set-ContainerAppSetting -ResourceGroup $group -Name $worker -RequireRunningReplica `
            -Settings @{ GT_SCHEDULER_ENDPOINT = Get-Value 'SCHEDULER_HEALTHY_ENDPOINT' }
        $cases += Invoke-ScenarioTest 'scheduler-recovery' 'test_scheduler_recovery_completes_queued_request' `
        ($variables + @{ GT_SCENARIO_PHASE = 'scheduler-recovery' })
    }
    finally {
        Write-Host 'Restoring the Scheduler fault'
        Set-ContainerAppSetting -ResourceGroup $group -Name $worker -RequireRunningReplica `
            -Settings @{ GT_SCHEDULER_ENDPOINT = Get-Value 'SCHEDULER_FAULT_ENDPOINT' }
    }
    $results.Add((Merge-TestOutcome $cases 'F4' 'Scheduler outage: the request stays visibly queued, then completes once'))
    $artifact = Read-Artifact 'scheduler-outage'
    if ($artifact) {
        Add-LogCheck 'F4 log' 'Worker dispatch failures are logged with the planning request ID' `
            "ContainerAppConsoleLogs_CL | where TimeGenerated > $since | where ContainerAppName_s == '$worker' | where Log_s contains $(Format-KqlString $artifact.request_id) and Log_s contains 'Scheduler dispatch failed' | count"
    }
}

if ((Test-Selected 'model-denied') -and (Get-Value 'MODEL_DENIED_URL')) {
    $url = Get-Value 'MODEL_DENIED_URL'
    $worker = Get-Value 'MODEL_DENIED_WORKER'
    Wait-HttpOk "$url/api/health"
    $cases = Invoke-ScenarioTest 'model-denied' 'test_model_denial_fails_after_bounded_retries' @{ GT_SCENARIO_MODEL_DENIED_URL = $url }
    $results.Add((Merge-TestOutcome $cases 'F5' 'Model denial: the request fails after bounded retries, without a proposal'))
    $artifact = Read-Artifact 'model-denied'
    if ($artifact) {
        $logs = "ContainerAppConsoleLogs_CL | where TimeGenerated > $since | where ContainerAppName_s == '$worker'"
        Add-LogCheck 'F5 log' 'The activity failure is logged with the planning request ID' `
            "$logs | where Log_s contains $(Format-KqlString $artifact.request_id) and Log_s contains 'Planning activity failed' | count"
        Add-LogCheck 'F5 hygiene' 'No provider error body or prompt text in the worker log' -ExpectNone `
            "$logs | where Log_s contains 'lacks the required data action' or Log_s contains 'Synthetic fault validation' | count"
    }
}

foreach ($key in $jobs.Keys) {
    $job = $jobs[$key]
    $status = Wait-ContainerAppJob -ResourceGroup $group -Name $job.Name -Execution $job.Execution
    $outcome = if ($status -eq 'Succeeded') { 'Passed' } else { 'Failed' }
    $results.Add((New-CheckResult $key $job.Description $outcome "$($job.Execution): $status"))
}

if (-not (Write-AcceptanceSummary -Results $results.ToArray() -EvidenceDirectory $evidence)) { exit 1 }
