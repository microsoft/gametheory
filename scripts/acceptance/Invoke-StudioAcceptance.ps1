#requires -Version 7.2
<#
.SYNOPSIS
Runs the live-acceptance checks S2-S8 against a deployed studio and summarizes the evidence.

.DESCRIPTION
Signs the tests in with Azure CLI, runs the live API tests, restarts the applications to prove
persistence, runs the private-network probe jobs, and checks Log Analytics for correlation and
hygiene. Tokens stay in memory. Evidence, which names tenant resources, goes to the gitignored
.acceptance/studio/ folder. See docs/live-acceptance.md.

.EXAMPLE
pwsh ./scripts/acceptance/Invoke-StudioAcceptance.ps1 -ResourceGroup rg-studio -OtherTenant <tenant-id> `
    -MemberConfigDirectory ~/.azure-member -WaitForTokenExpiry
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string] $ResourceGroup,
    [string] $Subscription,
    # The studio's infra/main.bicep deployment; defaults to the newest one with its outputs.
    [string] $Deployment,
    # A tenant where any account in your Azure CLI is signed in, for the wrong-tenant token.
    [string] $OtherTenant,
    # An AZURE_CONFIG_DIR where a non-administrator member of the studio tenant is signed in.
    [string] $MemberConfigDirectory,
    # Keep the first token and prove it is refused after it expires (60-90 minutes).
    [switch] $WaitForTokenExpiry,
    [switch] $SkipRestart,
    [switch] $SkipProbes,
    [switch] $SkipLogs
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
Import-Module (Join-Path $PSScriptRoot 'AcceptanceCommon.psm1') -Force

if ($Subscription) { Invoke-Az account set --subscription $Subscription | Out-Null }

function Get-StudioOutput {
    if ($Deployment) {
        return Invoke-AzJson deployment group show --resource-group $ResourceGroup --name $Deployment --query properties.outputs
    }
    $deployments = @(Invoke-AzJson deployment group list --resource-group $ResourceGroup)
    foreach ($item in ($deployments | Sort-Object { $_.properties.timestamp } -Descending)) {
        $property = $item.properties.PSObject.Properties['outputs']
        $outputs = if ($property) { $property.Value } else { $null }
        if ($outputs -and $outputs.PSObject.Properties['webName']) { return $outputs }
    }
}

function Find-Resource([string] $Kind, [string] $Prefix) {
    $names = @(Invoke-AzJson $Kind list --resource-group $ResourceGroup --query "[?starts_with(name, '$Prefix')].name")
    if ($names.Count -ne 1) { throw "Expected one $Kind named $Prefix* in $ResourceGroup" }
    $names[0]
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

$outputs = Get-StudioOutput
function Get-Output([string] $Name) {
    # Older deployments lack these outputs; empty values count as missing too.
    if (-not $outputs -or -not $outputs.PSObject.Properties[$Name]) { return $null }
    $value = $outputs.$Name.value
    if ($null -eq $value -or ($value -is [string] -and -not $value)) { return $null }
    $value
}
$webName = (Get-Output 'webName') ?? (Find-Resource 'webapp' 'web-')
$workerName = (Get-Output 'workerAppName') ?? (Find-Resource 'containerapp' 'worker-')
$workspaceQuery = @('resource', 'list', '--resource-group', $ResourceGroup, '--resource-type', 'Microsoft.OperationalInsights/workspaces', '--query', '[0].id')
$workspace = (Get-Output 'logAnalyticsWorkspaceId') ?? (Get-AzText @workspaceQuery)
$probes = Get-Output 'probeJobNames'
if (-not $probes) {
    $probes = Invoke-AzJson containerapp job list --resource-group $ResourceGroup --query "[?starts_with(name, 'validate-')].name"
}
$probes = @($probes | Where-Object { $_ })
$webUrl = 'https://' + (Get-AzText webapp show --resource-group $ResourceGroup --name $webName --query defaultHostName)

$config = Invoke-RestMethod -Uri "$webUrl/api/config"
$scope = $config.auth.scope
$tenant = ($config.auth.authority.TrimEnd('/') -split '/')[-1]

function Get-OptionalToken([string] $Purpose, [scriptblock] $Acquire) {
    # A missing optional token skips its checks instead of ending the run.
    try { & $Acquire }
    catch {
        Write-Warning "No $Purpose token ($($_.Exception.Message)); its checks will be skipped."
        ''
    }
}
$ownerToken = Get-OptionalToken 'owner' { Get-AccessToken -Scope $scope -Tenant $tenant }
if (-not $ownerToken) {
    Write-Warning 'Preauthorize Azure CLI with Grant-AzureCliAccess.ps1 to run S3-S5 and S8.'
}
$ownerId = if ($ownerToken) { (Get-TokenClaim $ownerToken).oid } else { '' }
$expiringToken = if ($WaitForTokenExpiry) { $ownerToken } else { '' }
$wrongAudience = Get-OptionalToken 'wrong-audience' { Get-AccessToken -Resource 'https://management.azure.com/' -Tenant $tenant }
$wrongTenant = if ($OtherTenant) {
    Get-OptionalToken 'wrong-tenant' {
        # --tenant asks with the default account, which may not exist in that tenant; a cached
        # subscription there selects an account that does.
        $others = @(Invoke-AzJson account list --all --query "[?tenantId=='$OtherTenant'].id")
        if ($others) { Get-AccessToken -Resource 'https://management.azure.com/' -Subscription $others[0] }
        else { Get-AccessToken -Resource 'https://management.azure.com/' -Tenant $OtherTenant }
    }
} else { '' }
$memberToken = if ($MemberConfigDirectory) {
    Get-OptionalToken 'member' { Get-AccessToken -Scope $scope -Tenant $tenant -ConfigDirectory $MemberConfigDirectory }
} else { '' }

$evidence = New-EvidenceDirectory 'studio'
$artifact = Join-Path $evidence 'live-api.json'
$since = "datetime($((Get-Date).ToUniversalTime().AddMinutes(-2).ToString('o')))"
$results = [Collections.Generic.List[object]]::new()
@{ webUrl = $webUrl; webName = $webName; workerName = $workerName; startedAt = (Get-Date).ToUniversalTime().ToString('o') } |
    ConvertTo-Json | Set-Content -Path (Join-Path $evidence 'context.json') -Encoding utf8NoBOM

# Probe jobs run in the background while the API tests run.
$probeRuns = [ordered]@{}
if (-not $SkipProbes) {
    foreach ($probe in $probes) { $probeRuns[$probe] = Start-ContainerAppJob -ResourceGroup $ResourceGroup -Name $probe }
}

$selection = @(
    'test_persisted_authoring_and_reviewed_real_planning'
    'test_live_rejects_missing_and_malformed_tokens'
    'test_live_token_denials'
    'test_live_workspace_isolation_and_revocation'
) -join ' or '
$cases = @(Invoke-AcceptancePytest -Arguments @('tests/test_live_api.py', '-k', $selection) `
        -JUnitPath (Join-Path $evidence 'live-api.xml') -Environment @{
        GT_LIVE_API_URL                  = $webUrl
        GT_LIVE_API_TOKEN                = $ownerToken
        GT_LIVE_API_EXPECTED_USER        = $ownerId
        GT_LIVE_API_ARTIFACT             = $artifact
        GT_LIVE_API_WRONG_AUDIENCE_TOKEN = $wrongAudience
        GT_LIVE_API_WRONG_TENANT_TOKEN   = $wrongTenant
        GT_LIVE_API_MEMBER_TOKEN         = $memberToken
    })
