// Deployed with: az deployment group create \
//   --resource-group rg-azure-healthcare-digital-quality \
//   --template-file app-subnet-nsg.bicep
//
// The AKS nodes reside on the 'app' subnet of vnet-pynargp3zuafw.
// The subnet NSG has no custom rules by default — only DenyAllInBound at priority 65500.
// These rules are required so that:
//   1. The Azure LB can forward HTTP/HTTPS traffic to nodes (ports 80/443 from Internet)
//   2. The Azure LB health probe can reach NodePorts 30000-32767 (AzureLoadBalancer tag)
//   3. Let's Encrypt ACME HTTP-01 challenge can reach port 80 during cert issuance
targetScope = 'resourceGroup'

@description('Name of the NSG on the AKS app subnet')
param nsgName string = 'vnet-pynargp3zuafw-app-nsg-eastus2'

@description('Priority for the HTTP/HTTPS inbound rule')
param httpHttpsPriority int = 100

@description('Priority for the NodePort LB health probe rule')
param nodePortPriority int = 110

resource subnetNsg 'Microsoft.Network/networkSecurityGroups@2023-09-01' existing = {
  name: nsgName
}

// Allow inbound HTTP and HTTPS from Internet → nginx ingress controller + Let's Encrypt ACME challenge
resource allowHttpHttps 'Microsoft.Network/networkSecurityGroups/securityRules@2023-09-01' = {
  parent: subnetNsg
  name: 'Allow-HTTP-HTTPS-Inbound'
  properties: {
    priority: httpHttpsPriority
    direction: 'Inbound'
    access: 'Allow'
    protocol: 'Tcp'
    sourcePortRange: '*'
    destinationPortRanges: ['80', '443']
    sourceAddressPrefix: 'Internet'
    destinationAddressPrefix: '*'
    description: 'Allow HTTP/HTTPS from Internet for nginx ingress and Let\'s Encrypt ACME challenges'
  }
}

// Allow Azure LB health probes to reach NodePorts (30000-32767) on the nodes
resource allowNodePorts 'Microsoft.Network/networkSecurityGroups/securityRules@2023-09-01' = {
  parent: subnetNsg
  name: 'Allow-NodePorts-Inbound'
  properties: {
    priority: nodePortPriority
    direction: 'Inbound'
    access: 'Allow'
    protocol: 'Tcp'
    sourcePortRange: '*'
    destinationPortRange: '30000-32767'
    sourceAddressPrefix: 'AzureLoadBalancer'
    destinationAddressPrefix: '*'
    description: 'Allow Azure LB health probes to reach Kubernetes NodePorts'
  }
}

output nsgId string = subnetNsg.id
