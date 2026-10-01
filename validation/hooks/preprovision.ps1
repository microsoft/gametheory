#requires -Version 7.2
<#
.SYNOPSIS
Prepares a validation environment before `azd provision`; safe to rerun.

.DESCRIPTION
Creates the dedicated resource group and its registry, imports the studio's exact API and worker
images (or builds them from this checkout), builds the validation test image, and creates
dedicated Entra registrations. See docs/live-acceptance.md.
#>
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
Import-Module (Join-Path $PSScriptRoot '..' '..' 'scripts' 'acceptance' 'AcceptanceCommon.psm1') -Force

function Get-EnvValue([string] $Name, [string] $Default = '') {
    $value = [Environment]::GetEnvironmentVariable($Name)
    if ([string]::IsNullOrWhiteSpace($value)) { return $Default }
    $value
}

function Set-AzdValue([string] $Name, [string] $Value) {
    azd env set $Name $Value --environment $script:environmentName | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "azd env set $Name failed" }
    [Environment]::SetEnvironmentVariable($Name, $Value)
}

function Import-StudioImage([string] $Image, [string] $Subscription, [string] $Registry, [string] $Server) {
    if ($Image -notmatch '^(?<server>[^/]+)/(?<repository>[^@]+)@(?<digest>sha256:[0-9a-f]{64})$') {
        throw "Studio image $Image is not pinned by digest"
    }
    $sourceServer, $repository, $digest = $Matches.server, $Matches.repository, $Matches.digest
    $sources = @(Invoke-AzJson acr list --subscription $Subscription --query "[?loginServer=='$sourceServer'].id")
    if ($sources.Count -ne 1) { throw "Cannot find the studio registry $sourceServer" }
    # Import copies the manifest, so the digest, and therefore the artifact, is unchanged.
    Invoke-Az acr import --name $Registry --source "$repository@$digest" --registry $sources[0] `
        --image "${repository}:studio-$($digest.Substring(7, 12))" --force --output none | Out-Null
    "$Server/$repository@$digest"
}

function New-ValidationRegistration([string] $DisplayName) {
    $scopeId = [guid]::NewGuid().Guid
    $scope = @{
        id                      = $scopeId
        value                   = 'access_as_user'
        type                    = 'User'
        isEnabled               = $true
        adminConsentDisplayName = 'Use the Game Theory validation API'
        adminConsentDescription = 'Lets the validation studio and tests call its API as the signed-in user.'
        userConsentDisplayName  = 'Use the Game Theory validation API'
        userConsentDescription  = 'Lets the validation studio and tests call its API as you.'
    }
    $api = Invoke-AzJson ad app create --display-name "$DisplayName-api" --sign-in-audience AzureADMyOrg
    Invoke-Az ad app update --id $api.appId --identifier-uris "api://$($api.appId)" | Out-Null
    # The API validates v2 issuers, so the registration must issue v2 access tokens.
    Invoke-GraphRequest -Method PATCH -Path "applications/$($api.id)" -Body @{
        api = @{ requestedAccessTokenVersion = 2; oauth2PermissionScopes = @($scope) }
    } | Out-Null
    $spa = Invoke-AzJson ad app create --display-name "$DisplayName-spa" --sign-in-audience AzureADMyOrg
    Invoke-GraphRequest -Method PATCH -Path "applications/$($spa.id)" -Body @{
        spa                    = @{ redirectUris = @() }
        requiredResourceAccess = @(@{ resourceAppId = $api.appId; resourceAccess = @(@{ id = $scopeId; type = 'Scope' }) })
    } | Out-Null
    # Preauthorization avoids consent prompts for the studio and lets Azure CLI fetch test tokens.
    Invoke-GraphRequest -Method PATCH -Path "applications/$($api.id)" -Body @{
        api = @{
            requestedAccessTokenVersion = 2
            oauth2PermissionScopes      = @($scope)
            preAuthorizedApplications   = @(
                @{ appId = $spa.appId; delegatedPermissionIds = @($scopeId) }
                @{ appId = '04b07795-8ddb-461a-bbee-02f9e1bf7b46'; delegatedPermissionIds = @($scopeId) }
            )
        }
    } | Out-Null
    foreach ($appId in $api.appId, $spa.appId) { Invoke-Az ad sp create --id $appId --output none | Out-Null }
    @{ Api = $api.appId; Spa = $spa.appId }
}

$script:environmentName = Get-EnvValue 'AZURE_ENV_NAME'
$location = Get-EnvValue 'AZURE_LOCATION'
$subscription = Get-EnvValue 'AZURE_SUBSCRIPTION_ID'
if (-not ($environmentName -and $location -and $subscription)) {
    throw 'Select a subscription and location for this azd environment first.'
}
Invoke-Az account set --subscription $subscription | Out-Null

$group = "rg-gametheory-validation-$environmentName"
Write-Host "Preparing $group in $location"
Invoke-Az group create --name $group --location $location `
    --tags "azd-env-name=$environmentName" 'purpose=gametheory-live-acceptance' --output none | Out-Null
$registry = Invoke-AzJson deployment group create --resource-group $group --name validation-registry `
    --template-file (Join-Path $PSScriptRoot '..' 'infra' 'registry.bicep') --parameters "location=$location" `
    --query properties.outputs
$registryName = $registry.name.value
$loginServer = $registry.loginServer.value

$studioGroup = Get-EnvValue 'STUDIO_RESOURCE_GROUP'
$studioSubscription = Get-EnvValue 'STUDIO_SUBSCRIPTION_ID' $subscription
if ($studioGroup) {
    Write-Host "Importing the images that $studioGroup runs"
    $webs = @(Invoke-AzJson webapp list --resource-group $studioGroup --subscription $studioSubscription `
            --query "[?starts_with(name, 'web-')].name")
    $workers = @(Invoke-AzJson containerapp list --resource-group $studioGroup --subscription $studioSubscription `
            --query "[?starts_with(name, 'worker-')].name")
    if ($webs.Count -ne 1 -or $workers.Count -ne 1) {
        throw "Expected one studio web app and one planning worker in $studioGroup"
    }
    $apiSource = (Get-AzText webapp config show --resource-group $studioGroup --subscription $studioSubscription `
            --name $webs[0] --query linuxFxVersion) -replace '^DOCKER\|', ''
    $workerSource = Get-AzText containerapp show --resource-group $studioGroup --subscription $studioSubscription `
        --name $workers[0] --query 'properties.template.containers[0].image'
    Set-AzdValue 'VALIDATION_API_IMAGE' (Import-StudioImage $apiSource $studioSubscription $registryName $loginServer)
    Set-AzdValue 'VALIDATION_WORKER_IMAGE' (Import-StudioImage $workerSource $studioSubscription $registryName $loginServer)
}
else {
    Write-Host 'Building API and worker images from this checkout'
    Set-AzdValue 'VALIDATION_API_IMAGE' (Publish-SourceImage -Registry $registryName -Repository 'gametheory-api' -Target 'api')
    Set-AzdValue 'VALIDATION_WORKER_IMAGE' (Publish-SourceImage -Registry $registryName -Repository 'gametheory-worker' -Target 'worker')
}
Write-Host 'Building the validation test image from this checkout'
Set-AzdValue 'VALIDATION_TEST_IMAGE' (Publish-SourceImage -Registry $registryName -Repository 'gametheory-validation' -Target 'validation')

if (-not (Get-EnvValue 'SCHEDULER_PRIVATE_DNS_ZONE')) {
    if (-not $studioGroup) {
        throw 'Set SCHEDULER_PRIVATE_DNS_ZONE from the Scheduler privateLinkResources metadata.'
    }
    $zones = @(Invoke-AzJson network private-dns zone list --resource-group $studioGroup `
            --subscription $studioSubscription --query "[?contains(name, 'durabletask')].name")
    if ($zones.Count -ne 1) { throw "Cannot find the Scheduler private DNS zone in $studioGroup" }
    Set-AzdValue 'SCHEDULER_PRIVATE_DNS_ZONE' $zones[0]
}

$apiAppId = Get-EnvValue 'VALIDATION_API_APP_ID'
$exists = $false
if ($apiAppId) {
    & az ad app show --id $apiAppId --query appId --output tsv --only-show-errors 2>$null | Out-Null
    $exists = $LASTEXITCODE -eq 0
}
if (-not $exists) {
    Write-Host 'Creating dedicated Entra registrations for this environment'
    $created = New-ValidationRegistration "gametheory-validation-$environmentName"
    Set-AzdValue 'VALIDATION_API_APP_ID' $created.Api
    Set-AzdValue 'VALIDATION_SPA_APP_ID' $created.Spa
    Set-AzdValue 'VALIDATION_REGISTRATIONS_MANAGED' 'true'
}
Write-Host 'Preprovision complete'
