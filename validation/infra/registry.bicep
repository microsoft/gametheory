// Created by the preprovision hook before the main deployment so images can be imported first.
// Name and settings match infra/main.bicep, which then adopts this registry unchanged.
param location string = resourceGroup().location

resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: 'gt${uniqueString(resourceGroup().id)}'
  location: location
  sku: { name: 'Basic' }
  properties: { adminUserEnabled: false }
}

output name string = registry.name
output loginServer string = registry.properties.loginServer
