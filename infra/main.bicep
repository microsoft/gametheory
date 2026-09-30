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
@description('Provision the exercise executor identity, task hub, and bindings share. Nothing runs until enableExecution.')
param deployExecutor bool = false
@description('Start the executor and turn on execution in the API. Needs deployApplications, deployExecutor, and a separate deployment approval.')
param enableExecution bool = false
@description('Reviewed executor image by digest. Used only when execution is enabled.')
param executorImage string = ''
@description('Resource IDs of separately approved user-assigned identities for exercise targets. Attached to the executor only.')
param executorTargetIdentityIds array = []
param enablePlanning bool = false
@description('Run-check assistant on the API and planning worker. Applied only when enablePlanning is true.')
param enableRunAssistant bool = false
@description('Planner output token limit. Reasoning models count reasoning tokens against it.')
@minValue(256)
@maxValue(16000)
param plannerMaxOutputTokens int = 6000
@minValue(10)
@maxValue(600)
param modelTimeoutSeconds int = 120
param foundryProjectEndpoint string = ''
param modelDeployment string = ''
@description('Provision a dedicated private Foundry project and regional model rather than use an existing project.')
param deployFoundry bool = false
@allowed(['Standard', 'DataZoneStandard'])
@description('Use a supported regional or data-zone model SKU; global routing is not enabled by this template.')
param planningModelSku string = 'DataZoneStandard'
@description('OpenAI model and version for the dedicated Foundry deployment. Confirm the SKU is offered for it in the target region.')
param planningModelName string = 'gpt-6-luna'
param planningModelVersion string = '2026-09-22'

var suffix = uniqueString(resourceGroup().id)
var stem = '${namePrefix}-${suffix}'
var databaseName = 'gametheory'
var taskHubName = 'planning'
// The application refuses to start with the assistant on and planning off.
var runAssistantEnabled = enablePlanning && enableRunAssistant
var executionTaskHubName = 'exercises'
var bindingsMountPath = '/mnt/execution-bindings'
var bindingsFile = '${bindingsMountPath}/bindings.json'
// gametheory-executor exits unless execution is enabled, so the app exists only in that state.
var executionOn = deployApplications && deployExecutor && enableExecution

