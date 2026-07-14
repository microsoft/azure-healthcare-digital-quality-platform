@description('Name of the foundry resource')
param foundryName string

@description('Location for all resources')
param location string

@description('Model deployment name')
param modelDeploymentName string

@description('Model name')
param modelName string

@description('Model version')
param modelVersion string

@description('Model capacity')
param modelCapacity int

@description('Enable OpenAI model deployments in this region')
param enableModelDeployments bool = true

@description('Enable Azure AI Agents capability host creation')
param enableAgentsCapabilityHost bool = true

@description('Fine-tuning model deployment name')
param fineTuneModelDeploymentName string = 'gpt-4o-mini'

@description('Fine-tuning model name')
param fineTuneModelName string = 'gpt-4o-mini'

@description('Fine-tuning model version')
param fineTuneModelVersion string = '2025-08-07'

@description('Fine-tuning model capacity')
param fineTuneModelCapacity int = 10

@description('Embedding model deployment name')
param embeddingModelDeploymentName string = 'text-embedding-3-large'

@description('Embedding model name')
param embeddingModelName string = 'text-embedding-3-large'

@description('Embedding model version')
param embeddingModelVersion string = '1'

@description('Embedding model capacity')
param embeddingModelCapacity int = 10

@description('Tags for resources')
param tags object = {}

@description('Enable private endpoint')
param enablePrivateEndpoint bool = false

@description('Public network access setting')
param publicNetworkAccess string = 'Enabled'

@description('Set to true to restore a soft-deleted Foundry/Cognitive account with the same name')
param restoreFoundryAccount bool = false

// Create AI Services foundry account
resource foundryAccount 'Microsoft.CognitiveServices/accounts@2025-06-01' = {
  name: foundryName
  location: location
  tags: tags
  sku: {
    name: 'S0'
  }
  kind: 'AIServices'
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    restore: restoreFoundryAccount
    apiProperties: {}
    customSubDomainName: foundryName
    networkAcls: {
      defaultAction: enablePrivateEndpoint ? 'Deny' : 'Allow'
      virtualNetworkRules: []
      ipRules: []
    }
    allowProjectManagement: true
    defaultProject: 'proj-default'
    associatedProjects: [
      'proj-default'
    ]
    publicNetworkAccess: publicNetworkAccess
    disableLocalAuth: true
  }
}

// Create Agents capability host
resource agentsCapabilityHost 'Microsoft.CognitiveServices/accounts/capabilityHosts@2025-06-01' = if (enableAgentsCapabilityHost) {
  parent: foundryAccount
  name: 'Agents'
  properties: {
    capabilityHostKind: 'Agents'
  }
}

// Deploy GPT model
resource modelDeployment 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = if (enableModelDeployments) {
  parent: foundryAccount
  name: modelDeploymentName
  sku: {
    name: 'GlobalStandard'
    capacity: modelCapacity
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: modelName
      version: modelVersion
    }
    versionUpgradeOption: 'OnceNewDefaultVersionAvailable'
    currentCapacity: modelCapacity
    raiPolicyName: 'Microsoft.DefaultV2'
  }
}

// Deploy gpt-5-mini model for fine-tuning
resource fineTuneModelDeployment 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = if (enableModelDeployments) {
  parent: foundryAccount
  name: fineTuneModelDeploymentName
  sku: {
    name: 'GlobalStandard'
    capacity: fineTuneModelCapacity
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: fineTuneModelName
      version: fineTuneModelVersion
    }
    versionUpgradeOption: 'OnceNewDefaultVersionAvailable'
    currentCapacity: fineTuneModelCapacity
    raiPolicyName: 'Microsoft.DefaultV2'
  }
  dependsOn: [
    modelDeployment
  ]
}

// Deploy text-embedding-3-large model for semantic similarity
resource embeddingModelDeployment 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = if (enableModelDeployments) {
  parent: foundryAccount
  name: embeddingModelDeploymentName
  sku: {
    name: 'Standard'
    capacity: embeddingModelCapacity
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: embeddingModelName
      version: embeddingModelVersion
    }
    versionUpgradeOption: 'OnceNewDefaultVersionAvailable'
    currentCapacity: embeddingModelCapacity
    raiPolicyName: 'Microsoft.DefaultV2'
  }
  dependsOn: [
    fineTuneModelDeployment
  ]
}

// Create default project
resource defaultProject 'Microsoft.CognitiveServices/accounts/projects@2025-06-01' = {
  parent: foundryAccount
  name: 'proj-default'
  location: location
  kind: 'AIServices'
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    description: 'Default project for AI-assisted digital quality measures'
    displayName: 'proj-default'
  }
}

output foundryAccountId string = foundryAccount.id
output foundryAccountName string = foundryAccount.name
output foundryEndpoint string = foundryAccount.properties.endpoint
output projectId string = defaultProject.id
output projectEndpoint string = 'https://${foundryName}.services.ai.azure.com/api/projects/proj-default'
output modelDeploymentName string = enableModelDeployments ? modelDeployment.name : ''
output fineTuneModelDeploymentName string = enableModelDeployments ? fineTuneModelDeployment.name : ''
output embeddingModelDeploymentName string = enableModelDeployments ? embeddingModelDeployment.name : ''
