targetScope = 'resourceGroup'

@description('Commercial-only template. Confirm service and SKU availability before provisioning.')
param location string = resourceGroup().location
@minLength(3)
@maxLength(12)
param namePrefix string = 'gametheory'
@description('Entra tenant and pre-existing app registrations; no registrations are created here.')
param tenantId string
param spaClientId string
param apiAudience string
param apiScope string
@description('SQL administrator object ID for a user/group, or client ID for an Application principal.')
param sqlAdminId string
param sqlAdminName string
@allowed(['Group', 'User', 'Application'])
param sqlAdminPrincipalType string = 'Group'
@description('Allows an explicitly preflighted SQL server to be adopted by the combined deployment.')
param sqlServerName string = 'sql-${namePrefix}-${uniqueString(resourceGroup().id)}'
param schedulerName string = 'dts-${namePrefix}-${uniqueString(resourceGroup().id)}'
@description('Use the DNS zone returned by the target scheduler privateLinkResources metadata. Do not guess it.')
param schedulerPrivateDnsZoneName string
@description('First provision infrastructure with false; push reviewed image digests, bootstrap SQL, then redeploy with true.')
param deployApplications bool = false
param apiImage string = ''
param workerImage string = ''
param enablePlanning bool = false
param foundryProjectEndpoint string = ''
param modelDeployment string = ''
@description('Provision a dedicated private Foundry project and regional model rather than use an existing project.')
param deployFoundry bool = false
@allowed(['Standard', 'DataZoneStandard'])
@description('Use a supported regional or data-zone model SKU; global routing is not enabled by this template.')
param planningModelSku string = 'Standard'

var suffix = uniqueString(resourceGroup().id)
var stem = '${namePrefix}-${suffix}'
var databaseName = 'gametheory'
var taskHubName = 'planning'

resource apiIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-${stem}-api'
  location: location
}
resource workerIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-${stem}-worker'
  location: location
}
resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: 'gt${suffix}'
  location: location
  sku: { name: 'Basic' }
  properties: { adminUserEnabled: false }
}
resource pullApi 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: registry
  name: guid(registry.id, apiIdentity.id, 'pull')
  properties: {
    principalId: apiIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d')
  }
}
resource pullWorker 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: registry
  name: guid(registry.id, workerIdentity.id, 'pull')
  properties: {
    principalId: workerIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d')
  }
}
module computeNetwork './compute-network.bicep' = {
  name: 'compute-network'
  params: {
    location: location
    namePrefix: namePrefix
  }
}
var webSubnet = computeNetwork.outputs.webSubnetId
var privateSubnet = computeNetwork.outputs.privateSubnetId

resource sql 'Microsoft.Sql/servers@2023-08-01' = {
  name: sqlServerName
  location: location
  properties: {
    version: '12.0'
    minimalTlsVersion: '1.2'
    publicNetworkAccess: 'Disabled'
    administrators: {
      administratorType: 'ActiveDirectory'
      principalType: sqlAdminPrincipalType
      login: sqlAdminName
      sid: sqlAdminId
      tenantId: tenantId
      azureADOnlyAuthentication: true
    }
  }
}
resource database 'Microsoft.Sql/servers/databases@2023-08-01' = {
  parent: sql
  name: databaseName
  location: location
  sku: { name: 'S0', tier: 'Standard', capacity: 10 }
}
resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: 'gt${suffix}'
  location: location
  kind: 'StorageV2'
  sku: { name: 'Standard_LRS' }
  properties: {
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false
    supportsHttpsTrafficOnly: true
    minimumTlsVersion: 'TLS1_2'
    publicNetworkAccess: 'Disabled'
  }
}
resource blobs 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storage
  name: 'default'
  properties: {
    deleteRetentionPolicy: { enabled: true, days: 7 }
    containerDeleteRetentionPolicy: { enabled: true, days: 7 }
  }
}
resource assets 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobs
  name: 'assets'
  properties: { publicAccess: 'None' }
}
resource blobAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: assets
  name: guid(assets.id, apiIdentity.id, 'blob')
  properties: {
    principalId: apiIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'ba92f5b4-2d11-453d-a403-e96b0029c9fe')
  }
}
resource scheduler 'Microsoft.DurableTask/schedulers@2026-02-01' = {
  name: schedulerName
  location: location
  properties: {
    sku: { name: 'Consumption' }
    publicNetworkAccess: 'Disabled'
    ipAllowlist: []
  }
}
resource hub 'Microsoft.DurableTask/schedulers/taskHubs@2026-02-01' = {
  parent: scheduler
  name: taskHubName
  properties: {}
}
resource schedulerAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: hub
  name: guid(hub.id, workerIdentity.id, 'scheduler')
  properties: {
    principalId: workerIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '0ad04412-c4d5-4796-b79c-f76d14c8d402')
  }
}
module sqlLink './private-link.bicep' = {
  name: 'sql-private-link'
  params: {
    name: 'pe-${stem}-sql'
    location: location
    targetId: sql.id
    groupId: 'sqlServer'
    dnsZoneName: 'privatelink${environment().suffixes.sqlServerHostname}'
    virtualNetworkId: computeNetwork.outputs.virtualNetworkId
    subnetId: privateSubnet
  }
}
module blobLink './private-link.bicep' = {
  name: 'blob-private-link'
  params: {
    name: 'pe-${stem}-blob'
    location: location
    targetId: storage.id
    groupId: 'blob'
    dnsZoneName: 'privatelink.blob.${environment().suffixes.storage}'
    virtualNetworkId: computeNetwork.outputs.virtualNetworkId
    subnetId: privateSubnet
  }
}
module schedulerLink './private-link.bicep' = {
  name: 'scheduler-private-link'
  params: {
    name: 'pe-${stem}-scheduler'
    location: location
    targetId: scheduler.id
    groupId: 'scheduler'
    dnsZoneName: schedulerPrivateDnsZoneName
    virtualNetworkId: computeNetwork.outputs.virtualNetworkId
    subnetId: privateSubnet
  }
}
resource plan 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: 'plan-${stem}'
  location: location
  kind: 'linux'
  sku: { name: 'B1', tier: 'Basic' }
  properties: { reserved: true }
}
module foundry './foundry.bicep' = if (deployFoundry) {
  name: 'planning-foundry'
  params: {
    name: 'aif-${stem}'
    location: location
    virtualNetworkId: computeNetwork.outputs.virtualNetworkId
    subnetId: privateSubnet
    workerPrincipalId: workerIdentity.properties.principalId
    modelSku: planningModelSku
  }
}
var commonSettings = [
  { name: 'AZURE_CLOUD', value: 'commercial' }
  { name: 'GT_TENANT_ID', value: tenantId }
  { name: 'GT_SPA_CLIENT_ID', value: spaClientId }
  { name: 'GT_API_AUDIENCE', value: apiAudience }
  { name: 'GT_API_SCOPE', value: apiScope }
  { name: 'GT_PLANNING_ENABLED', value: string(enablePlanning) }
  { name: 'GT_SCHEDULER_ENDPOINT', value: scheduler.properties.endpoint }
  { name: 'GT_SCHEDULER_TASKHUB', value: taskHubName }
  { name: 'GT_FOUNDRY_PROJECT_ENDPOINT', value: deployFoundry ? foundry!.outputs.projectEndpoint : foundryProjectEndpoint }
  { name: 'GT_MODEL_DEPLOYMENT', value: deployFoundry ? foundry!.outputs.modelDeployment : modelDeployment }
]
var sqlUrlBase = 'mssql+pyodbc://${sql.properties.fullyQualifiedDomainName}/${databaseName}?driver=ODBC+Driver+18+for+SQL+Server&authentication=ActiveDirectoryMsi&Encrypt=yes'

