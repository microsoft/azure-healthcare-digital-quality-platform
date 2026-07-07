@description('Name of the Azure SQL logical server.')
param serverName string

@description('Name of the Azure SQL database.')
param databaseName string

@description('Azure region for the SQL resources.')
param location string = resourceGroup().location

param tags object = {}

@description('SQL administrator login used for break-glass provisioning. Runtime services use managed identity.')
param administratorLogin string

@secure()
@minLength(16)
@description('SQL administrator password used for break-glass provisioning. Runtime services use managed identity. Provide via AZURE_SQL_ADMINISTRATOR_PASSWORD or a Key Vault-backed azd environment value.')
param administratorPassword string

@description('Public network access for the logical server. Disable when private endpoint networking is enabled.')
@allowed([
  'Enabled'
  'Disabled'
])
param publicNetworkAccess string = 'Disabled'

@description('Optional developer IP address for public SQL access during local development.')
param developerIpAddress string = ''

@description('Azure SQL Database SKU name.')
param skuName string = 'Basic'

@description('Azure SQL Database SKU tier.')
param skuTier string = 'Basic'

@description('Object ID of the Microsoft Entra administrator for the SQL server. Required to run managed-identity migrations.')
param entraAdminObjectId string = ''

@description('Login/display name of the Microsoft Entra administrator for the SQL server.')
param entraAdminLogin string = ''

@description('Tenant ID for the Microsoft Entra administrator.')
param entraAdminTenantId string = tenant().tenantId

@description('When true, disables SQL password authentication after the Entra administrator is configured.')
param azureAdOnlyAuthentication bool = false

resource sqlServer 'Microsoft.Sql/servers@2023-08-01-preview' = {
  name: serverName
  location: location
  tags: tags
  properties: {
    administratorLogin: administratorLogin
    administratorLoginPassword: administratorPassword
    minimalTlsVersion: '1.2'
    publicNetworkAccess: publicNetworkAccess
    restrictOutboundNetworkAccess: 'Disabled'
  }
}

resource sqlDatabase 'Microsoft.Sql/servers/databases@2023-08-01-preview' = {
  parent: sqlServer
  name: databaseName
  location: location
  tags: tags
  sku: {
    name: skuName
    tier: skuTier
  }
  properties: {
    collation: 'SQL_Latin1_General_CP1_CI_AS'
    zoneRedundant: false
  }
}

resource sqlEntraAdmin 'Microsoft.Sql/servers/administrators@2023-08-01-preview' = if (!empty(entraAdminObjectId) && !empty(entraAdminLogin)) {
  parent: sqlServer
  name: 'ActiveDirectory'
  properties: {
    administratorType: 'ActiveDirectory'
    login: entraAdminLogin
    sid: entraAdminObjectId
    tenantId: entraAdminTenantId
  }
}

resource aadOnlyAuth 'Microsoft.Sql/servers/azureADOnlyAuthentications@2023-08-01-preview' = if (azureAdOnlyAuthentication && !empty(entraAdminObjectId) && !empty(entraAdminLogin)) {
  parent: sqlServer
  name: 'Default'
  properties: {
    azureADOnlyAuthentication: true
  }
  dependsOn: [
    sqlEntraAdmin
  ]
}

resource developerFirewallRule 'Microsoft.Sql/servers/firewallRules@2023-08-01-preview' = if (publicNetworkAccess == 'Enabled' && !empty(developerIpAddress)) {
  parent: sqlServer
  name: 'developer-ip'
  properties: {
    startIpAddress: developerIpAddress
    endIpAddress: developerIpAddress
  }
}

output serverName string = sqlServer.name
output databaseName string = sqlDatabase.name
output fullyQualifiedDomainName string = sqlServer.properties.fullyQualifiedDomainName
