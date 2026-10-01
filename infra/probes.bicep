// Manual private-network probe jobs for a deployed studio, with their own validation task hub.
// Deploy into the studio's resource group after infra/main.bicep; see docs/live-acceptance.md.
// This is a separate template because Consumption Schedulers allow five task hubs, and deployment
// validation counts every task hub a template declares on top of the hubs that already exist.
// Keeping this hub out of the main template lets both templates be redeployed.
param location string = resourceGroup().location
@minLength(3)
@maxLength(12)
@description('The namePrefix used for the studio deployment, so existing resources are found by name.')
param namePrefix string = 'gametheory'
param tenantId string = tenant().tenantId
@description('The studio\'s SQL server and Scheduler names (main.bicep outputs sqlServerName and schedulerName).')
param sqlServerName string
param schedulerName string
@description('The planning Foundry project endpoint and model deployment (main.bicep outputs).')
param foundryProjectEndpoint string
param modelDeployment string
@description('Validation image (Dockerfile validation target) by digest, in the studio registry.')
param validationImage string

var suffix = uniqueString(resourceGroup().id)
var stem = '${namePrefix}-${suffix}'
var probeArgs = ['-q', '--tb=short', '-p', 'no:cacheprovider', '-rA', 'backend/tests/test_azure_dependencies.py', '-k']

resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = {
  name: 'gt${suffix}'
}
resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: 'gt${suffix}'
}
resource sql 'Microsoft.Sql/servers@2023-08-01' existing = {
  name: sqlServerName
}
resource scheduler 'Microsoft.DurableTask/schedulers@2026-02-01' existing = {
  name: schedulerName
}
resource environment 'Microsoft.App/managedEnvironments@2024-03-01' existing = {
  name: 'cae-${stem}'
}
resource apiIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' existing = {
  name: 'id-${stem}-api'
}
resource workerIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' existing = {
  name: 'id-${stem}-worker'
}

// Probe orchestrations use this hub only, so they never share history with planning.
resource hub 'Microsoft.DurableTask/schedulers/taskHubs@2026-02-01' = {
  parent: scheduler
  name: 'validation'
  properties: {}
}
resource hubAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: hub
  name: guid(hub.id, workerIdentity.id, 'scheduler')
  properties: {
    principalId: workerIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '0ad04412-c4d5-4796-b79c-f76d14c8d402')
  }
}

// Each probe runs as the runtime identity whose access it proves. Starting one is an operator action.
resource dependencyProbe 'Microsoft.App/jobs@2024-03-01' = {
  name: 'validate-dependencies'
  location: location
  identity: { type: 'UserAssigned', userAssignedIdentities: { '${workerIdentity.id}': {} } }
  properties: {
    environmentId: environment.id
    workloadProfileName: 'Consumption'
    configuration: {
      triggerType: 'Manual'
      replicaTimeout: 900
      replicaRetryLimit: 0
      manualTriggerConfig: { parallelism: 1, replicaCompletionCount: 1 }
      registries: [{ server: registry.properties.loginServer, identity: workerIdentity.id }]
    }
    template: {
      containers: [{
        name: 'job'
        image: validationImage
        command: ['pytest']
        args: concat(probeArgs, ['not blob'])
        resources: { cpu: 1, memory: '2Gi' }
        env: [
          { name: 'AZURE_CLIENT_ID', value: workerIdentity.properties.clientId }
          { name: 'AZURE_CLOUD', value: 'commercial' }
          { name: 'GT_TENANT_ID', value: tenantId }
          { name: 'GT_PLANNING_ENABLED', value: 'true' }
          { name: 'GT_SQL_URL', value: 'mssql+pyodbc://${sql.properties.fullyQualifiedDomainName}/gametheory?driver=ODBC+Driver+18+for+SQL+Server&authentication=ActiveDirectoryMsi&Encrypt=yes&UID=${workerIdentity.properties.clientId}' }
          { name: 'GT_BLOB_URL', value: storage.properties.primaryEndpoints.blob }
          { name: 'GT_SCHEDULER_ENDPOINT', value: scheduler.properties.endpoint }
          { name: 'GT_SCHEDULER_TASKHUB', value: hub.name }
          { name: 'GT_FOUNDRY_PROJECT_ENDPOINT', value: foundryProjectEndpoint }
          { name: 'GT_MODEL_DEPLOYMENT', value: modelDeployment }
          { name: 'GT_TEST_AZURE_DEPENDENCIES', value: 'true' }
          { name: 'PYTHONUNBUFFERED', value: '1' }
        ]
      }]
    }
  }
  dependsOn: [hubAccess]
}
resource blobProbe 'Microsoft.App/jobs@2024-03-01' = {
  name: 'validate-blob'
  location: location
  identity: { type: 'UserAssigned', userAssignedIdentities: { '${apiIdentity.id}': {} } }
  properties: {
    environmentId: environment.id
    workloadProfileName: 'Consumption'
    configuration: {
      triggerType: 'Manual'
      replicaTimeout: 600
      replicaRetryLimit: 0
      manualTriggerConfig: { parallelism: 1, replicaCompletionCount: 1 }
      registries: [{ server: registry.properties.loginServer, identity: apiIdentity.id }]
    }
    template: {
      containers: [{
        name: 'job'
        image: validationImage
        command: ['pytest']
        args: concat(probeArgs, ['blob'])
        resources: { cpu: 1, memory: '2Gi' }
        env: [
          { name: 'AZURE_CLIENT_ID', value: apiIdentity.properties.clientId }
          { name: 'AZURE_CLOUD', value: 'commercial' }
          { name: 'GT_TENANT_ID', value: tenantId }
          { name: 'GT_BLOB_URL', value: storage.properties.primaryEndpoints.blob }
          { name: 'GT_BLOB_CONTAINER', value: 'assets' }
          { name: 'GT_TEST_AZURE_DEPENDENCIES', value: 'true' }
          { name: 'PYTHONUNBUFFERED', value: '1' }
        ]
      }]
    }
  }
}

output jobNames array = [dependencyProbe.name, blobProbe.name]
output taskHub string = hub.name
