// Parameters
@description('Specifies the name of the virtual network.')
param virtualNetworkName string

@description('Specifies the name of the subnet which contains the private endpoints.')
param subnetName string

@description('Specifies the resource name of the Foundry/Cognitive Services resource.')
param resourceName string

@description('Specifies the location.')
param location string = resourceGroup().location

param tags object = {}

// Virtual Network
resource vnet 'Microsoft.Network/virtualNetworks@2021-08-01' existing = {
  name: virtualNetworkName
}

resource foundryAccount 'Microsoft.CognitiveServices/accounts@2025-06-01' existing = {
  name: resourceName
}

var cognitiveServicesPrivateDNSZoneName = 'privatelink.cognitiveservices.azure.com'
var openAIPrivateDNSZoneName = 'privatelink.openai.azure.com'
var servicesAIPrivateDNSZoneName = 'privatelink.services.ai.azure.com'
var cognitiveServicesDnsZoneLinkName = format('{0}-cogservices-link-{1}', resourceName, take(toLower(uniqueString(resourceName, virtualNetworkName)), 4))
var openAIDnsZoneLinkName = format('{0}-openai-link-{1}', resourceName, take(toLower(uniqueString(resourceName, virtualNetworkName)), 4))
var servicesAIDnsZoneLinkName = format('{0}-servicesai-link-{1}', resourceName, take(toLower(uniqueString(resourceName, virtualNetworkName)), 4))

// Private DNS Zone for Cognitive Services
resource cognitiveServicesPrivateDnsZone 'Microsoft.Network/privateDnsZones@2020-06-01' = {
  name: cognitiveServicesPrivateDNSZoneName
  location: 'global'
  tags: tags
  properties: {}
  dependsOn: [
    vnet
  ]
}

// Private DNS Zone for OpenAI
resource openAIPrivateDnsZone 'Microsoft.Network/privateDnsZones@2020-06-01' = {
  name: openAIPrivateDNSZoneName
  location: 'global'
  tags: tags
  properties: {}
  dependsOn: [
    vnet
  ]
}

// Private DNS Zone for AI Services (services.ai.azure.com)
resource servicesAIPrivateDnsZone 'Microsoft.Network/privateDnsZones@2020-06-01' = {
  name: servicesAIPrivateDNSZoneName
  location: 'global'
  tags: tags
  properties: {}
  dependsOn: [
    vnet
  ]
}

// Virtual Network Links
resource cognitiveServicesPrivateDnsZoneVirtualNetworkLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2020-06-01' = {
  parent: cognitiveServicesPrivateDnsZone
  name: cognitiveServicesDnsZoneLinkName
  location: 'global'
  tags: tags
  properties: {
    registrationEnabled: false
    virtualNetwork: {
      id: vnet.id
    }
  }
}

resource openAIPrivateDnsZoneVirtualNetworkLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2020-06-01' = {
  parent: openAIPrivateDnsZone
  name: openAIDnsZoneLinkName
  location: 'global'
  tags: tags
  properties: {
    registrationEnabled: false
    virtualNetwork: {
      id: vnet.id
    }
  }
}

resource servicesAIPrivateDnsZoneVirtualNetworkLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2020-06-01' = {
  parent: servicesAIPrivateDnsZone
  name: servicesAIDnsZoneLinkName
  location: 'global'
  tags: tags
  properties: {
    registrationEnabled: false
    virtualNetwork: {
      id: vnet.id
    }
  }
}

// Private Endpoint for Cognitive Services account
resource foundryPrivateEndpoint 'Microsoft.Network/privateEndpoints@2021-08-01' = {
  name: 'pe-${resourceName}-account'
  location: location
  tags: tags
  properties: {
    privateLinkServiceConnections: [
      {
        name: 'foundryAccountPrivateLinkConnection'
        properties: {
          privateLinkServiceId: foundryAccount.id
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

// Private DNS Zone Group for Cognitive Services endpoint
resource foundryPrivateEndpointDnsZoneGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2021-08-01' = {
  parent: foundryPrivateEndpoint
  name: 'foundryPrivateDnsZoneGroup'
  properties: {
    privateDnsZoneConfigs: [
      {
        name: 'cognitiveservices-config'
        properties: {
          privateDnsZoneId: cognitiveServicesPrivateDnsZone.id
        }
      }
      {
        name: 'openai-config'
        properties: {
          privateDnsZoneId: openAIPrivateDnsZone.id
        }
      }
      {
        name: 'servicesai-config'
        properties: {
          privateDnsZoneId: servicesAIPrivateDnsZone.id
        }
      }
    ]
  }
}

output privateEndpointId string = foundryPrivateEndpoint.id
output cognitiveServicesPrivateDnsZoneId string = cognitiveServicesPrivateDnsZone.id
output openAIPrivateDnsZoneId string = openAIPrivateDnsZone.id
output servicesAIPrivateDnsZoneId string = servicesAIPrivateDnsZone.id
