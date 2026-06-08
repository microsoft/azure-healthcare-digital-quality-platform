// Private Endpoint for Azure Container Registry
// Enables secure, private connectivity to ACR from AKS cluster

@description('Specifies the name of the virtual network.')
param virtualNetworkName string

@description('Specifies the name of the subnet which contains the private endpoints.')
param subnetName string

@description('Specifies the name of the Azure Container Registry.')
param acrName string

@description('Specifies the location.')
param location string = resourceGroup().location

param tags object = {}

// Virtual Network
resource vnet 'Microsoft.Network/virtualNetworks@2021-08-01' existing = {
  name: virtualNetworkName
}

// Azure Container Registry
resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = {
  name: acrName
}

var acrPrivateDNSZoneName = 'privatelink.azurecr.io'
var acrPrivateDnsZoneVirtualNetworkLinkName = format('{0}-acr-link-{1}', acrName, take(toLower(uniqueString(acrName, virtualNetworkName)), 4))

// Private DNS Zone for ACR
resource acrPrivateDnsZone 'Microsoft.Network/privateDnsZones@2020-06-01' = {
  name: acrPrivateDNSZoneName
  location: 'global'
  tags: tags
  properties: {}
  dependsOn: [
    vnet
  ]
}

// Virtual Network Link
resource acrPrivateDnsZoneVirtualNetworkLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2020-06-01' = {
  parent: acrPrivateDnsZone
  name: acrPrivateDnsZoneVirtualNetworkLinkName
  location: 'global'
  tags: tags
  properties: {
    registrationEnabled: false
    virtualNetwork: {
      id: vnet.id
    }
  }
}

// Private Endpoint for ACR
resource acrPrivateEndpoint 'Microsoft.Network/privateEndpoints@2021-08-01' = {
  name: '${acrName}-pe'
  location: location
  tags: tags
  properties: {
    privateLinkServiceConnections: [
      {
        name: 'acrPrivateLinkConnection'
        properties: {
          privateLinkServiceId: acr.id
          groupIds: [
            'registry'
          ]
        }
      }
    ]
    subnet: {
      id: '${vnet.id}/subnets/${subnetName}'
    }
  }
}

// Private DNS Zone Group
resource acrPrivateDnsZoneGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2021-08-01' = {
  parent: acrPrivateEndpoint
  name: 'acrPrivateDnsZoneGroup'
  properties: {
    privateDnsZoneConfigs: [
      {
        name: 'config1'
        properties: {
          privateDnsZoneId: acrPrivateDnsZone.id
        }
      }
    ]
  }
}

output privateEndpointId string = acrPrivateEndpoint.id
output privateDnsZoneId string = acrPrivateDnsZone.id
