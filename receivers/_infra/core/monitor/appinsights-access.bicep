param principalID string
param roleDefinitionID string
param appInsightsName string

@description('Optional suffix to make role assignment name unique across deployments')
param nameSuffix string = ''

resource applicationInsights 'Microsoft.Insights/components@2020-02-02' existing = {
  name: appInsightsName
}

// Allow access from API to app insights using a managed identity and least priv role
// Role assignment name is deterministic based on scope, principal, and role to ensure idempotency
resource appInsightsRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: empty(nameSuffix) ? guid(applicationInsights.id, principalID, roleDefinitionID) : guid(applicationInsights.id, principalID, roleDefinitionID, nameSuffix)
  scope: applicationInsights
  properties: {
    roleDefinitionId: resourceId('Microsoft.Authorization/roleDefinitions', roleDefinitionID)
    principalId: principalID
    principalType: 'ServicePrincipal'
  }
}

output ROLE_ASSIGNMENT_NAME string = appInsightsRoleAssignment.name

