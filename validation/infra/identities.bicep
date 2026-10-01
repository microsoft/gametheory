param location string = resourceGroup().location

var stem = 'gtval-${uniqueString(resourceGroup().id)}'

// SQL administrator for database setup and the restart test. Never attached to an application.
resource operator 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-${stem}-operator'
  location: location
}
// Planning worker identity for the model-denial scenario. It deliberately has no Foundry role.
resource denied 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-${stem}-denied'
  location: location
}

output operatorName string = operator.name
output operatorClientId string = operator.properties.clientId
output deniedName string = denied.name
