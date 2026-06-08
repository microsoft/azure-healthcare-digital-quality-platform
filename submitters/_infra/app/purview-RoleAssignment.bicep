@description('Name of the Purview account.')
param purviewAccountName string

@description('Role definition ID to assign (use built-in Azure RBAC roles).')
param roleDefinitionID string

@description('Principal ID to assign the role to.')
param principalID string

@description('Principal type: User, Group, or ServicePrincipal.')
param principalType string = 'ServicePrincipal'

resource purviewAccount 'Microsoft.Purview/accounts@2021-12-01' existing = {
  name: purviewAccountName
}

// Azure RBAC role assignment on Purview account resource
// Common roles for Purview:
// - Purview Data Reader: 4c48d476-69c1-41d0-88c2-9ac66e4b64f4
// - Purview Data Curator: 8a3c2885-9b38-4fd2-9d99-91af537c1347
// - Purview Data Source Administrator: 200bba9e-f0c8-430f-892b-6f0794863803
resource purviewRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(purviewAccount.id, principalID, roleDefinitionID)
  scope: purviewAccount
  properties: {
    principalId: principalID
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleDefinitionID)
    principalType: principalType
  }
}

output roleAssignmentId string = purviewRoleAssignment.id
