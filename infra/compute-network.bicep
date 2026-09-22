param location string = resourceGroup().location
@minLength(3)
@maxLength(12)
param namePrefix string = 'gametheory'

var stem = '${namePrefix}-${uniqueString(resourceGroup().id)}'

resource network 'Microsoft.Network/virtualNetworks@2024-05-01' = {
  name: 'vnet-${stem}'
  location: location
  properties: {
    addressSpace: { addressPrefixes: ['10.46.0.0/16'] }
    subnets: [
      {
        name: 'web'
        properties: {
          addressPrefix: '10.46.0.0/24'
          delegations: [{ name: 'web', properties: { serviceName: 'Microsoft.Web/serverFarms' } }]
        }
      }
      {
        name: 'workers'
        properties: {
          addressPrefix: '10.46.2.0/23'
          delegations: [{ name: 'containers', properties: { serviceName: 'Microsoft.App/environments' } }]
        }
      }
      {
        name: 'private'
        properties: { addressPrefix: '10.46.4.0/24', privateEndpointNetworkPolicies: 'Disabled' }
      }
    ]
  }
}
resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: 'logs-${stem}'
  location: location
  properties: { retentionInDays: 30, sku: { name: 'PerGB2018' } }
}
resource containerEnvironment 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: 'cae-${stem}'
  location: location
  properties: {
    vnetConfiguration: { infrastructureSubnetId: '${network.id}/subnets/workers' }
    workloadProfiles: [{ name: 'Consumption', workloadProfileType: 'Consumption' }]
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logs.properties.customerId
        sharedKey: logs.listKeys().primarySharedKey
      }
    }
  }
}
output virtualNetworkId string = network.id
output webSubnetId string = '${network.id}/subnets/web'
output privateSubnetId string = '${network.id}/subnets/private'
output containerEnvironmentId string = containerEnvironment.id
