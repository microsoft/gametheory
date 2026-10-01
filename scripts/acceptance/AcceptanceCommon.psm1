#requires -Version 7.2
# Shared helpers for the live-acceptance runners and the validation environment hooks.
# Tokens stay in memory: they reach child processes only through environment variables that
# are removed afterward, and they are never printed or written to disk.

Set-StrictMode -Version Latest

$script:AzureCliAppId = '04b07795-8ddb-461a-bbee-02f9e1bf7b46'
$script:WorkspaceCustomerIds = @{}

function Get-RepositoryRoot {
    (Resolve-Path (Join-Path $PSScriptRoot '..' '..')).Path
}

function Invoke-Az {
    # Runs az, failing on a non-zero exit. Arguments never contain secrets; output may.
    # A simple function, so az's --flags pass through untouched instead of binding as parameters.
    $output = & az @args --only-show-errors
    if ($LASTEXITCODE -ne 0) {
        throw "az $($args | Select-Object -First 3) failed with exit code $LASTEXITCODE"
    }
    $output
}

function Invoke-AzJson {
    $output = Invoke-Az @args --output json
    if (-not $output) { return }
    ($output | Out-String) | ConvertFrom-Json
}

function Get-AzText {
    ((Invoke-Az @args --output tsv) | Out-String).Trim()
}

function Get-AccessToken {
    # Returns a short-lived token for a scope or resource, optionally from another az profile.
    param(
        [string] $Scope,
        [string] $Resource,
        [string] $Tenant,
        [string] $ConfigDirectory
    )
    $arguments = @('account', 'get-access-token', '--query', 'accessToken', '--output', 'tsv')
    $arguments += if ($Scope) { @('--scope', $Scope) } else { @('--resource', $Resource) }
    if ($Tenant) { $arguments += @('--tenant', $Tenant) }
    $previous = $env:AZURE_CONFIG_DIR
    try {
        if ($ConfigDirectory) { $env:AZURE_CONFIG_DIR = $ConfigDirectory }
        $token = & az @arguments --only-show-errors
        if ($LASTEXITCODE -ne 0 -or -not $token) {
            throw "Could not get a token for $(if ($Scope) { $Scope } else { $Resource })"
        }
        ($token | Out-String).Trim()
    }
    finally {
        $env:AZURE_CONFIG_DIR = $previous
    }
}

function Get-TokenClaim {
    # Decodes claims locally to choose test inputs; the signature is not, and need not be, checked.
    param([Parameter(Mandatory)] [string] $Token)
    $payload = $Token.Split('.')[1].Replace('-', '+').Replace('_', '/')
    switch ($payload.Length % 4) { 2 { $payload += '==' } 3 { $payload += '=' } }
    [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($payload)) | ConvertFrom-Json
}

function Invoke-GraphRequest {
    param(
        [Parameter(Mandatory)] [ValidateSet('GET', 'PATCH', 'POST', 'DELETE')] [string] $Method,
        [Parameter(Mandatory)] [string] $Path,
        [object] $Body
    )
    $arguments = @('rest', '--method', $Method.ToLowerInvariant(), '--url', "https://graph.microsoft.com/v1.0/$Path")
    $file = $null
    try {
        if ($null -ne $Body) {
            $file = New-TemporaryFile
            $Body | ConvertTo-Json -Depth 20 -Compress | Set-Content -Path $file -Encoding utf8NoBOM
            $arguments += @('--headers', 'Content-Type=application/json', '--body', "@$file")
        }
        $output = Invoke-Az @arguments
    }
    finally {
        if ($file) { Remove-Item $file -Force }
    }
    if ($output) { ($output | Out-String) | ConvertFrom-Json }
}

function Grant-AzureCliPreAuthorization {
    # Lets `az account get-access-token --scope api://<app>/<scope>` sign tests in as the user.
    # Every other `api` setting is sent back unchanged, so the token version and scopes survive.
    param(
        [Parameter(Mandatory)] [string] $ApiAppId,
        [string] $ScopeValue = 'access_as_user',
        [switch] $Remove
    )
    $objectId = Get-AzText ad app show --id $ApiAppId --query id
    $application = Invoke-GraphRequest -Method GET -Path "applications/$objectId"
    $api = $application.api
    $scope = @($api.oauth2PermissionScopes | Where-Object { $_.value -eq $ScopeValue })
    if ($scope.Count -ne 1) { throw "Registration $ApiAppId has no '$ScopeValue' scope" }
    $entries = @($api.preAuthorizedApplications | Where-Object { $_.appId -ne $script:AzureCliAppId })
    if (-not $Remove) {
        $entries += [pscustomobject]@{ appId = $script:AzureCliAppId; delegatedPermissionIds = @($scope[0].id) }
    }
    $api.preAuthorizedApplications = $entries
    Invoke-GraphRequest -Method PATCH -Path "applications/$objectId" -Body @{ api = $api } | Out-Null
}

