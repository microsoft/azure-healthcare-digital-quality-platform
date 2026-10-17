targetScope = 'subscription'

metadata description = '''
Entry point for the headless Submitters App Service deployment.

Creates a dedicated resource group and deploys the self-contained App Service
solution in resources.bicep. Designed for `azd up` from submitters/appservice.
'''

@minLength(1)
@maxLength(64)
@description('Name of the environment; used to derive the resource group and a short unique resource token.')
param environmentName string

@minLength(1)
@description('Primary location for all resources.')
@allowed([
  'australiaeast'
  'eastasia'
  'eastus'
  'eastus2'
  'centralus'
  'northeurope'
  'southcentralus'
  'southeastasia'
  'swedencentral'
  'uksouth'
  'westus2'
  'westus3'
])
@metadata({
  azd: {
    type: 'location'
  }
})
param location string

@description('Backend DEVELOPMENT_MODE flag ("false" keeps Entra ID auth enforced).')
param developmentMode string = 'false'

@description('Base URL of an external Digital Quality Orchestrator, if the customer has one. Leave empty for the core submitter API only.')
param orchestratorBaseUrl string = ''

@description('Cosmos SQL database name.')
param cosmosDatabaseName string = 'dq'

@description('App Service Plan SKU name (e.g. B2, S1, P1v3).')
param appServicePlanSkuName string = 'B2'

@description('Optional object ID of the deploying user/admin, granted Cosmos data-plane access for local debugging. azd supplies AZURE_PRINCIPAL_ID.')
param developerPrincipalId string = ''

@description('Optional explicit resource group name. Defaults to rg-{environmentName}.')
param resourceGroupName string = ''

var tags = {
  'azd-env-name': environmentName
}
var resourceToken = toLower(uniqueString(subscription().id, environmentName, location))

resource rg 'Microsoft.Resources/resourceGroups@2021-04-01' = {
  name: !empty(resourceGroupName) ? resourceGroupName : 'rg-${environmentName}'
  location: location
  tags: tags
}

module resources 'resources.bicep' = {
  name: 'submitter-appservice'
  scope: rg
  params: {
    location: location
    tags: tags
    resourceToken: resourceToken
    serviceName: 'submitter'
    developmentMode: developmentMode
    orchestratorBaseUrl: orchestratorBaseUrl
    cosmosDatabaseName: cosmosDatabaseName
    appServicePlanSkuName: appServicePlanSkuName
    developerPrincipalId: developerPrincipalId
  }
}

output AZURE_LOCATION string = location
output AZURE_TENANT_ID string = tenant().tenantId
output AZURE_RESOURCE_GROUP string = rg.name
output SERVICE_SUBMITTER_NAME string = resources.outputs.submitterName
output SERVICE_SUBMITTER_URI string = resources.outputs.submitterUri
output SUBMITTER_API_URL string = resources.outputs.submitterUri
output COSMOSDB_ENDPOINT string = resources.outputs.cosmosEndpoint
output APPLICATIONINSIGHTS_CONNECTION_STRING string = resources.outputs.appInsightsConnectionString
