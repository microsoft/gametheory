// Fault scenarios beside the validation studio. Each scenario has its own database and task hub,
// so its deliberate fault never reaches the baseline studio or another scenario.
param location string = resourceGroup().location
@description('Enabled scenario IDs: worker-restart, sql-outage, blob-outage, scheduler-outage, model-denied.')
param scenarios array
param tenantId string
param spaAppId string
param apiAppId string
@description('Signed-in operator registered as the first administrator in every scenario database.')
param principalId string
param organizationName string
param apiImage string
param workerImage string
param validationImage string
param registryName string
param containerEnvironmentId string
param sqlServerName string
param schedulerName string
param blobUrl string
param foundryEndpoint string
param modelDeployment string
param apiIdentityName string
param workerIdentityName string
param operatorIdentityName string
param deniedIdentityName string

var restartOn = contains(scenarios, 'worker-restart')
var sqlOn = contains(scenarios, 'sql-outage')
var blobOn = contains(scenarios, 'blob-outage')
var schedulerOn = contains(scenarios, 'scheduler-outage')
var modelOn = contains(scenarios, 'model-denied')
// Reserved .invalid hosts never resolve, so these faults are deterministic and reach nothing real.
var faultBlobUrl = 'https://storage-unavailable.invalid/'
var faultSchedulerEndpoint = 'https://scheduler-unavailable.invalid'
var acrPull = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d')
var durableTaskContributor = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '0ad04412-c4d5-4796-b79c-f76d14c8d402')

resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = {
  name: registryName
}
resource sql 'Microsoft.Sql/servers@2023-08-01' existing = {
  name: sqlServerName
}
resource scheduler 'Microsoft.DurableTask/schedulers@2026-02-01' existing = {
  name: schedulerName
}
resource apiIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' existing = {
  name: apiIdentityName
}
resource workerIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' existing = {
  name: workerIdentityName
}
resource operator 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' existing = {
  name: operatorIdentityName
}
resource denied 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' existing = {
  name: deniedIdentityName
}

func sqlUrl(host string, database string, clientId string) string =>
  'mssql+pyodbc://${host}/${database}?driver=ODBC+Driver+18+for+SQL+Server&authentication=ActiveDirectoryMsi&Encrypt=yes&UID=${clientId}'

var sqlHost = sql.properties.fullyQualifiedDomainName
var databaseNames = filter([
  restartOn ? 'gametheory_test' : ''
  blobOn ? 'gametheory_blob_outage' : ''
  schedulerOn ? 'gametheory_scheduler_outage' : ''
  modelOn ? 'gametheory_model_denied' : ''
], name => !empty(name))
resource databases 'Microsoft.Sql/servers/databases@2023-08-01' = [for name in databaseNames: {
  parent: sql
  name: name
  location: location
  sku: { name: 'Basic', tier: 'Basic', capacity: 5 }
}]

resource operatorPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: registry
  name: guid(registry.id, operator.id, 'pull')
  properties: {
    principalId: operator.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: acrPull
  }
}
resource deniedPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (modelOn) {
  scope: registry
  name: guid(registry.id, denied.id, 'pull')
  properties: {
    principalId: denied.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: acrPull
  }
}
resource restartHub 'Microsoft.DurableTask/schedulers/taskHubs@2026-02-01' = if (restartOn) {
  parent: scheduler
  name: 'restart-test'
  properties: {}
}
resource restartHubAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (restartOn) {
  scope: restartHub
  name: guid(restartHub.id, operator.id, 'scheduler')
  properties: {
    principalId: operator.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: durableTaskContributor
  }
}
resource schedulerOutageHub 'Microsoft.DurableTask/schedulers/taskHubs@2026-02-01' = if (schedulerOn) {
  parent: scheduler
  name: 'scheduler-outage'
  properties: {}
}
resource schedulerOutageHubAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (schedulerOn) {
  scope: schedulerOutageHub
  name: guid(schedulerOutageHub.id, workerIdentity.id, 'scheduler')
  properties: {
    principalId: workerIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: durableTaskContributor
  }
}
resource modelDeniedHub 'Microsoft.DurableTask/schedulers/taskHubs@2026-02-01' = if (modelOn) {
  parent: scheduler
  name: 'model-denied'
  properties: {}
}
resource modelDeniedHubAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (modelOn) {
  scope: modelDeniedHub
  name: guid(modelDeniedHub.id, denied.id, 'scheduler')
  properties: {
    principalId: denied.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: durableTaskContributor
  }
}

