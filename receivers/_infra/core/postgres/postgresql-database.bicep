@description('Name of the Azure Database for PostgreSQL Flexible Server.')
param serverName string

@description('Name of the PostgreSQL database.')
param databaseName string

@description('Azure region for the PostgreSQL resources.')
param location string = resourceGroup().location

param tags object = {}

@description('PostgreSQL administrator login.')
param administratorLogin string

@secure()
@minLength(16)
@description('PostgreSQL administrator password. Provide it through a secure azd environment value.')
param administratorPassword string

@description('Public network access. Disable when private endpoint networking is enabled.')
@allowed([
  'Enabled'
  'Disabled'
])
param publicNetworkAccess string = 'Disabled'

@description('Optional developer IP address for direct PostgreSQL access.')
param developerIpAddress string = ''

@description('Flexible Server compute SKU name.')
param skuName string = 'Standard_B1ms'

@description('Flexible Server compute tier.')
@allowed([
  'Burstable'
  'GeneralPurpose'
  'MemoryOptimized'
])
param skuTier string = 'Burstable'

@minValue(32)
@description('Allocated PostgreSQL storage in GiB.')
param storageSizeGB int = 32

resource postgresServer 'Microsoft.DBforPostgreSQL/flexibleServers@2024-08-01' = {
  name: serverName
  location: location
  tags: tags
  sku: {
    name: skuName
    tier: skuTier
  }
  properties: {
    administratorLogin: administratorLogin
    administratorLoginPassword: administratorPassword
    version: '16'
    backup: {
      backupRetentionDays: 7
      geoRedundantBackup: 'Disabled'
    }
    highAvailability: {
      mode: 'Disabled'
    }
    network: {
      publicNetworkAccess: publicNetworkAccess
    }
    storage: {
      autoGrow: 'Enabled'
      storageSizeGB: storageSizeGB
    }
  }
}

resource postgresDatabase 'Microsoft.DBforPostgreSQL/flexibleServers/databases@2024-08-01' = {
  parent: postgresServer
  name: databaseName
  properties: {
    charset: 'UTF8'
    collation: 'en_US.utf8'
  }
}

resource allowAzureServices 'Microsoft.DBforPostgreSQL/flexibleServers/firewallRules@2024-08-01' = if (publicNetworkAccess == 'Enabled') {
  parent: postgresServer
  name: 'AllowAllAzureServicesAndResourcesWithinAzureIps'
  properties: {
    startIpAddress: '0.0.0.0'
    endIpAddress: '0.0.0.0'
  }
}

resource developerFirewallRule 'Microsoft.DBforPostgreSQL/flexibleServers/firewallRules@2024-08-01' = if (publicNetworkAccess == 'Enabled' && !empty(developerIpAddress)) {
  parent: postgresServer
  name: 'developer-ip'
  properties: {
    startIpAddress: developerIpAddress
    endIpAddress: developerIpAddress
  }
}

output serverName string = postgresServer.name
output databaseName string = postgresDatabase.name
output fullyQualifiedDomainName string = postgresServer.properties.fullyQualifiedDomainName
