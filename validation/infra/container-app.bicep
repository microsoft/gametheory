// One long-lived app for a fault scenario: an API with public, Entra-protected ingress that
// scales to zero, or a planning worker that keeps one replica polling its own database.
param name string
param location string = resourceGroup().location
param environmentId string
param identityId string
param registryServer string
param image string
param env array
param api bool

resource app 'Microsoft.App/containerApps@2024-03-01' = {
  name: name
  location: location
  identity: { type: 'UserAssigned', userAssignedIdentities: { '${identityId}': {} } }
  properties: {
    managedEnvironmentId: environmentId
    workloadProfileName: 'Consumption'
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: api ? { external: true, targetPort: 8000, transport: 'auto', allowInsecure: false } : null
      registries: [{ server: registryServer, identity: identityId }]
    }
    template: {
      scale: { minReplicas: api ? 0 : 1, maxReplicas: 1 }
      containers: [{
        name: api ? 'api' : 'planner'
        image: image
        resources: { cpu: json('0.5'), memory: '1Gi' }
        env: env
      }]
    }
  }
}

var fqdn = app.properties.configuration.?ingress.?fqdn ?? ''
output name string = app.name
output url string = empty(fqdn) ? '' : 'https://${fqdn}'