var authSettings = [
  { name: 'AZURE_CLOUD', value: 'commercial' }
  { name: 'GT_TENANT_ID', value: tenantId }
  { name: 'GT_SPA_CLIENT_ID', value: spaAppId }
  { name: 'GT_API_AUDIENCE', value: apiAppId }
  { name: 'GT_API_SCOPE', value: 'api://${apiAppId}/access_as_user' }
  { name: 'GT_RUN_ASSISTANT_ENABLED', value: 'false' }
  { name: 'AZURE_CLIENT_ID', value: apiIdentity.properties.clientId }
  { name: 'GT_BLOB_CONTAINER', value: 'assets' }
]
func planningSettings(endpoint string, taskHub string, foundry string, model string) array => [
  { name: 'GT_PLANNING_ENABLED', value: 'true' }
  { name: 'GT_SCHEDULER_ENDPOINT', value: endpoint }
  { name: 'GT_SCHEDULER_TASKHUB', value: taskHub }
  { name: 'GT_FOUNDRY_PROJECT_ENDPOINT', value: foundry }
  { name: 'GT_MODEL_DEPLOYMENT', value: model }
]
func workerSettings(tenant string, clientId string, databaseUrl string) array => [
  { name: 'AZURE_CLOUD', value: 'commercial' }
  { name: 'GT_TENANT_ID', value: tenant }
  { name: 'GT_RUN_ASSISTANT_ENABLED', value: 'false' }
  { name: 'AZURE_CLIENT_ID', value: clientId }
  { name: 'GT_SQL_URL', value: databaseUrl }
  { name: 'GT_PLANNER_MAX_OUTPUT_TOKENS', value: '6000' }
  { name: 'GT_MODEL_TIMEOUT_SECONDS', value: '120' }
]

