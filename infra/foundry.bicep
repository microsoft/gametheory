param name string
param location string
param virtualNetworkId string
param subnetId string
param workerPrincipalId string
@allowed(['Standard', 'DataZoneStandard'])
param modelSku string = 'DataZoneStandard'
param modelName string = 'gpt-5.6-luna'
param modelVersion string = '2026-07-09'
@description('Model capacity units. Confirm subscription quota for the SKU in the region.')
@minValue(1)
param modelCapacity int = 50

resource account 'Microsoft.CognitiveServices/accounts@2025-06-01' = {
  name: name
  location: location
  kind: 'AIServices'
  sku: { name: 'S0' }
  identity: { type: 'SystemAssigned' }
  properties: {
    customSubDomainName: name
    allowProjectManagement: true
    disableLocalAuth: true
    publicNetworkAccess: 'Disabled'
    networkAcls: { defaultAction: 'Deny' }
  }
}
resource project 'Microsoft.CognitiveServices/accounts/projects@2025-06-01' = {
  parent: account
  name: 'scenario-planning'
  location: location
  identity: { type: 'SystemAssigned' }
  properties: {
    displayName: 'Game Theory scenario planning'
    description: 'Stateless planning inference. Authoring records remain in application SQL.'
  }
}
resource model 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = {
  parent: account
  // A deployment cannot change models in place; a new name also avoids stale project routing.
  name: modelName
  sku: { name: modelSku, capacity: modelCapacity }
  properties: {
    model: { format: 'OpenAI', name: modelName, version: modelVersion }
    raiPolicyName: 'Microsoft.DefaultV2'
    versionUpgradeOption: 'OnceCurrentVersionExpired'
  }
  dependsOn: [project]
}
resource access 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: project
  name: guid(project.id, workerPrincipalId, 'foundry-user')
  properties: {
    principalId: workerPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '53ca6127-db72-4b80-b1b0-d745d6d5456d')
  }
}
// Commercial service metadata reports all three zones for the account endpoint.
var zoneNames = [
  'privatelink.cognitiveservices.azure.com'
  'privatelink.openai.azure.com'
  'privatelink.services.ai.azure.com'
]
resource zones 'Microsoft.Network/privateDnsZones@2024-06-01' = [for zone in zoneNames: {
  name: zone
  location: 'global'
}]
resource links 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = [for (zone, i) in zoneNames: {
  name: '${zones[i].name}/${name}'
  location: 'global'
  properties: {
    virtualNetwork: { id: virtualNetworkId }
    registrationEnabled: false
  }
}]
resource endpoint 'Microsoft.Network/privateEndpoints@2024-05-01' = {
  name: 'pe-${name}'
  location: location
  properties: {
    subnet: { id: subnetId }
    privateLinkServiceConnections: [{
      name: name
      properties: { privateLinkServiceId: account.id, groupIds: ['account'] }
    }]
  }
  dependsOn: [model]
}
resource dns 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-05-01' = {
  parent: endpoint
  name: 'default'
  properties: {
    privateDnsZoneConfigs: [for (zone, i) in zoneNames: {
      name: 'zone-${i}'
      properties: { privateDnsZoneId: zones[i].id }
    }]
  }
}
output projectEndpoint string = project.properties.endpoints['AI Foundry API']
output modelDeployment string = model.name
