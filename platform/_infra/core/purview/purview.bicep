metadata description = 'Create a Microsoft Purview account for data governance and compliance.'

param name string
param location string = resourceGroup().location
param tags object = {}

@description('Managed identity type for the Purview account')
@allowed(['SystemAssigned', 'UserAssigned', 'SystemAssigned,UserAssigned'])
param managedIdentityType string = 'SystemAssigned'

@description('Public network access configuration')
@allowed(['Enabled', 'Disabled'])
param publicNetworkAccess string = 'Disabled'

@description('Managed resource group name for Purview managed resources')
param managedResourceGroupName string = ''

resource purviewAccount 'Microsoft.Purview/accounts@2021-12-01' = {
  name: name
  location: location
  tags: tags
  identity: {
    type: managedIdentityType
  }
  properties: {
    publicNetworkAccess: publicNetworkAccess
    managedResourceGroupName: !empty(managedResourceGroupName) ? managedResourceGroupName : '${name}-managed-rg'
  }
}

output id string = purviewAccount.id
output name string = purviewAccount.name
output endpoint string = 'https://${purviewAccount.name}.purview.azure.com'
output catalogEndpoint string = purviewAccount.properties.endpoints.catalog
output scanEndpoint string = purviewAccount.properties.endpoints.scan
output principalId string = purviewAccount.identity.principalId
output managedResourceGroupName string = purviewAccount.properties.managedResources.resourceGroup
