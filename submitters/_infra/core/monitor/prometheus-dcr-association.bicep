@description('Name of the AKS cluster')
param aksClusterName string

@description('Data Collection Rule ID for Prometheus metrics')
param dataCollectionRuleId string

@description('Data Collection Endpoint ID for Prometheus metrics')
param dataCollectionEndpointId string

// Reference to existing AKS cluster
resource aksCluster 'Microsoft.ContainerService/managedClusters@2024-02-01' existing = {
  name: aksClusterName
}

// Data Collection Rule Association for Prometheus metrics
// This links the AKS cluster to the Azure Monitor Workspace's default DCR
resource prometheusDataCollectionRuleAssociation 'Microsoft.Insights/dataCollectionRuleAssociations@2022-06-01' = {
  name: 'configurationAccessEndpoint'
  scope: aksCluster
  properties: {
    dataCollectionEndpointId: dataCollectionEndpointId
  }
}

// DCR Association for Prometheus metrics scraping
resource prometheusDcrAssociation 'Microsoft.Insights/dataCollectionRuleAssociations@2022-06-01' = {
  name: 'ContainerInsightsMetricsExtension'
  scope: aksCluster
  properties: {
    dataCollectionRuleId: dataCollectionRuleId
    description: 'Association of data collection rule for Prometheus metrics'
  }
}

output dataCollectionRuleAssociationName string = prometheusDcrAssociation.name
