param name string
param location string
param targetId string
param groupId string
param dnsZoneName string
param virtualNetworkId string
param subnetId string

resource zone 'Microsoft.Network/privateDnsZones@2024-06-01' = {
  name: dnsZoneName
  location: 'global'
}
resource link 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = {
  parent: zone
  name: name
  location: 'global'
  properties: {
    virtualNetwork: { id: virtualNetworkId }
    registrationEnabled: false
  }
}
resource endpoint 'Microsoft.Network/privateEndpoints@2024-05-01' = {
  name: name
  location: location
  properties: {
    subnet: { id: subnetId }
    privateLinkServiceConnections: [{
      name: name
      properties: {
        privateLinkServiceId: targetId
        groupIds: [groupId]
      }
    }]
  }
}
resource zoneGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-05-01' = {
  parent: endpoint
  name: 'default'
  properties: {
    privateDnsZoneConfigs: [{
      name: 'default'
      properties: { privateDnsZoneId: zone.id }
    }]
  }
}
