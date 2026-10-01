#requires -Version 7.2
<#
.SYNOPSIS
Publishes the validation image to a studio's registry and deploys the private-network probe jobs.

.DESCRIPTION
Reads names from the studio's latest infra/main.bicep deployment, builds the validation (test)
image from this checkout with ACR Tasks, and deploys infra/probes.bicep into the same resource
group: the validation task hub, its grant, and the validate-dependencies and validate-blob jobs.
See docs/live-acceptance.md.

.EXAMPLE
pwsh ./scripts/acceptance/Deploy-ProbeJobs.ps1 -ResourceGroup <studio-resource-group>
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string] $ResourceGroup,
    [string] $Subscription,
    # The studio's infra/main.bicep deployment; defaults to the newest successful one.
    [string] $Deployment,
    # An already published validation image by digest; skips the build.
    [string] $ValidationImage
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
Import-Module (Join-Path $PSScriptRoot 'AcceptanceCommon.psm1') -Force

if ($Subscription) { Invoke-Az account set --subscription $Subscription | Out-Null }

function Get-DeploymentValue([object] $Object, [string] $Name) {
    if ($Object -and $Object.PSObject.Properties[$Name]) { $Object.$Name.value } else { $null }
}

$deployments = @(Invoke-AzJson deployment group list --resource-group $ResourceGroup)
$studio = $deployments | Where-Object {
    $_.properties.provisioningState -eq 'Succeeded' -and $_.properties.PSObject.Properties['outputs'] -and
    $_.properties.outputs -and $_.properties.outputs.PSObject.Properties['foundryEndpoint'] -and
    (-not $Deployment -or $_.name -eq $Deployment)
} | Sort-Object { $_.properties.timestamp } -Descending | Select-Object -First 1
if (-not $studio) { throw "No successful studio template deployment found in $ResourceGroup" }
$parameters = $studio.properties.parameters
$outputs = $studio.properties.outputs
Write-Host "Using deployment $($studio.name)"

function Find-Name([string] $Type) {
    $names = @(Invoke-AzJson resource list --resource-group $ResourceGroup --resource-type $Type --query '[].name')
    if ($names.Count -ne 1) { throw "Expected one $Type in $ResourceGroup; found $($names.Count)" }
    $names[0]
}

$namePrefix = (Get-DeploymentValue $parameters 'namePrefix') ?? 'gametheory'
$sqlServerName = (Get-DeploymentValue $outputs 'sqlServerName') ?? (Get-DeploymentValue $parameters 'sqlServerName') ?? (Find-Name 'Microsoft.Sql/servers')
$schedulerName = (Get-DeploymentValue $outputs 'schedulerName') ?? (Get-DeploymentValue $parameters 'schedulerName') ?? (Find-Name 'Microsoft.DurableTask/schedulers')
$registryName = (Get-DeploymentValue $outputs 'registryName') ?? (Find-Name 'Microsoft.ContainerRegistry/registries')
$modelDeployment = Get-DeploymentValue $outputs 'modelDeployment'
if (-not $modelDeployment) {
    $modelDeployment = if ((Get-DeploymentValue $parameters 'deployFoundry') -eq $true) {
        (Get-DeploymentValue $parameters 'planningModelName') ?? 'gpt-5.6-luna'
    }
    else { Get-DeploymentValue $parameters 'modelDeployment' }
}

$image = if ($ValidationImage) {
    $ValidationImage
}
else {
    Publish-SourceImage -Registry $registryName -Repository 'gametheory-validation' -Target 'validation'
}
Write-Host "Validation image: $image"
Invoke-Az deployment group create --resource-group $ResourceGroup --name acceptance-probes `
    --template-file (Join-Path (Get-RepositoryRoot) 'infra' 'probes.bicep') `
    --parameters "namePrefix=$namePrefix" "tenantId=$(Get-DeploymentValue $parameters 'tenantId')" `
    "sqlServerName=$sqlServerName" "schedulerName=$schedulerName" `
    "foundryProjectEndpoint=$(Get-DeploymentValue $outputs 'foundryEndpoint')" "modelDeployment=$modelDeployment" `
    "validationImage=$image" --output none | Out-Null
Write-Host 'Deployed the validate-dependencies and validate-blob jobs and the validation task hub.'