function Invoke-LogQuery {
    # Queries Log Analytics through its REST API, so no CLI extension is needed.
    param(
        [Parameter(Mandatory)] [string] $WorkspaceResourceId,
        [Parameter(Mandatory)] [string] $Query
    )
    if (-not $script:WorkspaceCustomerIds.ContainsKey($WorkspaceResourceId)) {
        $script:WorkspaceCustomerIds[$WorkspaceResourceId] =
            Get-AzText resource show --ids $WorkspaceResourceId --query properties.customerId
    }
    $customerId = $script:WorkspaceCustomerIds[$WorkspaceResourceId]
    $file = New-TemporaryFile
    try {
        @{ query = $Query } | ConvertTo-Json -Compress | Set-Content -Path $file -Encoding utf8NoBOM
        $result = Invoke-AzJson rest --method post `
            --url "https://api.loganalytics.io/v1/workspaces/$customerId/query" `
            --resource 'https://api.loganalytics.io' --body "@$file"
    }
    finally {
        Remove-Item $file -Force
    }
    $table = $result.tables[0]
    foreach ($row in $table.rows) {
        $item = [ordered]@{}
        for ($index = 0; $index -lt $table.columns.Count; $index++) {
            $item[$table.columns[$index].name] = $row[$index]
        }
        [pscustomobject]$item
    }
}

function Get-LogCount {
    # Returns the `Count` column of a query, or -1 while a table does not exist yet.
    param([string] $WorkspaceResourceId, [string] $Query)
    try {
        $rows = @(Invoke-LogQuery -WorkspaceResourceId $WorkspaceResourceId -Query $Query)
        if ($rows.Count -eq 0) { return 0 }
        [long] $rows[0].Count
    }
    catch {
        -1
    }
}

function Wait-LogCount {
    # Polls until a positive log check finds rows; ingestion usually lags a few minutes.
    param([string] $WorkspaceResourceId, [string] $Query, [int] $TimeoutMinutes = 20)
    $deadline = (Get-Date).AddMinutes($TimeoutMinutes)
    do {
        $count = Get-LogCount -WorkspaceResourceId $WorkspaceResourceId -Query $Query
        if ($count -gt 0) { return $count }
        Start-Sleep -Seconds 60
    } while ((Get-Date) -lt $deadline)
    [Math]::Max($count, 0)
}

function Format-KqlString {
    param([string] $Value)
    "'" + $Value.Replace('\', '\\').Replace("'", "\'") + "'"
}

function Start-ContainerAppJob {
    param([Parameter(Mandatory)] [string] $ResourceGroup, [Parameter(Mandatory)] [string] $Name)
    $execution = Invoke-AzJson containerapp job start --resource-group $ResourceGroup --name $Name
    if ($execution -and $execution.PSObject.Properties['name']) { return $execution.name }
    $executions = @(Invoke-AzJson containerapp job execution list --resource-group $ResourceGroup --name $Name)
    ($executions | Sort-Object { $_.properties.startTime } | Select-Object -Last 1).name
}

function Wait-ContainerAppJob {
    param(
        [Parameter(Mandatory)] [string] $ResourceGroup,
        [Parameter(Mandatory)] [string] $Name,
        [Parameter(Mandatory)] [string] $Execution,
        [int] $TimeoutMinutes = 45
    )
    $deadline = (Get-Date).AddMinutes($TimeoutMinutes)
    do {
        Start-Sleep -Seconds 20
        $state = Invoke-AzJson containerapp job execution show --resource-group $ResourceGroup `
            --name $Name --job-execution-name $Execution
        $status = $state.properties.status
        if ($status -in 'Succeeded', 'Failed', 'Stopped', 'Degraded') { return $status }
    } while ((Get-Date) -lt $deadline)
    'TimedOut'
}

function Wait-HttpOk {
    param([Parameter(Mandatory)] [string] $Url, [int] $TimeoutMinutes = 10)
    $deadline = (Get-Date).AddMinutes($TimeoutMinutes)
    do {
        try {
            $response = Invoke-WebRequest -Uri $Url -TimeoutSec 60 -SkipHttpErrorCheck
            if ($response.StatusCode -eq 200) { return }
        }
        catch {
            Write-Verbose "Waiting for ${Url}: $($_.Exception.Message)"
        }
        Start-Sleep -Seconds 10
    } while ((Get-Date) -lt $deadline)
    throw "Timed out waiting for $Url"
}

function Set-ContainerAppSetting {
    # Changes environment settings, then waits until the new revision is provisioned.
    param(
        [Parameter(Mandatory)] [string] $ResourceGroup,
        [Parameter(Mandatory)] [string] $Name,
        [Parameter(Mandatory)] [hashtable] $Settings,
        [switch] $RequireRunningReplica
    )
    # Always an array: splatting a lone string would pass each character as an argument.
    $pairs = @(foreach ($key in $Settings.Keys) { "$key=$($Settings[$key])" })
    Invoke-Az containerapp update --resource-group $ResourceGroup --name $Name --set-env-vars @pairs --output none |
        Out-Null
    $deadline = (Get-Date).AddMinutes(10)
    do {
        $app = Invoke-AzJson containerapp show --resource-group $ResourceGroup --name $Name
        $revision = Invoke-AzJson containerapp revision show --resource-group $ResourceGroup --name $Name `
            --revision $app.properties.latestRevisionName
        $provisioned = $revision.properties.provisioningState -eq 'Provisioned'
        $running = "$($revision.properties.runningState)" -like 'Running*'
        if ($provisioned -and ($running -or -not $RequireRunningReplica)) { return }
        Start-Sleep -Seconds 10
    } while ((Get-Date) -lt $deadline)
    throw "$Name did not finish rolling out its new settings"
}

function Get-AcceptancePython {
    if ($env:GT_ACCEPTANCE_PYTHON) { return $env:GT_ACCEPTANCE_PYTHON }
    $root = Get-RepositoryRoot
    foreach ($candidate in '.venv/Scripts/python.exe', '.venv/bin/python', 'backend/.venv/Scripts/python.exe', 'backend/.venv/bin/python') {
        $path = Join-Path $root $candidate
        if (Test-Path $path) { return $path }
    }
    $python = Get-Command python3, python -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $python) { throw 'Python with the backend dev dependencies is required; see docs/development.md' }
    $python.Source
}

