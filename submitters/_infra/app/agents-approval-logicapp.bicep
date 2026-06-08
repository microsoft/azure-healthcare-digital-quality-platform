// ============================================================================
// Agents Approval Logic App Infrastructure
// Deploys Azure Logic App for Teams-based approval workflow
// ============================================================================

@description('The name of the Logic App')
param logicAppName string

@description('The location for all resources')
param location string = resourceGroup().location

@description('Tags to apply to all resources')
param tags object = {}

@description('The CosmosDB account name for audit logging')
param cosmosDbAccountName string

@description('The CosmosDB database name')
param cosmosDbDatabaseName string = 'dq'

@description('The CosmosDB container name for approvals')
param cosmosDbContainerName string = 'approvals'

@description('The Teams channel ID for approval notifications')
param teamsChannelId string = ''

@description('The Teams group/team ID for approval notifications')
param teamsGroupId string = ''

@description('Approval timeout in hours')
param approvalTimeoutHours int = 2

@description('The user-assigned managed identity ID')
param userAssignedIdentityId string = ''

// ============================================================================
// Existing Resources
// ============================================================================

resource cosmosDbAccount 'Microsoft.DocumentDB/databaseAccounts@2024-05-15' existing = {
  name: cosmosDbAccountName
}

// Cosmos DB Built-in Data Contributor role
var CosmosDBDataContributor = '00000000-0000-0000-0000-000000000002'

// ============================================================================
// API Connections
// ============================================================================

var cosmosConnectionName = '${logicAppName}-cosmos'
var teamsConnectionName = '${logicAppName}-teams'

// CosmosDB connection - Standard V1 connection (requires manual authentication or access key)
// For managed identity, the Logic App uses its identity directly when accessing Cosmos DB
resource cosmosDbConnection 'Microsoft.Web/connections@2016-06-01' = {
  name: cosmosConnectionName
  location: location
  tags: tags
  properties: {
    displayName: 'CosmosDB Connection for Approvals'
    api: {
      id: subscriptionResourceId('Microsoft.Web/locations/managedApis', location, 'documentdb')
    }
    parameterValues: {
      databaseAccount: cosmosDbAccountName
      accessKey: cosmosDbAccount.listKeys().primaryMasterKey
    }
  }
}

resource teamsConnection 'Microsoft.Web/connections@2016-06-01' = {
  name: teamsConnectionName
  location: location
  tags: tags
  properties: {
    displayName: 'Teams Connection for Approvals'
    api: {
      id: subscriptionResourceId('Microsoft.Web/locations/managedApis', location, 'teams')
    }
  }
}

// ============================================================================
// Logic App Workflow
// ============================================================================

