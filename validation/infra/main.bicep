targetScope = 'subscription'

@description('azd environment name. Names the dedicated resource group.')
@minLength(1)
@maxLength(40)
param environmentName string
@description('Region for every validation resource. Confirm Container Apps capacity and model quota first.')
param location string
@description('Signed-in operator object ID (AZURE_PRINCIPAL_ID); the first administrator in every database.')
param principalId string
@description('Validation SPA and API registrations. The preprovision hook creates them unless supplied.')
param spaAppId string = ''
param apiAppId string = ''
// The preprovision hook sets the values below; empty defaults keep azd from asking for them first.
@description('Images by digest in this environment\'s registry. The preprovision hook imports or builds them.')
param apiImage string = ''
param workerImage string = ''
param validationImage string = ''
@description('Use the DNS zone returned by the Scheduler privateLinkResources metadata.')
param schedulerPrivateDnsZoneName string = ''
@description('Model capacity units for this environment. Confirm subscription quota for the SKU and region.')
param planningModelCapacity string = '20'
@description('Comma-separated fault scenarios to deploy.')
param scenarios string = 'worker-restart,sql-outage,blob-outage,scheduler-outage,model-denied'
param organizationName string = 'Game Theory validation'

var enabledScenarios = map(split(scenarios, ','), scenario => trim(scenario))

resource group 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: 'rg-gametheory-validation-${environmentName}'
  location: location
  tags: { 'azd-env-name': environmentName, purpose: 'gametheory-live-acceptance' }
}
module identities 'identities.bicep' = {
  scope: group
  name: 'validation-identities'
  params: { location: location }
}
// The same template as a real studio, so faults are exercised against identical topology.
module studio '../../infra/main.bicep' = {
  scope: group
  name: 'validation-studio'
  params: {
    location: location
    namePrefix: 'gtval'
    tenantId: tenant().tenantId
    spaClientId: spaAppId
    apiAudience: apiAppId
    apiScope: 'api://${apiAppId}/access_as_user'
    sqlAdminId: identities.outputs.operatorClientId
    sqlAdminName: identities.outputs.operatorName
    sqlAdminPrincipalType: 'Application'
    schedulerPrivateDnsZoneName: schedulerPrivateDnsZoneName
    deployApplications: true
    apiImage: apiImage
    workerImage: workerImage
    enablePlanning: true
    deployFoundry: true
    planningModelCapacity: int(planningModelCapacity)
  }
}
module probes '../../infra/probes.bicep' = {
  scope: group
  name: 'validation-probes'
  params: {
    location: location
    namePrefix: 'gtval'
    tenantId: tenant().tenantId
    sqlServerName: studio.outputs.sqlServerName
    schedulerName: studio.outputs.schedulerName
    foundryProjectEndpoint: studio.outputs.foundryEndpoint
    modelDeployment: studio.outputs.modelDeployment
    validationImage: validationImage
  }
}
module faults 'scenarios.bicep' = {
  scope: group
  name: 'validation-scenarios'
  params: {
    location: location
    scenarios: enabledScenarios
    tenantId: tenant().tenantId
    spaAppId: spaAppId
    apiAppId: apiAppId
    principalId: principalId
    organizationName: organizationName
    apiImage: apiImage
    workerImage: workerImage
    validationImage: validationImage
    registryName: studio.outputs.registryName
    containerEnvironmentId: studio.outputs.containerEnvironmentId
    sqlServerName: studio.outputs.sqlServerName
    privateSubnetId: studio.outputs.privateSubnetId
    schedulerPrivateDnsZoneName: schedulerPrivateDnsZoneName
    blobUrl: studio.outputs.blobUrl
    foundryEndpoint: studio.outputs.foundryEndpoint
    modelDeployment: studio.outputs.modelDeployment
    apiIdentityName: studio.outputs.apiIdentityName
    workerIdentityName: studio.outputs.workerIdentityName
    operatorIdentityName: identities.outputs.operatorName
    deniedIdentityName: identities.outputs.deniedName
  }
}

output AZURE_RESOURCE_GROUP string = group.name
output VALIDATION_API_SCOPE string = 'api://${apiAppId}/access_as_user'
output BASELINE_URL string = studio.outputs.webUrl
output BASELINE_WEB_NAME string = studio.outputs.webName
output BASELINE_WORKER_NAME string = studio.outputs.workerAppName
output LOG_ANALYTICS_WORKSPACE_ID string = studio.outputs.logAnalyticsWorkspaceId
output PROBE_JOB_NAMES array = probes.outputs.jobNames
output SETUP_JOB_NAME string = faults.outputs.setupJobName
output RESTART_JOB_NAME string = faults.outputs.restartJobName
output SQL_OUTAGE_URL string = faults.outputs.sqlOutageUrl
output BLOB_OUTAGE_URL string = faults.outputs.blobOutageUrl
output BLOB_OUTAGE_APP string = faults.outputs.blobOutageApp
output BLOB_FAULT_URL string = faults.outputs.faultBlobUrl
output BLOB_HEALTHY_URL string = studio.outputs.blobUrl
output SCHEDULER_OUTAGE_URL string = faults.outputs.schedulerOutageUrl
output SCHEDULER_OUTAGE_WORKER string = faults.outputs.schedulerOutageWorker
output SCHEDULER_FAULT_ENDPOINT string = faults.outputs.faultSchedulerEndpoint
output SCHEDULER_HEALTHY_ENDPOINT string = faults.outputs.healthySchedulerEndpoint
output MODEL_DENIED_URL string = faults.outputs.modelDeniedUrl
output MODEL_DENIED_WORKER string = faults.outputs.modelDeniedWorker
