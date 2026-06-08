@description('Name of the Foundry/Cognitive Services account')
param foundryAccountName string

@description('The role definition ID to assign')
param roleDefinitionID string

@description('The principal ID to assign the role to')
param principalID string

@description('Optional suffix to make role assignment name unique across deployments')
param nameSuffix string = ''

resource foundryAccount 'Microsoft.CognitiveServices/accounts@2025-06-01' existing = {
  name: foundryAccountName
}

// Role assignment name is deterministic based on scope, principal, and role to ensure idempotency
resource roleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: empty(nameSuffix) ? guid(foundryAccount.id, principalID, roleDefinitionID) : guid(foundryAccount.id, principalID, roleDefinitionID, nameSuffix)
  scope: foundryAccount
  properties: {
    roleDefinitionId: resourceId('Microsoft.Authorization/roleDefinitions', roleDefinitionID)
    principalId: principalID
    principalType: 'ServicePrincipal'
  }
}