function Invoke-AcceptancePytest {
    # Runs pytest from backend/ with temporary environment variables and returns JUnit results.
    param(
        [Parameter(Mandatory)] [string[]] $Arguments,
        [hashtable] $Environment = @{},
        [Parameter(Mandatory)] [string] $JUnitPath
    )
    $python = Get-AcceptancePython
    $previous = @{}
    foreach ($name in $Environment.Keys) {
        $previous[$name] = [Environment]::GetEnvironmentVariable($name)
        [Environment]::SetEnvironmentVariable($name, [string] $Environment[$name])
    }
    Push-Location (Join-Path (Get-RepositoryRoot) 'backend')
    $exitCode = $null
    try {
        & $python -m pytest -q -rA --tb=short -p no:cacheprovider "--junitxml=$JUnitPath" @Arguments | Out-Host
        $exitCode = $LASTEXITCODE
    }
    finally {
        Pop-Location
        foreach ($name in $previous.Keys) { [Environment]::SetEnvironmentVariable($name, $previous[$name]) }
    }
    if (-not (Test-Path $JUnitPath)) {
        return [pscustomobject]@{ Name = 'pytest'; Outcome = 'Failed'; Detail = "pytest produced no results (exit $exitCode)" }
    }
    Read-JUnitResult -Path $JUnitPath
}

function Read-JUnitResult {
    param([Parameter(Mandatory)] [string] $Path)
    if (-not (Test-Path $Path)) { return }
    [xml] $document = Get-Content -Raw -Path $Path
    foreach ($case in $document.SelectNodes('//testcase')) {
        $problem = $case.SelectSingleNode('failure') ?? $case.SelectSingleNode('error')
        $skipped = $case.SelectSingleNode('skipped')
        $outcome = if ($problem) { 'Failed' } elseif ($skipped) { 'Skipped' } else { 'Passed' }
        $detail = if ($problem) { $problem.GetAttribute('message') } elseif ($skipped) { $skipped.GetAttribute('message') } else { '' }
        [pscustomobject]@{
            Name    = $case.GetAttribute('name')
            Outcome = $outcome
            Detail  = ($detail -split "`n")[0]
        }
    }
}

function New-EvidenceDirectory {
    param([Parameter(Mandatory)] [string] $Kind)
    $stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMdd-HHmmss')
    $path = Join-Path (Get-RepositoryRoot) '.acceptance' $Kind $stamp
    New-Item -ItemType Directory -Force -Path $path | Out-Null
    $path
}

function New-CheckResult {
    param(
        [Parameter(Mandatory)] [string] $Check,
        [Parameter(Mandatory)] [string] $Description,
        [Parameter(Mandatory)] [ValidateSet('Passed', 'Failed', 'Skipped')] [string] $Outcome,
        [string] $Detail = ''
    )
    [pscustomobject]@{ Check = $Check; Description = $Description; Outcome = $Outcome; Detail = $Detail }
}