resource apiIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-${stem}-api'
  location: location
}
resource workerIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-${stem}-worker'
  location: location
}
resource executorIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = if (deployExecutor) {
  name: 'id-${stem}-executor'
  location: location
}
// ARM validation evaluates reference() and listKeys() even inside condition-false resources,
// so every runtime reference to an optional executor resource is guarded by its condition.
var executorPrincipalId = deployExecutor ? executorIdentity!.properties.principalId : ''
var executorClientId = deployExecutor ? executorIdentity!.properties.clientId : ''
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
resource pullExecutor 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (deployExecutor) {
  scope: registry
  name: guid(registry.id, executorIdentity.id, 'pull')
  properties: {
    principalId: executorPrincipalId
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
// Holds only the reviewed bindings file. App Service and Container Apps mount Azure Files
// only with the account key, so shared-key access is enabled on this account alone.
resource bindingsAccount 'Microsoft.Storage/storageAccounts@2023-05-01' = if (deployExecutor) {
  name: 'gtb${suffix}'
  location: location
  kind: 'StorageV2'
  sku: { name: 'Standard_LRS' }
  properties: {
    allowBlobPublicAccess: false
    allowSharedKeyAccess: true
    supportsHttpsTrafficOnly: true
    minimumTlsVersion: 'TLS1_2'
    publicNetworkAccess: 'Disabled'
    networkAcls: { defaultAction: 'Deny' }
  }
}
resource bindingsFileService 'Microsoft.Storage/storageAccounts/fileServices@2023-05-01' = if (deployExecutor) {
  parent: bindingsAccount
  name: 'default'
}
resource bindingsFileShare 'Microsoft.Storage/storageAccounts/fileServices/shares@2023-05-01' = if (deployExecutor) {
  parent: bindingsFileService
  name: 'execution-bindings'
  properties: { shareQuota: 1 }
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
resource executionHub 'Microsoft.DurableTask/schedulers/taskHubs@2026-02-01' = if (deployExecutor) {
  parent: scheduler
  name: executionTaskHubName
  properties: {}
}
resource executionHubAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (deployExecutor) {
  scope: executionHub
  name: guid(executionHub.id, executorIdentity.id, 'scheduler')
  properties: {
    principalId: executorPrincipalId
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
module bindingsLink './private-link.bicep' = if (deployExecutor) {
  name: 'bindings-private-link'
  params: {
    name: 'pe-${stem}-bindings'
    location: location
    targetId: bindingsAccount.id
    groupId: 'file'
    dnsZoneName: 'privatelink.file.${environment().suffixes.storage}'
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
    modelName: planningModelName
    modelVersion: planningModelVersion
  }
}
var commonSettings = [
  { name: 'AZURE_CLOUD', value: 'commercial' }
  { name: 'GT_TENANT_ID', value: tenantId }
  { name: 'GT_SPA_CLIENT_ID', value: spaClientId }
  { name: 'GT_API_AUDIENCE', value: apiAudience }
  { name: 'GT_API_SCOPE', value: apiScope }
  { name: 'GT_PLANNING_ENABLED', value: string(enablePlanning) }
  { name: 'GT_RUN_ASSISTANT_ENABLED', value: string(runAssistantEnabled) }
  { name: 'GT_SCHEDULER_ENDPOINT', value: scheduler.properties.endpoint }
  { name: 'GT_SCHEDULER_TASKHUB', value: taskHubName }
  { name: 'GT_FOUNDRY_PROJECT_ENDPOINT', value: deployFoundry ? foundry!.outputs.projectEndpoint : foundryProjectEndpoint }
  { name: 'GT_MODEL_DEPLOYMENT', value: deployFoundry ? foundry!.outputs.modelDeployment : modelDeployment }
]
var sqlUrlBase = 'mssql+pyodbc://${sql.properties.fullyQualifiedDomainName}/${databaseName}?driver=ODBC+Driver+18+for+SQL+Server&authentication=ActiveDirectoryMsi&Encrypt=yes'
// The API and the executor must read the same bindings file.
var executionSettings = [
  { name: 'GT_EXECUTION_ENABLED', value: 'true' }
  { name: 'GT_EXECUTION_TASKHUB', value: executionTaskHubName }
  { name: 'GT_EXECUTION_BINDINGS_FILE', value: bindingsFile }
]

resource web 'Microsoft.Web/sites@2023-12-01' = if (deployApplications) {
  name: 'web-${stem}'
  location: location
  kind: 'app,linux,container'
  identity: { type: 'UserAssigned', userAssignedIdentities: { '${apiIdentity.id}': {} } }
  properties: {
    serverFarmId: plan.id
    httpsOnly: true
    virtualNetworkSubnetId: webSubnet
    // The bindings share has no public endpoint. This routes only the platform's storage-mount
    // traffic through VNet integration; application traffic, including public services, is unchanged.
    ...(executionOn ? { vnetContentShareEnabled: true } : {})
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
      ], executionOn ? executionSettings : [])
      // App Service cannot mount Azure Files read-only. The executor's mount is read-only, and it
      // rejects changed bindings that lack an operator readiness receipt for their digest.
      ...(executionOn ? {
        azureStorageAccounts: {
          'execution-bindings': {
            type: 'AzureFiles'
            protocol: 'Smb'
            accountName: bindingsAccount.name
            shareName: bindingsFileShare.name
            accessKey: bindingsAccount!.listKeys().keys[0].value
            mountPath: bindingsMountPath
          }
        }
      } : {})
    }
  }
  dependsOn: [pullApi, sqlLink, blobLink, bindingsLink]
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
          { name: 'GT_PLANNER_MAX_OUTPUT_TOKENS', value: string(plannerMaxOutputTokens) }
          { name: 'GT_MODEL_TIMEOUT_SECONDS', value: string(modelTimeoutSeconds) }
        ])
      }]
    }
  }
  dependsOn: [pullWorker, schedulerAccess, sqlLink, schedulerLink]
}
resource containerEnvironment 'Microsoft.App/managedEnvironments@2024-03-01' existing = {
  name: 'cae-${stem}'
}
resource bindingsEnvironmentStorage 'Microsoft.App/managedEnvironments/storages@2024-03-01' = if (executionOn) {
  parent: containerEnvironment
  name: 'execution-bindings'
  properties: {
    azureFile: {
      accountName: bindingsAccount.name
      accountKey: executionOn ? bindingsAccount!.listKeys().keys[0].value : ''
      shareName: bindingsFileShare.name
      accessMode: 'ReadOnly'
    }
  }
  dependsOn: [computeNetwork]
}
resource executor 'Microsoft.App/containerApps@2024-03-01' = if (executionOn) {
  name: 'executor-${stem}'
  location: location
  identity: {
    type: 'UserAssigned'
    // Target identities are attached here only, never to the API or the planning worker.
    userAssignedIdentities: union({ '${executorIdentity.id}': {} }, toObject(executorTargetIdentityIds, id => id, id => {}))
  }
  properties: {
    managedEnvironmentId: computeNetwork.outputs.containerEnvironmentId
    workloadProfileName: 'Consumption'
    configuration: {
      activeRevisionsMode: 'Single'
      registries: [{ server: registry.properties.loginServer, identity: executorIdentity.id }]
    }
    template: {
      scale: { minReplicas: 1, maxReplicas: 1 }
      volumes: [{
        name: 'execution-bindings'
        storageType: 'AzureFile'
        storageName: bindingsEnvironmentStorage.name
        // Readable by the image's non-root user (uid 10001) whatever the platform's default SMB modes are.
        mountOptions: 'dir_mode=0555,file_mode=0444'
      }]
      containers: [{
        name: 'executor'
        image: executorImage
        resources: { cpu: 1, memory: '2Gi' }
        volumeMounts: [{ volumeName: 'execution-bindings', mountPath: bindingsMountPath }]
        // Explicit settings: no planning, Foundry, or model configuration reaches the executor.
        // AZURE_CLIENT_ID keeps DefaultAzureCredential on the executor identity, never a target identity.
        env: concat([
          { name: 'AZURE_CLOUD', value: 'commercial' }
          { name: 'GT_TENANT_ID', value: tenantId }
          { name: 'AZURE_CLIENT_ID', value: executorClientId }
          { name: 'GT_SQL_URL', value: '${sqlUrlBase}&UID=${executorClientId}' }
          { name: 'GT_SCHEDULER_ENDPOINT', value: scheduler.properties.endpoint }
          { name: 'GT_SCHEDULER_TASKHUB', value: taskHubName }
        ], executionSettings)
      }]
    }
  }
  dependsOn: [pullExecutor, executionHubAccess, sqlLink, schedulerLink, bindingsLink]
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
output runAssistantEnabled bool = runAssistantEnabled
output webUrl string = deployApplications ? 'https://${web!.properties.defaultHostName}' : ''
output executorIdentityName string = deployExecutor ? executorIdentity.name : ''
output executorIdentityClientId string = executorClientId
output executorIdentityObjectId string = executorPrincipalId
output executionTaskHub string = deployExecutor ? executionHub.name : ''
output bindingsStorageAccount string = deployExecutor ? bindingsAccount.name : ''
output bindingsShare string = deployExecutor ? bindingsFileShare.name : ''
output bindingsFile string = deployExecutor ? bindingsFile : ''
output executionEnabled bool = executionOn
output executorAppName string = executionOn ? executor.name : ''
