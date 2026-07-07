@description('Specifies the name of the virtual network.')
param virtualNetworkName string

@description('Specifies the name of the subnet which contains the private endpoints.')
param subnetName string

@description('Specifies the Azure SQL logical server name.')
param serverName string

@description('Specifies the location.')
param location string = resourceGroup().location

param tags object = {}

resource vnet 'Microsoft.Network/virtualNetworks@2021-08-01' existing = {
  name: virtualNetworkName
}

resource sqlServer 'Microsoft.Sql/servers@2023-08-01-preview' existing = {
  name: serverName
}

var sqlPrivateDnsZoneName = format('privatelink{0}', environment().suffixes.sqlServerHostname)
var sqlPrivateDnsZoneVirtualNetworkLinkName = format('{0}-sql-link-{1}', serverName, take(toLower(uniqueString(serverName, virtualNetworkName)), 4))

resource sqlPrivateDnsZone 'Microsoft.Network/privateDnsZones@2020-06-01' = {
  name: sqlPrivateDnsZoneName
  location: 'global'
  tags: tags
  properties: {}
  dependsOn: [
    vnet
  ]
}

resource sqlPrivateDnsZoneVirtualNetworkLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2020-06-01' = {
  parent: sqlPrivateDnsZone
  name: sqlPrivateDnsZoneVirtualNetworkLinkName
  location: 'global'
  tags: tags
  properties: {
    registrationEnabled: false
    virtualNetwork: {
      id: vnet.id
    }
  }
}

resource sqlPrivateEndpoint 'Microsoft.Network/privateEndpoints@2021-08-01' = {
  name: '${serverName}-sql-private-endpoint'
  location: location
  tags: tags
  properties: {
    privateLinkServiceConnections: [
      {
        name: 'sqlPrivateLinkConnection'
        properties: {
          privateLinkServiceId: sqlServer.id
          groupIds: [
            'sqlServer'
          ]
        }
      }
    ]
    subnet: {
      id: '${vnet.id}/subnets/${subnetName}'
    }
  }
}

resource sqlPrivateDnsZoneGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2021-08-01' = {
  parent: sqlPrivateEndpoint
  name: 'sqlPrivateDnsZoneGroup'
  properties: {
    privateDnsZoneConfigs: [
      {
        name: 'config1'
        properties: {
          privateDnsZoneId: sqlPrivateDnsZone.id
        }
      }
    ]
  }
}

output privateEndpointId string = sqlPrivateEndpoint.id
output privateDnsZoneId string = sqlPrivateDnsZone.id
