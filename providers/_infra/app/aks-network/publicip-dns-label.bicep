// Deployed with: az deployment group create --resource-group <nodeResourceGroup> --template-file publicip-dns-label.bicep
targetScope = 'resourceGroup'

@description('Name of the public IP resource created by the nginx ingress controller LoadBalancer service')
param publicIpName string

@description('DNS label to assign (becomes <dnsLabel>.<location>.cloudapp.azure.com)')
param dnsLabel string

@description('Location of the public IP resource')
param location string = 'eastus2'

// Apply the DNS label so the IP gets a stable FQDN usable in Entra redirect URIs.
// Deploy this file directly into the node resource group (mc_ / MC_).
resource ingressPublicIp 'Microsoft.Network/publicIPAddresses@2023-09-01' = {
  name: publicIpName
  location: location
  sku: {
    name: 'Standard'
  }
  properties: {
    publicIPAllocationMethod: 'Static'
    dnsSettings: {
      domainNameLabel: dnsLabel
    }
  }
}

output fqdn string = ingressPublicIp.properties.dnsSettings.fqdn
output ipAddress string = ingressPublicIp.properties.ipAddress
