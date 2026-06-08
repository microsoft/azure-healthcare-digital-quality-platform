metadata description = 'Create an Azure Cosmos DB account.'

param name string
param location string = resourceGroup().location
param tags object = {}

@allowed(['GlobalDocumentDB'])
@description('Sets the kind of account.')
param kind string = 'GlobalDocumentDB'

@description('Enables serverless for this account. Defaults to false.')
param enableServerless bool = true

@description('Enables NoSQL vector search for this account. Defaults to false.')
param enableNoSQLVectorSearch bool = false

@description('Enables NoSQL full text search for this account. Defaults to false.')
param enableNoSQLFullTextSearch bool = false

@description('Disables key-based authentication. Defaults to false.')
param disableKeyBasedAuth bool = false

@description('IP addresses or CIDR ranges allowed to access the account (for developer access).')
param ipRules array = []

resource account 'Microsoft.DocumentDB/databaseAccounts@2024-05-15' = {
  name: name
  location: location
  tags: tags
  kind: kind
  properties: {
    consistencyPolicy: {
      defaultConsistencyLevel: 'Session'
    }
    databaseAccountOfferType: 'Standard'
    locations: [
      {
        locationName: location
        failoverPriority: 0
        isZoneRedundant: false
      }
    ]
    enableAutomaticFailover: false
    enableMultipleWriteLocations: false
    disableLocalAuth: disableKeyBasedAuth
    // Allow access from Azure Portal and specified IP addresses
    ipRules: [for ip in ipRules: {
      ipAddressOrRange: ip
    }]
    publicNetworkAccess: empty(ipRules) ? 'Disabled' : 'SecuredByPerimeter'
    capabilities: union(
      (enableServerless)
        ? [
            {
              name: 'EnableServerless'
            }
          ]
        : [],
      (enableNoSQLVectorSearch)
        ? [
            {
              name: 'EnableNoSQLVectorSearch'
            }
          ]
        : [],
      (enableNoSQLFullTextSearch)
        ? [
            {
              name: 'EnableNoSQLFullTextSearch'
            }
          ]
        : []  
    )
  }
}

output endpoint string = account.properties.documentEndpoint
output name string = account.name