// F2: every connection fails because the configured database does not exist.
module sqlOutageApi 'container-app.bicep' = if (sqlOn) {
  name: 'fault-sql-api'
  params: {
    name: 'fault-sql-api'
    location: location
    environmentId: containerEnvironmentId
    identityId: apiIdentity.id
    registryServer: registry.properties.loginServer
    image: apiImage
    api: true
    env: concat(authSettings, [
      { name: 'GT_PLANNING_ENABLED', value: 'false' }
      { name: 'GT_SQL_URL', value: sqlUrl(sqlHost, 'gametheory_unavailable', apiIdentity.properties.clientId) }
      { name: 'GT_BLOB_URL', value: blobUrl }
    ])
  }
}
// F3: a working database with an unreachable Blob endpoint.
module blobOutageApi 'container-app.bicep' = if (blobOn) {
  name: 'fault-blob-api'
  params: {
    name: 'fault-blob-api'
    location: location
    environmentId: containerEnvironmentId
    identityId: apiIdentity.id
    registryServer: registry.properties.loginServer
    image: apiImage
    api: true
    env: concat(authSettings, [
      { name: 'GT_PLANNING_ENABLED', value: 'false' }
      { name: 'GT_SQL_URL', value: sqlUrl(sqlHost, 'gametheory_blob_outage', apiIdentity.properties.clientId) }
      { name: 'GT_BLOB_URL', value: faultBlobUrl }
    ])
  }
  dependsOn: [databases]
}
// F4: a healthy API and a worker whose Scheduler endpoint does not resolve.
module schedulerOutageApi 'container-app.bicep' = if (schedulerOn) {
  name: 'fault-scheduler-api'
  params: {
    name: 'fault-scheduler-api'
    location: location
    environmentId: containerEnvironmentId
    identityId: apiIdentity.id
    registryServer: registry.properties.loginServer
    image: apiImage
    api: true
    env: concat(authSettings, planningSettings(scheduler.properties.endpoint, 'scheduler-outage', foundryEndpoint, modelDeployment), [
      { name: 'GT_SQL_URL', value: sqlUrl(sqlHost, 'gametheory_scheduler_outage', apiIdentity.properties.clientId) }
      { name: 'GT_BLOB_URL', value: blobUrl }
    ])
  }
  dependsOn: [databases]
}
module schedulerOutageWorker 'container-app.bicep' = if (schedulerOn) {
  name: 'fault-scheduler-worker'
  params: {
    name: 'fault-scheduler-worker'
    location: location
    environmentId: containerEnvironmentId
    identityId: workerIdentity.id
    registryServer: registry.properties.loginServer
    image: workerImage
    api: false
    env: concat(
      workerSettings(tenantId, workerIdentity.properties.clientId, sqlUrl(sqlHost, 'gametheory_scheduler_outage', workerIdentity.properties.clientId)),
      planningSettings(faultSchedulerEndpoint, 'scheduler-outage', foundryEndpoint, modelDeployment)
    )
  }
  dependsOn: [databases, schedulerOutageHubAccess]
}
// F5: a healthy API and a worker whose identity has no Foundry role.
module modelDeniedApi 'container-app.bicep' = if (modelOn) {
  name: 'fault-model-api'
  params: {
    name: 'fault-model-api'
    location: location
    environmentId: containerEnvironmentId
    identityId: apiIdentity.id
    registryServer: registry.properties.loginServer
    image: apiImage
    api: true
    env: concat(authSettings, planningSettings(scheduler.properties.endpoint, 'model-denied', foundryEndpoint, modelDeployment), [
      { name: 'GT_SQL_URL', value: sqlUrl(sqlHost, 'gametheory_model_denied', apiIdentity.properties.clientId) }
      { name: 'GT_BLOB_URL', value: blobUrl }
    ])
  }
  dependsOn: [databases]
}
module modelDeniedWorker 'container-app.bicep' = if (modelOn) {
  name: 'fault-model-worker'
  params: {
    name: 'fault-model-worker'
    location: location
    environmentId: containerEnvironmentId
    identityId: denied.id
    registryServer: registry.properties.loginServer
    image: workerImage
    api: false
    env: concat(
      workerSettings(tenantId, denied.properties.clientId, sqlUrl(sqlHost, 'gametheory_model_denied', denied.properties.clientId)),
      planningSettings(scheduler.properties.endpoint, 'model-denied', foundryEndpoint, modelDeployment)
    )
  }
  dependsOn: [databases, deniedPull, modelDeniedHubAccess]
}

