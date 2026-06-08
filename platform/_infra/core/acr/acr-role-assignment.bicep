param principalID string
param roleDefinitionID string
param containerRegistryName string

@description('Optional suffix to make role assignment name unique across deployments')
param nameSuffix string = ''

resource containerRegistry 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = {
  name: containerRegistryName
}

// Allow access from AKS to Container Registry using a managed identity
// Role assignment name is deterministic based on scope, principal, and role to ensure idempotency
resource acrRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: empty(nameSuffix) ? guid(containerRegistry.id, principalID, roleDefinitionID) : guid(containerRegistry.id, principalID, roleDefinitionID, nameSuffix)
  scope: containerRegistry
  properties: {
    roleDefinitionId: resourceId('Microsoft.Authorization/roleDefinitions', roleDefinitionID)
    principalId: principalID
    principalType: 'ServicePrincipal'
  }
}

output roleAssignmentName string = acrRoleAssignment.name
