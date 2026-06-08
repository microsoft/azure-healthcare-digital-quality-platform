@description('Name of the Azure Monitor Workspace')
param name string

@description('Location for the Azure Monitor Workspace')
param location string = resourceGroup().location

@description('Tags for the Azure Monitor Workspace')
param tags object = {}

@description('Enable public network access')
@allowed(['Enabled', 'Disabled'])
param publicNetworkAccess string = 'Enabled'

// Azure Monitor Workspace for Prometheus metrics
resource azureMonitorWorkspace 'Microsoft.Monitor/accounts@2023-04-03' = {
  name: name
  location: location
  tags: tags
  properties: {
    publicNetworkAccess: publicNetworkAccess
  }
}

@description('The resource ID of the Azure Monitor Workspace')
output id string = azureMonitorWorkspace.id

@description('The name of the Azure Monitor Workspace')
output name string = azureMonitorWorkspace.name

@description('The Prometheus query endpoint')
output prometheusQueryEndpoint string = azureMonitorWorkspace.properties.metrics.prometheusQueryEndpoint

@description('The default data collection rule resource ID')
output dataCollectionRuleId string = azureMonitorWorkspace.properties.defaultIngestionSettings.dataCollectionRuleResourceId

@description('The default data collection endpoint resource ID')
output dataCollectionEndpointId string = azureMonitorWorkspace.properties.defaultIngestionSettings.dataCollectionEndpointResourceId