resource web 'Microsoft.Web/sites@2023-12-01' = if (deployApplications) {
  name: 'web-${stem}'
  location: location
  kind: 'app,linux,container'
  identity: { type: 'UserAssigned', userAssignedIdentities: { '${apiIdentity.id}': {} } }
  properties: {
    serverFarmId: plan.id
    httpsOnly: true
    virtualNetworkSubnetId: webSubnet
    siteConfig: {
      linuxFxVersion: 'DOCKER|${apiImage}'
      acrUseManagedIdentityCreds: true
      acrUserManagedIdentityID: apiIdentity.properties.clientId
      alwaysOn: true
      minTlsVersion: '1.2'
      ftpsState: 'Disabled'
      healthCheckPath: '/api/health'
      appSettings: concat(commonSettings, [
        { name: 'AZURE_CLIENT_ID', value: apiIdentity.properties.clientId }
        { name: 'GT_SQL_URL', value: '${sqlUrlBase}&UID=${apiIdentity.properties.clientId}' }
        { name: 'GT_BLOB_URL', value: storage.properties.primaryEndpoints.blob }
        { name: 'GT_BLOB_CONTAINER', value: assets.name }
        { name: 'WEBSITES_PORT', value: '8000' }
        { name: 'WEBSITES_ENABLE_APP_SERVICE_STORAGE', value: 'false' }
      ])
    }
  }
  dependsOn: [pullApi, sqlLink, blobLink]
}
resource worker 'Microsoft.App/containerApps@2024-03-01' = if (deployApplications && enablePlanning) {
  name: 'worker-${stem}'
  location: location
  identity: { type: 'UserAssigned', userAssignedIdentities: { '${workerIdentity.id}': {} } }
  properties: {
    managedEnvironmentId: computeNetwork.outputs.containerEnvironmentId
    workloadProfileName: 'Consumption'
    configuration: {
      activeRevisionsMode: 'Single'
      registries: [{ server: registry.properties.loginServer, identity: workerIdentity.id }]
    }
    template: {
      scale: { minReplicas: 1, maxReplicas: 1 }
      containers: [{
        name: 'planner'
        image: workerImage
        resources: { cpu: 1, memory: '2Gi' }
        env: concat(commonSettings, [
          { name: 'AZURE_CLIENT_ID', value: workerIdentity.properties.clientId }
          { name: 'GT_SQL_URL', value: '${sqlUrlBase}&UID=${workerIdentity.properties.clientId}' }
        ])
      }]
    }
  }
  dependsOn: [pullWorker, schedulerAccess, sqlLink, schedulerLink]
}

output registryServer string = registry.properties.loginServer
output sqlServer string = sql.properties.fullyQualifiedDomainName
output database string = database.name
output blobUrl string = storage.properties.primaryEndpoints.blob
output schedulerEndpoint string = scheduler.properties.endpoint
output apiIdentityName string = apiIdentity.name
output apiIdentityObjectId string = apiIdentity.properties.principalId
output workerIdentityName string = workerIdentity.name
output workerIdentityObjectId string = workerIdentity.properties.principalId
output apiIdentityClientId string = apiIdentity.properties.clientId
output workerIdentityClientId string = workerIdentity.properties.clientId
output containerEnvironmentId string = computeNetwork.outputs.containerEnvironmentId
output virtualNetworkId string = computeNetwork.outputs.virtualNetworkId
output plannedWebName string = 'web-${stem}'
output foundryEndpoint string = deployFoundry ? foundry!.outputs.projectEndpoint : foundryProjectEndpoint
output webUrl string = deployApplications ? 'https://${web!.properties.defaultHostName}' : ''