function Merge-TestOutcome {
    # Collapses JUnit cases into one check result: any failure fails, all skipped skips.
    param([object[]] $Cases, [string] $Check, [string] $Description)
    $items = @($Cases | Where-Object { $_ })
    $failed = @($items | Where-Object Outcome -EQ 'Failed')
    $passed = @($items | Where-Object Outcome -EQ 'Passed')
    if ($failed) {
        return New-CheckResult $Check $Description 'Failed' (($failed | ForEach-Object { "$($_.Name): $($_.Detail)" }) -join '; ')
    }
    if ($passed) {
        $skipped = @($items | Where-Object Outcome -EQ 'Skipped')
        $note = if ($skipped) { "skipped: $(($skipped | ForEach-Object Name) -join ', ')" } else { '' }
        return New-CheckResult $Check $Description 'Passed' $note
    }
    $reason = ($items | Select-Object -First 1 | ForEach-Object Detail)
    New-CheckResult $Check $Description 'Skipped' "$reason"
}

function Write-AcceptanceSummary {
    param([Parameter(Mandatory)] [object[]] $Results, [Parameter(Mandatory)] [string] $EvidenceDirectory)
    Write-Host ''
    foreach ($result in $Results) {
        Write-Host ('{0,-12} {1,-8} {2}' -f $result.Check, $result.Outcome, $result.Description)
        if ($result.Detail) { Write-Host ('{0,-21} {1}' -f '', $result.Detail) }
    }
    $Results | ConvertTo-Json -Depth 5 | Set-Content -Path (Join-Path $EvidenceDirectory 'summary.json') -Encoding utf8NoBOM
    Write-Host "Evidence: $EvidenceDirectory"
    @($Results | Where-Object Outcome -EQ 'Failed').Count -eq 0
}

function Get-SourceTag {
    # Commit-based tag; uncommitted changes get a unique suffix so images are never confused.
    $root = Get-RepositoryRoot
    $commit = ((git -C $root rev-parse --short=12 HEAD) | Out-String).Trim()
    if (git -C $root status --porcelain) {
        return "$commit-dirty-$((Get-Date).ToUniversalTime().ToString('yyyyMMddHHmmss'))"
    }
    $commit
}

function Publish-SourceImage {
    # Builds a Dockerfile target in the registry with ACR Tasks and returns its reference by digest.
    param(
        [Parameter(Mandatory)] [string] $Registry,
        [Parameter(Mandatory)] [string] $Repository,
        [Parameter(Mandatory)] [string] $Target,
        [string] $Tag = (Get-SourceTag)
    )
    $root = Get-RepositoryRoot
    $server = Get-AzText acr show --name $Registry --query loginServer
    $digest = & az acr repository show --name $Registry --image "${Repository}:$Tag" --query digest --output tsv --only-show-errors 2>$null
    if ($LASTEXITCODE -ne 0 -or -not $digest) {
        # ACR Tasks resolves --file relative to the uploaded context, which honors .dockerignore.
        # --no-logs waits without streaming: on Windows the CLI can crash encoding build logs
        # for a redirected console. Success is judged by the pushed image.
        Write-Host "Building $Target in $Registry with ACR Tasks"
        # The CLI resolves --file from the working directory, so build from the repository root.
        Push-Location $root
        try {
            $run = Invoke-AzJson acr build --registry $Registry --image "${Repository}:$Tag" --target $Target `
                --file Dockerfile . --no-logs
        }
        finally {
            Pop-Location
        }
        $status = if ($run -and $run.PSObject.Properties['status']) { $run.status } else { 'Unknown' }
        $digest = & az acr repository show --name $Registry --image "${Repository}:$Tag" --query digest --output tsv --only-show-errors 2>$null
        if ($LASTEXITCODE -ne 0 -or -not $digest) {
            throw "Building $Target ended $status; see az acr task list-runs --registry $Registry"
        }
    }
    "$server/$Repository@$(($digest | Out-String).Trim())"
}

Export-ModuleMember -Function Get-RepositoryRoot, Invoke-Az, Invoke-AzJson, Get-AzText, Get-AccessToken,
Get-TokenClaim, Invoke-GraphRequest, Grant-AzureCliPreAuthorization, Invoke-LogQuery, Get-LogCount,
Wait-LogCount, Format-KqlString, Start-ContainerAppJob, Wait-ContainerAppJob, Wait-HttpOk,
Set-ContainerAppSetting, Get-AcceptancePython, Invoke-AcceptancePytest, Read-JUnitResult,
New-EvidenceDirectory, New-CheckResult, Merge-TestOutcome, Write-AcceptanceSummary, Get-SourceTag,
Publish-SourceImage