// Migrates, grants, and bootstraps every database; safe to rerun after each provision.
var setupDatabases = join(filter([
  'gametheory:${apiIdentity.properties.clientId}:${workerIdentity.properties.clientId}'
  blobOn ? 'gametheory_blob_outage:${apiIdentity.properties.clientId}:${workerIdentity.properties.clientId}' : ''
  schedulerOn ? 'gametheory_scheduler_outage:${apiIdentity.properties.clientId}:${workerIdentity.properties.clientId}' : ''
  modelOn ? 'gametheory_model_denied:${apiIdentity.properties.clientId}:${denied.properties.clientId}' : ''
], item => !empty(item)), ' ')
var setupScript = '''
set -eu
for spec in $SETUP_DATABASES; do
  database=${spec%%:*}
  rest=${spec#*:}
  api=${rest%%:*}
  worker=${rest#*:}
  export GT_SQL_URL="mssql+pyodbc://$SQL_SERVER/$database?driver=ODBC+Driver+18+for+SQL+Server&authentication=ActiveDirectoryMsi&Encrypt=yes&UID=$AZURE_CLIENT_ID"
  echo "Preparing $database"
  alembic -c backend/alembic.ini upgrade head
  gametheory database-grants --api-client-id "$api" --worker-client-id "$worker"
  if output=$(gametheory bootstrap --tenant "$GT_TENANT_ID" --object-id "$ADMIN_OBJECT_ID" --organization-name "$ORGANIZATION_NAME" 2>&1); then
    echo "$output"
  else
    case "$output" in
      *"already registered"*) echo "Administrator already registered in $database" ;;
      *) echo "$output"; exit 1 ;;
    esac
  fi
done
echo "Databases ready"
'''
resource setupJob 'Microsoft.App/jobs@2024-03-01' = {
  name: 'setup-databases'
  location: location
  identity: { type: 'UserAssigned', userAssignedIdentities: { '${operator.id}': {} } }
  properties: {
    environmentId: containerEnvironmentId
    workloadProfileName: 'Consumption'
    configuration: {
      triggerType: 'Manual'
      replicaTimeout: 1200
      replicaRetryLimit: 0
      manualTriggerConfig: { parallelism: 1, replicaCompletionCount: 1 }
      registries: [{ server: registry.properties.loginServer, identity: operator.id }]
    }
    template: {
      containers: [{
        name: 'setup'
        image: apiImage
        command: ['/bin/sh', '-c', setupScript]
        resources: { cpu: 1, memory: '2Gi' }
        env: [
          { name: 'AZURE_CLIENT_ID', value: operator.properties.clientId }
          { name: 'AZURE_CLOUD', value: 'commercial' }
          { name: 'GT_TENANT_ID', value: tenantId }
          { name: 'GT_PLANNING_ENABLED', value: 'false' }
          { name: 'SQL_SERVER', value: sqlHost }
          { name: 'SETUP_DATABASES', value: setupDatabases }
          { name: 'ADMIN_OBJECT_ID', value: principalId }
          { name: 'ORGANIZATION_NAME', value: organizationName }
        ]
      }]
    }
  }
  dependsOn: [databases, operatorPull]
}
// F1: kills a fixture worker mid-activity on the managed Scheduler and expects one result.
resource restartJob 'Microsoft.App/jobs@2024-03-01' = if (restartOn) {
  name: 'test-worker-restart'
  location: location
  identity: { type: 'UserAssigned', userAssignedIdentities: { '${operator.id}': {} } }
  properties: {
    environmentId: containerEnvironmentId
    workloadProfileName: 'Consumption'
    configuration: {
      triggerType: 'Manual'
      replicaTimeout: 2400
      replicaRetryLimit: 0
      manualTriggerConfig: { parallelism: 1, replicaCompletionCount: 1 }
      registries: [{ server: registry.properties.loginServer, identity: operator.id }]
    }
    template: {
      containers: [{
        name: 'job'
        image: validationImage
        command: ['pytest']
        args: ['-q', '--tb=short', '-p', 'no:cacheprovider', '-rA', 'backend/tests/test_scheduler_integration.py', '-k', 'worker_crash']
        resources: { cpu: 1, memory: '2Gi' }
        env: [
          { name: 'AZURE_CLIENT_ID', value: operator.properties.clientId }
          { name: 'AZURE_CLOUD', value: 'commercial' }
          { name: 'GT_TEST_SQL_URL', value: sqlUrl(sqlHost, 'gametheory_test', operator.properties.clientId) }
          { name: 'GT_TEST_SCHEDULER_ENDPOINT', value: scheduler.properties.endpoint }
          { name: 'GT_TEST_SCHEDULER_EMULATOR', value: 'false' }
          { name: 'GT_TEST_SCHEDULER_TASKHUB', value: 'restart-test' }
          { name: 'GT_TEST_RESTART_TIMEOUT', value: '600' }
          { name: 'PYTHONUNBUFFERED', value: '1' }
        ]
      }]
    }
  }
  dependsOn: [databases, operatorPull, restartHubAccess]
}

output setupJobName string = setupJob.name
output restartJobName string = restartOn ? 'test-worker-restart' : ''
output sqlOutageUrl string = sqlOn ? sqlOutageApi!.outputs.url : ''
output blobOutageUrl string = blobOn ? blobOutageApi!.outputs.url : ''
output blobOutageApp string = blobOn ? 'fault-blob-api' : ''
output faultBlobUrl string = faultBlobUrl
output schedulerOutageUrl string = schedulerOn ? schedulerOutageApi!.outputs.url : ''
output schedulerOutageWorker string = schedulerOn ? 'fault-scheduler-worker' : ''
output faultSchedulerEndpoint string = faultSchedulerEndpoint
output healthySchedulerEndpoint string = scheduler.properties.endpoint
output modelDeniedUrl string = modelOn ? modelDeniedApi!.outputs.url : ''
output modelDeniedWorker string = modelOn ? 'fault-model-worker' : ''
