@description('Name of the Data Collection Endpoint')
param dataCollectionEndpointName string

@description('Name of the Data Collection Rule')
param dataCollectionRuleName string

@description('Location')
param location string = resourceGroup().location

@description('Tags')
param tags object = {}

@description('Azure Monitor Workspace ID')
param azureMonitorWorkspaceId string

@description('AKS Cluster ID')
param aksClusterId string

// Data Collection Endpoint for Prometheus
resource dataCollectionEndpoint 'Microsoft.Insights/dataCollectionEndpoints@2022-06-01' = {
  name: dataCollectionEndpointName
  location: location
  tags: tags
  kind: 'Linux'
  properties: {
    networkAcls: {
      publicNetworkAccess: 'Enabled'
    }
  }
}

// Data Collection Rule for Prometheus metrics
resource dataCollectionRule 'Microsoft.Insights/dataCollectionRules@2022-06-01' = {
  name: dataCollectionRuleName
  location: location
  tags: tags
  kind: 'Linux'
  properties: {
    dataCollectionEndpointId: dataCollectionEndpoint.id
    dataSources: {
      prometheusForwarder: [
        {
          name: 'PrometheusDataSource'
          streams: [
            'Microsoft-PrometheusMetrics'
          ]
          labelIncludeFilter: {}
        }
      ]
    }
    destinations: {
      monitoringAccounts: [
        {
          name: 'MonitoringAccount'
          accountResourceId: azureMonitorWorkspaceId
        }
      ]
    }
    dataFlows: [
      {
        streams: [
          'Microsoft-PrometheusMetrics'
        ]
        destinations: [
          'MonitoringAccount'
        ]
      }
    ]
  }
}

// Data Collection Rule Association with AKS
resource dataCollectionRuleAssociation 'Microsoft.Insights/dataCollectionRuleAssociations@2022-06-01' = {
  name: 'ContainerInsightsMetricsExtension'
  scope: aksCluster
  properties: {
    dataCollectionRuleId: dataCollectionRule.id
    description: 'Association of data collection rule for Prometheus metrics'
  }
}

// Reference to existing AKS cluster for association
resource aksCluster 'Microsoft.ContainerService/managedClusters@2024-02-01' existing = {
  name: last(split(aksClusterId, '/'))
}

output dataCollectionEndpointId string = dataCollectionEndpoint.id
output dataCollectionRuleId string = dataCollectionRule.id
