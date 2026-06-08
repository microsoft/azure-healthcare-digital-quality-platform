@description('Name of the resource to grant access to')
param resourceName string

@description('Type of resource for role assignment scope')
@allowed(['logAnalytics', 'aks', 'appInsights', 'azureMonitorWorkspace'])
param resourceType string

@description('Role Definition ID to assign')
param roleDefinitionID string

@description('Principal ID to grant access to')
param principalID string

@description('Principal type for role assignment')
@allowed(['ServicePrincipal', 'User', 'Group'])
param principalType string = 'ServicePrincipal'

// Reference existing Log Analytics Workspace
resource logAnalyticsWorkspace 'Microsoft.OperationalInsights/workspaces@2022-10-01' existing = if (resourceType == 'logAnalytics') {
  name: resourceName
}

// Reference existing AKS cluster
resource aksCluster 'Microsoft.ContainerService/managedClusters@2024-02-01' existing = if (resourceType == 'aks') {
  name: resourceName
}

// Reference existing Application Insights
resource appInsights 'Microsoft.Insights/components@2020-02-02' existing = if (resourceType == 'appInsights') {
  name: resourceName
}

// Reference existing Azure Monitor Workspace (for Prometheus)
resource azureMonitorWorkspace 'Microsoft.Monitor/accounts@2023-04-03' existing = if (resourceType == 'azureMonitorWorkspace') {
  name: resourceName
}

// Role assignment for Log Analytics Workspace
resource logAnalyticsRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (resourceType == 'logAnalytics') {
  name: guid(logAnalyticsWorkspace.id, principalID, roleDefinitionID)
  scope: logAnalyticsWorkspace
  properties: {
    principalId: principalID
    roleDefinitionId: resourceId('Microsoft.Authorization/roleDefinitions', roleDefinitionID)
    principalType: principalType
  }
}

// Role assignment for AKS cluster
resource aksRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (resourceType == 'aks') {
  name: guid(aksCluster.id, principalID, roleDefinitionID)
  scope: aksCluster
  properties: {
    principalId: principalID
    roleDefinitionId: resourceId('Microsoft.Authorization/roleDefinitions', roleDefinitionID)
    principalType: principalType
  }
}

// Role assignment for Application Insights
resource appInsightsRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (resourceType == 'appInsights') {
  name: guid(appInsights.id, principalID, roleDefinitionID)
  scope: appInsights
  properties: {
    principalId: principalID
    roleDefinitionId: resourceId('Microsoft.Authorization/roleDefinitions', roleDefinitionID)
    principalType: principalType
  }
}

// Role assignment for Azure Monitor Workspace
resource azureMonitorWorkspaceRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (resourceType == 'azureMonitorWorkspace') {
  name: guid(azureMonitorWorkspace.id, principalID, roleDefinitionID)
  scope: azureMonitorWorkspace
  properties: {
    principalId: principalID
    roleDefinitionId: resourceId('Microsoft.Authorization/roleDefinitions', roleDefinitionID)
    principalType: principalType
  }
}
