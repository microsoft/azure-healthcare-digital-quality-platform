@description('Name of the Azure Managed Grafana instance')
param grafanaName string

@description('Location for the Grafana resource')
param location string = resourceGroup().location

@description('Tags for the resource')
param tags object = {}

@description('SKU name for Grafana - Standard or Essential')
@allowed(['Standard', 'Essential'])
param skuName string = 'Standard'

@description('Enable public network access')
@allowed(['Enabled', 'Disabled'])
param publicNetworkAccess string = 'Enabled'

@description('Enable zone redundancy')
@allowed(['Enabled', 'Disabled'])
param zoneRedundancy string = 'Disabled'

@description('Enable API key authentication')
@allowed(['Enabled', 'Disabled'])
param apiKeyEnabled string = 'Disabled'

@description('Enable deterministic outbound IP')
@allowed(['Enabled', 'Disabled'])
param deterministicOutboundIP string = 'Disabled'

@description('Enable system assigned managed identity')
param enableSystemAssignedIdentity bool = true

@description('Azure Monitor Workspace Resource ID for Prometheus integration')
param azureMonitorWorkspaceId string = ''

// Azure Managed Grafana resource
// Note: Grafana connects to Azure Monitor data sources (Log Analytics, App Insights) via RBAC
// The grafanaIntegrations property requires Azure Monitor Workspace (Microsoft.Monitor/accounts), not Log Analytics
resource grafana 'Microsoft.Dashboard/grafana@2024-10-01' = {
  name: grafanaName
  location: location
  tags: tags
  sku: {
    name: skuName
  }
  identity: {
    type: enableSystemAssignedIdentity ? 'SystemAssigned' : 'None'
  }
  properties: {
    publicNetworkAccess: publicNetworkAccess
    zoneRedundancy: zoneRedundancy
    apiKey: apiKeyEnabled
    deterministicOutboundIP: deterministicOutboundIP
    grafanaIntegrations: !empty(azureMonitorWorkspaceId) ? {
      azureMonitorWorkspaceIntegrations: [
        {
          azureMonitorWorkspaceResourceId: azureMonitorWorkspaceId
        }
      ]
    } : null
    grafanaConfigurations: {
      users: {
        viewersCanEdit: true
      }
    }
  }
}

@description('The resource ID of the Grafana instance')
output id string = grafana.id

@description('The name of the Grafana instance')
output name string = grafana.name

@description('The endpoint URL of the Grafana instance')
output endpoint string = grafana.properties.endpoint

@description('The principal ID of the system-assigned managed identity')
output principalId string = enableSystemAssignedIdentity ? grafana.identity.principalId : ''
