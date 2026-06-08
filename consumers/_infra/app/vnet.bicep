@description('Specifies the name of the virtual network.')
param vNetName string

@description('Specifies the location.')
param location string = resourceGroup().location

@description('Specifies the name of the subnet for the Service Bus private endpoint.')
param peSubnetName string = 'private-endpoints-subnet'

@description('Specifies the name of the subnet for Function App virtual network integration.')
param appSubnetName string = 'app'

@description('Specifies the name of the subnet used by API Management virtual network integration.')
param apimSubnetName string = 'apim'

@description('Specifies the name of the NSG attached to the APIM subnet.')
param apimSubnetNsgName string = 'nsg-apim'

param tags object = {}

resource apimSubnetNsg 'Microsoft.Network/networkSecurityGroups@2023-05-01' = {
  name: apimSubnetNsgName
  location: location
  tags: tags
  properties: {
    securityRules: [
      {
        name: 'Allow-ApiManagement-ControlPlane-3443'
        properties: {
          priority: 100
          direction: 'Inbound'
          access: 'Allow'
          protocol: 'Tcp'
          sourcePortRange: '*'
          destinationPortRange: '3443'
          sourceAddressPrefix: 'ApiManagement'
          destinationAddressPrefix: 'VirtualNetwork'
        }
      }
    ]
  }
}

resource virtualNetwork 'Microsoft.Network/virtualNetworks@2023-05-01' = {
  name: vNetName
  location: location
  tags: tags
  properties: {
    addressSpace: {
      addressPrefixes: [
        '10.0.0.0/16'
      ]
    }
    encryption: {
      enabled: false
      enforcement: 'AllowUnencrypted'
    }
    subnets: [
      {
        name: peSubnetName
        id: resourceId('Microsoft.Network/virtualNetworks/subnets', vNetName, 'private-endpoints-subnet')
        properties: {
          addressPrefixes: [
            '10.0.1.0/24'
          ]
          delegations: []
          privateEndpointNetworkPolicies: 'Disabled'
          privateLinkServiceNetworkPolicies: 'Enabled'
        }
        type: 'Microsoft.Network/virtualNetworks/subnets'
      }
      {
        name: appSubnetName
        id: resourceId('Microsoft.Network/virtualNetworks/subnets', vNetName, 'app')
        properties: {
          addressPrefixes: [
            '10.0.2.0/24'
          ]
          delegations: []
          privateEndpointNetworkPolicies: 'Disabled'
          privateLinkServiceNetworkPolicies: 'Enabled'
        }
        type: 'Microsoft.Network/virtualNetworks/subnets'
      }
      {
        name: apimSubnetName
        id: resourceId('Microsoft.Network/virtualNetworks/subnets', vNetName, apimSubnetName)
        properties: {
          addressPrefixes: [
            '10.0.3.0/24'
          ]
          delegations: []
          networkSecurityGroup: {
            id: apimSubnetNsg.id
          }
          privateEndpointNetworkPolicies: 'Enabled'
          privateLinkServiceNetworkPolicies: 'Enabled'
        }
        type: 'Microsoft.Network/virtualNetworks/subnets'
      }
    ]
    virtualNetworkPeerings: []
    enableDdosProtection: false
  }
}

output peSubnetName string = virtualNetwork.properties.subnets[0].name
output peSubnetID string = virtualNetwork.properties.subnets[0].id
output appSubnetName string = virtualNetwork.properties.subnets[1].name
output appSubnetID string = virtualNetwork.properties.subnets[1].id
output apimSubnetName string = virtualNetwork.properties.subnets[2].name
output apimSubnetID string = virtualNetwork.properties.subnets[2].id
