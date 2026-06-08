// Deployed with: az deployment group create --resource-group <nodeResourceGroup> --template-file nsg-http-rules.bicep
targetScope = 'resourceGroup'

@description('Name of the AKS node NSG (e.g. aks-agentpool-XXXXXXXX-nsg)')
param nsgName string

@description('Priority for the inbound HTTP rule')
param httpRulePriority int = 100

@description('Priority for the inbound HTTPS rule')
param httpsRulePriority int = 101

// Reference the existing NSG — deploy this file directly into the node resource group
resource nsg 'Microsoft.Network/networkSecurityGroups@2023-09-01' existing = {
  name: nsgName
}

// Allow inbound HTTP (port 80) - required for Let's Encrypt ACME HTTP-01 challenge
resource allowHttp 'Microsoft.Network/networkSecurityGroups/securityRules@2023-09-01' = {
  parent: nsg
  name: 'Allow-HTTP-Inbound'
  properties: {
    priority: httpRulePriority
    direction: 'Inbound'
    access: 'Allow'
    protocol: 'Tcp'
    sourcePortRange: '*'
    destinationPortRange: '80'
    sourceAddressPrefix: 'Internet'
    destinationAddressPrefix: '*'
    description: 'Allow inbound HTTP for nginx ingress and Let\'s Encrypt ACME challenges'
  }
}

// Allow inbound HTTPS (port 443) - required for TLS ingress traffic
resource allowHttps 'Microsoft.Network/networkSecurityGroups/securityRules@2023-09-01' = {
  parent: nsg
  name: 'Allow-HTTPS-Inbound'
  properties: {
    priority: httpsRulePriority
    direction: 'Inbound'
    access: 'Allow'
    protocol: 'Tcp'
    sourcePortRange: '*'
    destinationPortRange: '443'
    sourceAddressPrefix: 'Internet'
    destinationAddressPrefix: '*'
    description: 'Allow inbound HTTPS for TLS ingress traffic'
  }
}

output nsgId string = nsg.id
output httpRuleName string = allowHttp.name
output httpsRuleName string = allowHttps.name
