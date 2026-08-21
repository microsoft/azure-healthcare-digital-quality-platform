@description('Name of the virtual network.')
param virtualNetworkName string

@description('Name of the private-endpoint subnet.')
param subnetName string

@description('Name of the PostgreSQL Flexible Server.')
param serverName string

@description('Azure region for the private endpoint.')
param location string = resourceGroup().location

param tags object = {}

resource vnet 'Microsoft.Network/virtualNetworks@2021-08-01' existing = {
  name: virtualNetworkName
}

resource postgresServer 'Microsoft.DBforPostgreSQL/flexibleServers@2024-08-01' existing = {
  name: serverName
}

var privateDnsZoneName = 'privatelink.postgres.database.azure.com'
var virtualNetworkLinkName = '${serverName}-postgres-link-${take(toLower(uniqueString(serverName, virtualNetworkName)), 4)}'

resource privateDnsZone 'Microsoft.Network/privateDnsZones@2020-06-01' = {
  name: privateDnsZoneName
  location: 'global'
  tags: tags
}

resource privateDnsZoneVirtualNetworkLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2020-06-01' = {
  parent: privateDnsZone
  name: virtualNetworkLinkName
  location: 'global'
  tags: tags
  properties: {
    registrationEnabled: false
    virtualNetwork: {
      id: vnet.id
    }
  }
}

resource privateEndpoint 'Microsoft.Network/privateEndpoints@2021-08-01' = {
  name: '${serverName}-postgres-private-endpoint'
  location: location
  tags: tags
  properties: {
    privateLinkServiceConnections: [
      {
        name: 'postgresPrivateLinkConnection'
        properties: {
          privateLinkServiceId: postgresServer.id
          groupIds: [
            'postgresqlServer'
          ]
        }
      }
    ]
    subnet: {
      id: '${vnet.id}/subnets/${subnetName}'
    }
  }
}

resource privateDnsZoneGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2021-08-01' = {
  parent: privateEndpoint
  name: 'postgresPrivateDnsZoneGroup'
  properties: {
    privateDnsZoneConfigs: [
      {
        name: 'config1'
        properties: {
          privateDnsZoneId: privateDnsZone.id
        }
      }
    ]
  }
}

output privateEndpointId string = privateEndpoint.id
output privateDnsZoneId string = privateDnsZone.id