$results.Add((Merge-TestOutcome @($cases | Where-Object { $_.Name -like 'test_live_rejects*' -or $_.Name -like 'test_live_token_denials*' }) `
            'S2' 'Missing, malformed, wrong-audience, and wrong-tenant tokens are refused'))
$results.Add((Merge-TestOutcome @($cases | Where-Object Name -EQ 'test_persisted_authoring_and_reviewed_real_planning') `
            'S3' 'Authoring, conditional saves, publication, private assets, idempotent planning, applied proposal'))
$results.Add((Merge-TestOutcome @($cases | Where-Object Name -EQ 'test_live_workspace_isolation_and_revocation') `
            'S4' 'Non-members see nothing, viewers cannot save, removal revokes access'))
# A run that never reached the tests (wrong Python, import errors) must fail, not read as skipped.
$unattributed = @($cases | Where-Object {
        $_.Outcome -eq 'Failed' -and $_.Name -notlike 'test_live_rejects*' -and $_.Name -notlike 'test_live_token_denials*' -and
        $_.Name -ne 'test_persisted_authoring_and_reviewed_real_planning' -and $_.Name -ne 'test_live_workspace_isolation_and_revocation'
    })
if ($unattributed) {
    $results.Add((New-CheckResult 'pytest' 'The live API test run' 'Failed' (($unattributed | ForEach-Object { "$($_.Name): $($_.Detail)" }) -join '; ')))
}

if ($SkipRestart -or -not (Test-Path $artifact)) {
    $results.Add((New-CheckResult 'S5' 'Records persist across an application restart' 'Skipped' 'restart skipped or no S3 artifact'))
}
else {
    Write-Host "Restarting $webName and $workerName"
    Invoke-Az webapp restart --resource-group $ResourceGroup --name $webName --output none | Out-Null
    $revision = Get-AzText containerapp show --resource-group $ResourceGroup --name $workerName --query properties.latestRevisionName
    Invoke-Az containerapp revision restart --resource-group $ResourceGroup --name $workerName --revision $revision --output none |
        Out-Null
    Start-Sleep -Seconds 30
    Wait-HttpOk "$webUrl/api/health"
    $cases = Invoke-AcceptancePytest -Arguments @('tests/test_live_api.py', '-k', 'test_live_records_persist_after_restart') `
        -JUnitPath (Join-Path $evidence 'persistence.xml') -Environment @{
        GT_LIVE_API_URL             = $webUrl
        GT_LIVE_API_TOKEN           = (Get-AccessToken -Scope $scope -Tenant $tenant)
        GT_LIVE_API_VERIFY_ARTIFACT = $artifact
    }
    $state = Get-AzText containerapp revision show --resource-group $ResourceGroup --name $workerName --revision $revision `
        --query properties.runningState
    $result = Merge-TestOutcome $cases 'S5' 'Records, revisions, and asset content persist across an application restart'
    $result.Detail = "$($result.Detail) worker revision: $state".Trim()
    $results.Add($result)
}

$descriptions = @{
    'validate-dependencies' = @('S6', 'Private DNS for SQL, Blob, Scheduler, Foundry; real proposal; managed Scheduler activity')
    'validate-blob'         = @('S7', 'Private Blob round trip with the API identity')
}
foreach ($probe in $probeRuns.Keys) {
    $status = Wait-ContainerAppJob -ResourceGroup $ResourceGroup -Name $probe -Execution $probeRuns[$probe]
    $check, $description = $descriptions[$probe] ?? @("Probe $probe", 'Private-network probe')
    $results.Add((New-CheckResult $check $description ($status -eq 'Succeeded' ? 'Passed' : 'Failed') "$($probeRuns[$probe]): $status"))
}
if ($SkipProbes -or -not $probes) {
    $results.Add((New-CheckResult 'S6' 'Private-network probe jobs' 'Skipped' 'probes skipped or not deployed (deployProbeJobs)'))
}

if ($SkipLogs -or -not (Test-Path $artifact)) {
    $results.Add((New-CheckResult 'S8' 'Log correlation and hygiene' 'Skipped' 'logs skipped or no S3 artifact'))
}
else {
    $record = Get-Content -Raw $artifact | ConvertFrom-Json
    Write-Host 'Waiting for log ingestion'
    Add-LogCheck 'S8 worker' 'The worker log names the planning orchestration instance' `
        "ContainerAppConsoleLogs_CL | where TimeGenerated > $since | where ContainerAppName_s == '$workerName' | where Log_s contains $(Format-KqlString $record.planning_instance) | count"
    Add-LogCheck 'S8 API' 'The API console log records the run''s requests by workspace ID' `
        "AppServiceConsoleLogs | where TimeGenerated > $since | where ResultDescription contains $(Format-KqlString $record.workspace_id) | count"
    Add-LogCheck 'S8 HTTP' 'The API request log records the run''s requests' `
        "AppServiceHTTPLogs | where TimeGenerated > $since | where CsUriStem contains $(Format-KqlString $record.workspace_id) | count"
    $needles = @($record.log_markers) + @('eyJ', 'Bearer ')
    $match = ($needles | ForEach-Object { "Text contains $(Format-KqlString $_)" }) -join ' or '
    Add-LogCheck 'S8 hygiene' 'No prompt text, asset content, or tokens in any application log' -ExpectNone @"
union isfuzzy=true
    (ContainerAppConsoleLogs_CL | where TimeGenerated > $since | project Text = Log_s),
    (AppServiceConsoleLogs | where TimeGenerated > $since | project Text = ResultDescription),
    (AppServiceHTTPLogs | where TimeGenerated > $since | project Text = strcat(CsUriStem, ' ', CsUriQuery, ' ', UserAgent, ' ', Referer, ' ', Cookie))
| where $match
| count
"@
}

if ($expiringToken) {
    $expires = [DateTimeOffset]::FromUnixTimeSeconds([long] (Get-TokenClaim $expiringToken).exp)
    $wait = $expires - [DateTimeOffset]::UtcNow + [TimeSpan]::FromSeconds(90)
    if ($wait -gt [TimeSpan]::Zero) {
        Write-Host "Waiting $([Math]::Ceiling($wait.TotalMinutes)) minutes for the first token to expire"
        Start-Sleep -Seconds ([int] $wait.TotalSeconds)
    }
    $cases = Invoke-AcceptancePytest -Arguments @('tests/test_live_api.py', '-k', 'GT_LIVE_API_EXPIRED_TOKEN') `
        -JUnitPath (Join-Path $evidence 'expired.xml') -Environment @{
        GT_LIVE_API_URL           = $webUrl
        GT_LIVE_API_TOKEN         = (Get-AccessToken -Scope $scope -Tenant $tenant)
        GT_LIVE_API_EXPIRED_TOKEN = $expiringToken
    }
    $results.Add((Merge-TestOutcome $cases 'S2 expired' 'An expired studio token is refused'))
}

if (-not (Write-AcceptanceSummary -Results $results.ToArray() -EvidenceDirectory $evidence)) { exit 1 }
