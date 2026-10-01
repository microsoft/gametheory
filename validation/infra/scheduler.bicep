// A private Consumption Scheduler for test task hubs, reachable through the environment's
// existing Scheduler private DNS zone. Deployment validation counts each declared task hub on top
// of the existing ones against the five-hub Consumption limit, so a Scheduler that a template
// redeclares can hold at most two hubs.
param name string
param location string = resourceGroup().location
param privateSubnetId string
param dnsZoneName string
@description('Task hubs, each with the principal that receives Durable Task Data Contributor on it.')
@maxLength(2)
param hubs array

resource scheduler 'Microsoft.DurableTask/schedulers@2026-02-01' = {
  name: name
  location: location
  properties: {
    sku: { name: 'Consumption' }
    publicNetworkAccess: 'Disabled'
    ipAllowlist: []
  }
}
resource taskHubs 'Microsoft.DurableTask/schedulers/taskHubs@2026-02-01' = [for hub in hubs: {
  parent: scheduler
  name: hub.name
  properties: {}
}]
resource access 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for (hub, index) in hubs: {
  scope: taskHubs[index]
  name: guid(taskHubs[index].id, hub.principalId, 'scheduler')
  properties: {
    principalId: hub.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '0ad04412-c4d5-4796-b79c-f76d14c8d402')
  }
}]
resource zone 'Microsoft.Network/privateDnsZones@2024-06-01' existing = {
  name: dnsZoneName
}
resource endpoint 'Microsoft.Network/privateEndpoints@2024-05-01' = {
  name: 'pe-${name}'
  location: location
  properties: {
    subnet: { id: privateSubnetId }
    privateLinkServiceConnections: [{
      name: name
      properties: { privateLinkServiceId: scheduler.id, groupIds: ['scheduler'] }
    }]
  }
}
resource zoneGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-05-01' = {
  parent: endpoint
  name: 'default'
  properties: {
    privateDnsZoneConfigs: [{ name: 'default', properties: { privateDnsZoneId: zone.id } }]
  }
}

output endpoint string = scheduler.properties.endpoint
