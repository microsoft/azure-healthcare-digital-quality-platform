// Parameters
@description('Specifies the name of the virtual network.')
param virtualNetworkName string

@description('Specifies the name of the subnet which contains the private endpoints.')
param subnetName string

@description('Specifies the resource name of the Purview account.')
param resourceName string

@description('Specifies the location.')
param location string = resourceGroup().location

param tags object = {}

// Virtual Network
resource vnet 'Microsoft.Network/virtualNetworks@2021-08-01' existing = {
  name: virtualNetworkName
}

resource purviewAccount 'Microsoft.Purview/accounts@2021-12-01' existing = {
  name: resourceName
}

var purviewPrivateDNSZoneNames = [
  'privatelink.purview.azure.com'
  'privatelink.purviewstudio.azure.com'
]

// Private DNS Zones for Purview
resource purviewPrivateDnsZones 'Microsoft.Network/privateDnsZones@2020-06-01' = [for zoneName in purviewPrivateDNSZoneNames: {
  name: zoneName
  location: 'global'
  tags: tags
  properties: {}
}]

// Virtual Network Links for each DNS Zone
resource purviewPrivateDnsZoneVirtualNetworkLinks 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2020-06-01' = [for (zoneName, i) in purviewPrivateDNSZoneNames: {
  parent: purviewPrivateDnsZones[i]
  name: format('{0}-purview-link-{1}-{2}', resourceName, i, take(toLower(uniqueString(resourceName, virtualNetworkName)), 4))
  location: 'global'
  tags: tags
  properties: {
    registrationEnabled: false
    virtualNetwork: {
      id: vnet.id
    }
  }
}]

// Private Endpoint for Purview Account (account endpoint)
resource purviewAccountPrivateEndpoint 'Microsoft.Network/privateEndpoints@2021-08-01' = {
  name: '${resourceName}-account-private-endpoint'
  location: location
  tags: tags
  properties: {
    privateLinkServiceConnections: [
      {
        name: 'purviewAccountPrivateLinkConnection'
        properties: {
          privateLinkServiceId: purviewAccount.id
          groupIds: [
            'account'
          ]
        }
      }
    ]
    subnet: {
      id: '${vnet.id}/subnets/${subnetName}'
    }
  }
}

// Private DNS Zone Group for Account endpoint
resource purviewAccountPrivateDnsZoneGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2021-08-01' = {
  parent: purviewAccountPrivateEndpoint
  name: 'purviewAccountPrivateDnsZoneGroup'
  properties: {
    privateDnsZoneConfigs: [
      {
        name: 'config-account'
        properties: {
          privateDnsZoneId: purviewPrivateDnsZones[0].id
        }
      }
    ]
  }
}

// Private Endpoint for Purview Portal (portal endpoint)
resource purviewPortalPrivateEndpoint 'Microsoft.Network/privateEndpoints@2021-08-01' = {
  name: '${resourceName}-portal-private-endpoint'
  location: location
  tags: tags
  properties: {
    privateLinkServiceConnections: [
      {
        name: 'purviewPortalPrivateLinkConnection'
        properties: {
          privateLinkServiceId: purviewAccount.id
          groupIds: [
            'portal'
          ]
        }
      }
    ]
    subnet: {
      id: '${vnet.id}/subnets/${subnetName}'
    }
  }
}

// Private DNS Zone Group for Portal endpoint
resource purviewPortalPrivateDnsZoneGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2021-08-01' = {
  parent: purviewPortalPrivateEndpoint
  name: 'purviewPortalPrivateDnsZoneGroup'
  properties: {
    privateDnsZoneConfigs: [
      {
        name: 'config-portal'
        properties: {
          privateDnsZoneId: purviewPrivateDnsZones[1].id
        }
      }
    ]
  }
}

output accountPrivateEndpointId string = purviewAccountPrivateEndpoint.id
output portalPrivateEndpointId string = purviewPortalPrivateEndpoint.id
output privateDnsZoneIds array = [for (zoneName, i) in purviewPrivateDNSZoneNames: purviewPrivateDnsZones[i].id]
