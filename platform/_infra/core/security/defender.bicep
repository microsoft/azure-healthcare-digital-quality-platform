targetScope = 'subscription'

@description('Email address for security contact notifications')
param securityContactEmail string

@description('Phone number for security contact notifications')
param securityContactPhone string = ''

@description('Enable Defender for Containers')
param enableDefenderForContainers bool = true

@description('Enable Defender for Key Vault')
param enableDefenderForKeyVault bool = true

@description('Enable Defender for Azure Cosmos DB')
param enableDefenderForCosmosDB bool = true

@description('Enable Defender for APIs (API Management)')
param enableDefenderForAPIs bool = true

@description('Enable Defender for Resource Manager')
param enableDefenderForResourceManager bool = true

@description('Enable Defender for Container Registries')
param enableDefenderForContainerRegistry bool = true

// Defender for Containers
resource defenderForContainers 'Microsoft.Security/pricings@2024-01-01' = if (enableDefenderForContainers) {
  name: 'Containers'
  properties: {
    pricingTier: 'Standard'
    subPlan: 'P2'
    extensions: [
      {
        name: 'ContainerRegistriesVulnerabilityAssessments'
        isEnabled: 'True'
      }
      {
        name: 'AgentlessDiscoveryForKubernetes'
        isEnabled: 'True'
      }
      {
        name: 'AgentlessVmScanning'
        isEnabled: 'True'
      }
    ]
  }
}

// Defender for Key Vault
resource defenderForKeyVault 'Microsoft.Security/pricings@2024-01-01' = if (enableDefenderForKeyVault) {
  name: 'KeyVaults'
  properties: {
    pricingTier: 'Standard'
  }
}

// Defender for Azure Cosmos DB
resource defenderForCosmosDB 'Microsoft.Security/pricings@2024-01-01' = if (enableDefenderForCosmosDB) {
  name: 'CosmosDbs'
  properties: {
    pricingTier: 'Standard'
  }
}

// Defender for APIs (API Management)
resource defenderForAPIs 'Microsoft.Security/pricings@2024-01-01' = if (enableDefenderForAPIs) {
  name: 'Api'
  properties: {
    pricingTier: 'Standard'
    subPlan: 'P1'
  }
}

// Defender for Resource Manager
resource defenderForResourceManager 'Microsoft.Security/pricings@2024-01-01' = if (enableDefenderForResourceManager) {
  name: 'Arm'
  properties: {
    pricingTier: 'Standard'
  }
}

// Defender for Container Registries (legacy, but included for completeness)
resource defenderForContainerRegistry 'Microsoft.Security/pricings@2024-01-01' = if (enableDefenderForContainerRegistry) {
  name: 'ContainerRegistry'
  properties: {
    pricingTier: 'Standard'
  }
}

// Security contact configuration
resource securityContact 'Microsoft.Security/securityContacts@2020-01-01-preview' = {
  name: 'default'
  properties: {
    emails: securityContactEmail
    phone: securityContactPhone
    alertNotifications: {
      state: 'On'
      minimalSeverity: 'Medium'
    }
    notificationsByRole: {
      state: 'On'
      roles: ['Owner']
    }
  }
}

// Auto-provisioning settings for Log Analytics agent
resource autoProvisioningLogAnalytics 'Microsoft.Security/autoProvisioningSettings@2017-08-01-preview' = {
  name: 'default'
  properties: {
    autoProvision: 'On'
  }
}

// Outputs
output defenderForContainersEnabled bool = enableDefenderForContainers
output defenderForKeyVaultEnabled bool = enableDefenderForKeyVault
output defenderForCosmosDBEnabled bool = enableDefenderForCosmosDB
output defenderForAPIsEnabled bool = enableDefenderForAPIs
output defenderForResourceManagerEnabled bool = enableDefenderForResourceManager
output securityContactConfigured bool = true