resource logicApp 'Microsoft.Logic/workflows@2019-05-01' = {
  name: logicAppName
  location: location
  tags: union(tags, {
    'azd-service-name': 'agents-approval-workflow'
  })
  identity: empty(userAssignedIdentityId) ? {
    type: 'SystemAssigned'
  } : {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${userAssignedIdentityId}': {}
    }
  }
  properties: {
    state: 'Enabled'
    definition: {
      '$schema': 'https://schema.management.azure.com/providers/Microsoft.Logic/schemas/2016-06-01/workflowdefinition.json#'
      contentVersion: '1.0.0.0'
      parameters: {
        '$connections': {
          defaultValue: {}
          type: 'Object'
        }
        teamsChannelId: {
          defaultValue: teamsChannelId
          type: 'String'
        }
        teamsGroupId: {
          defaultValue: teamsGroupId
          type: 'String'
        }
        approvalTimeoutHours: {
          defaultValue: approvalTimeoutHours
          type: 'Int'
        }
        cosmosDbEndpoint: {
          defaultValue: 'https://${cosmosDbAccountName}.documents.azure.com'
          type: 'String'
        }
      }
      triggers: {
        When_an_HTTP_request_is_received: {
          type: 'Request'
          kind: 'Http'
          inputs: {
            schema: {
              type: 'object'
              properties: {
                approval_id: { type: 'string' }
                task: { type: 'string' }
                environment: { type: 'string' }
                cluster: { type: 'string' }
                namespace: { type: 'string' }
                image_tags: { type: 'array' }
                commit_sha: { type: 'string' }
                requested_by: { type: 'string' }
                callback_url: { type: 'string' }
              }
              required: ['approval_id', 'task', 'environment', 'cluster', 'callback_url']
            }
          }
        }
      }
      actions: {
        Initialize_Approval_Record: {
          type: 'InitializeVariable'
          runAfter: {}
          inputs: {
            variables: [
              {
                name: 'approvalRecord'
                type: 'object'
                value: {
                  approval_id: '@{triggerBody()?[\'approval_id\']}'
                  task: '@{triggerBody()?[\'task\']}'
                  environment: '@{triggerBody()?[\'environment\']}'
                  status: 'pending'
                  created_at: '@{utcNow()}'
                }
              }
            ]
          }
        }
        Start_Teams_Approval: {
          type: 'ApiConnectionWebhook'
          runAfter: {
            Initialize_Approval_Record: ['Succeeded']
          }
          inputs: {
            host: {
              connection: {
                name: '@parameters(\'$connections\')[\'teams\'][\'connectionId\']'
              }
            }
            body: {
              notificationUrl: '@{listCallbackUrl()}'
              message: {
                title: 'CI/CD Deployment Approval - @{triggerBody()?[\'environment\']}'
                details: 'Deployment to @{triggerBody()?[\'cluster\']} requested'
              }
              approvalType: 'CustomResponse'
              customResponses: [
                {
                  response: 'Approve'
                  comment: { isOptional: true }
                }
                {
                  response: 'Reject'
                  comment: { isOptional: false }
                }
              ]
            }
            path: '/v2/approvals/create'
          }
          limit: {
            timeout: 'PT@{parameters(\'approvalTimeoutHours\')}H'
          }
        }
        Notify_Agent_Of_Decision: {
          type: 'Http'
          runAfter: {
            Start_Teams_Approval: ['Succeeded']
          }
          inputs: {
            method: 'POST'
            uri: '@{triggerBody()?[\'callback_url\']}'
            body: {
              approval_id: '@{triggerBody()?[\'approval_id\']}'
              decision: '@{body(\'Start_Teams_Approval\')?[\'outcome\']}'
              approved_by: '@{body(\'Start_Teams_Approval\')?[\'responder\']?[\'displayName\']}'
              timestamp: '@{utcNow()}'
            }
            headers: {
              'Content-Type': 'application/json'
            }
          }
        }
        Response: {
          type: 'Response'
          runAfter: {
            Notify_Agent_Of_Decision: ['Succeeded']
          }
          inputs: {
            statusCode: 200
            body: '@variables(\'approvalRecord\')'
          }
        }
      }
    }
    parameters: {
      '$connections': {
        value: {
          documentdb: {
            connectionId: cosmosDbConnection.id
            connectionName: 'documentdb'
            id: subscriptionResourceId('Microsoft.Web/locations/managedApis', location, 'documentdb')
          }
          teams: {
            connectionId: teamsConnection.id
            connectionName: 'teams'
            id: subscriptionResourceId('Microsoft.Web/locations/managedApis', location, 'teams')
          }
        }
      }
    }
  }
}

// ============================================================================
// CosmosDB Role Assignment for Logic App Managed Identity
// ============================================================================

// Assign Cosmos DB Data Contributor role to Logic App's managed identity
resource cosmosRoleAssignmentLogicApp 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-05-15' = {
  name: guid(cosmosDbAccount.id, logicApp.id, CosmosDBDataContributor, 'logicapp')
  parent: cosmosDbAccount
  properties: {
    principalId: logicApp.identity.principalId
    roleDefinitionId: '${cosmosDbAccount.id}/sqlRoleDefinitions/${CosmosDBDataContributor}'
    scope: cosmosDbAccount.id
  }
}

// ============================================================================
// CosmosDB Container for Approvals
// ============================================================================

resource cosmosDatabase 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases@2024-05-15' existing = {
  parent: cosmosDbAccount
  name: cosmosDbDatabaseName
}

resource approvalsContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2023-04-15' = {
  parent: cosmosDatabase
  name: cosmosDbContainerName
  properties: {
    resource: {
      id: cosmosDbContainerName
      partitionKey: {
        paths: ['/environment']
        kind: 'Hash'
      }
      indexingPolicy: {
        indexingMode: 'consistent'
        includedPaths: [
          { path: '/*' }
        ]
        excludedPaths: [
          { path: '/"_etag"/?' }
        ]
      }
      defaultTtl: -1 // No automatic expiration for audit data
    }
  }
}

// ============================================================================
// Outputs
// ============================================================================

@description('The Logic App trigger URL for approval requests')
#disable-next-line outputs-should-not-contain-secrets
output logicAppTriggerUrl string = listCallbackUrl('${logicApp.id}/triggers/When_an_HTTP_request_is_received', '2019-05-01').value

@description('The Logic App resource ID')
output logicAppId string = logicApp.id

@description('The Logic App name')
output logicAppName string = logicApp.name

@description('The Logic App managed identity principal ID')
output logicAppPrincipalId string = logicApp.identity.principalId

@description('The approvals container name')
output approvalsContainerName string = approvalsContainer.name
