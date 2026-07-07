targetScope = 'subscription'

@minLength(1)
@maxLength(64)
@description('Name of the the environment which is used to generate a short unique hash used in all resources.')
param environmentName string


@minLength(1)
@description('Primary location for all resources')
@allowed(['australiaeast', 'eastasia', 'eastus', 'eastus2', 'centralus', 'northeurope', 'southcentralus', 'southeastasia', 'swedencentral', 'uksouth', 'westus2', 'eastus2euap'])
@metadata({
  azd: {
    type: 'location'
  }
})
param location string
param vnetEnabled bool
param apiServiceName string = ''
param apiUserAssignedIdentityName string = ''
param applicationInsightsName string = ''
param logAnalyticsName string = ''
param resourceGroupName string = ''
param storageAccountName string = ''
param vNetName string = ''
param mcpEntraApplicationDisplayName string = ''
param mcpEntraApplicationUniqueName string = ''
param existingEntraAppId string = ''
param disableLocalAuth bool = true

@description('APIM SKU. Use Developer or Premium when APIM virtual network integration is enabled.')
@allowed([
  'Developer'
  'Basicv2'
  'Standardv2'
  'Premium'
])
param apimSku string = 'Basicv2'

@description('APIM virtual network mode. Set to External or Internal to give APIM private connectivity to AKS subnet.')
@allowed([
  'None'
  'External'
  'Internal'
])
param apimVirtualNetworkType string = 'External'

@description('Backend URL for the MCP server (e.g. internal LB or ingress endpoint). Set post-provision.')
param mcpServerBackendUrl string = 'http://placeholder/runtime/webhooks/mcp'

@description('AKS system node pool VM size.')
param aksSystemNodePoolVmSize string = 'Standard_D2s_v5'

@description('AKS system node pool name. Must match existing cluster pool when updating.')
param aksSystemNodePoolName string = 'sys3'

@description('AKS system node pool node count. Use 1 in constrained regions to avoid capacity failures during updates.')
param aksSystemNodePoolCount int = 1

// Foundry AI configuration
param foundryName string = ''
param foundryModelDeploymentName string = 'gpt-4o-mini'
param foundryModelName string = 'gpt-4o-mini'
param foundryModelVersion string = '2024-07-18'
param foundryModelCapacity int = 10
param enableFoundryModelDeployments bool = true
param enableFoundryAgentsCapabilityHost bool = true
param restoreFoundryAccount bool = false

// Fine-tuning model configuration
param fineTuneModelDeploymentName string = 'gpt-4o-mini-ft'
param fineTuneModelName string = 'gpt-4o-mini'
param fineTuneModelVersion string = '2024-07-18'
param fineTuneModelCapacity int = 10

// Embeddings model configuration
param embeddingModelDeploymentName string = 'text-embedding-3-large'
param embeddingModelName string = 'text-embedding-3-large'
param embeddingModelVersion string = '1'
param embeddingModelCapacity int = 10

// CosmosDB configuration
param cosmosDbAccountName string = ''
param cosmosDatabaseName string = 'dq'

// Azure SQL reporting configuration
param sqlServerName string = ''
param sqlDatabaseName string = 'dq_receiver_reporting'
param sqlAdministratorLogin string = 'sqladminuser'
@secure()
@minLength(16)
param sqlAdministratorPassword string
param sqlDatabaseSkuName string = 'Basic'
param sqlDatabaseSkuTier string = 'Basic'
param sqlEntraAdminObjectId string = ''
param sqlEntraAdminLogin string = ''
param sqlAzureAdOnlyAuthentication bool = false



// =========================================
// Entra Agent Identity Configuration
// =========================================
@description('Enable Entra Agent Identity for the Digital Quality Orchestrator agent (preview feature)')
param agentIdentityEnabled bool = true

@description('Display name for the Agent Identity Blueprint')
param agentBlueprintDisplayName string = ''

@description('Display name for the Digital Quality Orchestrator Agent Identity')
param agentIdentityDisplayName string = ''

@description('Principal ID of sponsor user for agent identity (admin user)')
param agentSponsorPrincipalId string = ''

@description('Principal ID of developer user for local development Cosmos DB access (optional)')
param developerPrincipalId string = ''

@description('Developer IP address for Cosmos DB firewall access (optional, for local development)')
param developerIpAddress string = ''

// =========================================
// Agents Approval Logic App Configuration
// =========================================
@description('Enable the approval Logic App for CI/CD governance')
param approvalLogicAppEnabled bool = false

@description('Teams channel ID for approval notifications')
param teamsChannelId string = ''

@description('Teams group/team ID for approval notifications')
param teamsGroupId string = ''

@description('Approval timeout in hours')
param approvalTimeoutHours int = 2

// =========================================
// Azure Managed Grafana Configuration
// =========================================
@description('Enable Azure Managed Grafana for AKS monitoring dashboards')
param grafanaEnabled bool = true

@description('Name for the Azure Managed Grafana instance')
param grafanaName string = ''

// =========================================
// Microsoft Defender for Cloud Configuration
// =========================================
@description('Enable Microsoft Defender for Cloud')
param defenderEnabled bool = true

@description('Email address for Defender security contact notifications')
param defenderSecurityContactEmail string = ''

@description('Phone number for Defender security contact notifications')
param defenderSecurityContactPhone string = ''

@description('Enable Defender for Containers')
param defenderForContainersEnabled bool = true

@description('Enable Defender for Key Vault')
param defenderForKeyVaultEnabled bool = true

@description('Enable Defender for Azure Cosmos DB')
param defenderForCosmosDBEnabled bool = true

@description('Enable Defender for APIs (API Management)')
param defenderForAPIsEnabled bool = true

@description('Enable Defender for Resource Manager')
param defenderForResourceManagerEnabled bool = true

@description('Enable Defender for Container Registries')
param defenderForContainerRegistryEnabled bool = true

// =========================================
// Microsoft Purview Configuration
// =========================================
@description('Enable Microsoft Purview for data governance and compliance')
param purviewEnabled bool = false

@description('Name for the Microsoft Purview account')
param purviewAccountName string = ''

// MCP Client APIM gateway specific variables

var oauth_scopes = 'openid https://graph.microsoft.com/.default'


var abbrs = loadJsonContent('./abbreviations.json')
var resourceToken = toLower(uniqueString(subscription().id, environmentName, location))
var tags = { 'azd-env-name': environmentName }
var functionAppName = !empty(apiServiceName) ? apiServiceName : '${abbrs.webSitesFunctions}api-${resourceToken}'
var deploymentStorageContainerName = 'app-package-${take(functionAppName, 32)}-${take(toLower(uniqueString(functionAppName, resourceToken)), 7)}'
var serviceVirtualNetworkName = !empty(vNetName) ? vNetName : '${abbrs.networkVirtualNetworks}${resourceToken}'
var serviceVirtualNetworkAppSubnetName = 'app'
var serviceVirtualNetworkPrivateEndpointSubnetName = 'private-endpoints-subnet'
var serviceVirtualNetworkApimSubnetName = 'apim'


// Organize resources in a resource group
resource rg 'Microsoft.Resources/resourceGroups@2021-04-01' = {
  name: !empty(resourceGroupName) ? resourceGroupName : '${abbrs.resourcesResourceGroups}${environmentName}'
  location: location
  tags: tags
}

var apimResourceToken = toLower(uniqueString(subscription().id, resourceGroupName, environmentName, location))
var apiManagementName = '${abbrs.apiManagementService}${apimResourceToken}'
var sqlReportingServerName = !empty(sqlServerName) ? sqlServerName : '${abbrs.sqlServers}${resourceToken}'
var sqlReportingDatabaseName = sqlDatabaseName

// apim service deployment
module apimService './core/apim/apim.bicep' = {
  name: apiManagementName
  scope: rg
  params:{
    apiManagementName: apiManagementName
    apimSku: apimSku
    virtualNetworkType: vnetEnabled ? apimVirtualNetworkType : 'None'
    virtualNetworkSubnetResourceId: (vnetEnabled && apimVirtualNetworkType != 'None') ? '${rg.id}/providers/Microsoft.Network/virtualNetworks/${serviceVirtualNetworkName}/subnets/${serviceVirtualNetworkApimSubnetName}' : ''
    logAnalyticsWorkspaceId: monitoring.outputs.logAnalyticsWorkspaceId
  }
  dependsOn: vnetEnabled ? [
    monitoring
    serviceVirtualNetworkEarly
  ] : [
    monitoring
  ]
}

// MCP client oauth via APIM gateway
module oauthAPIModule './app/apim-oauth/oauth.bicep' = {
  name: 'oauthAPIModule'
  scope: rg
  params: {
    location: location
    entraAppUniqueName: !empty(mcpEntraApplicationUniqueName) ? mcpEntraApplicationUniqueName : 'mcp-oauth-${abbrs.applications}${apimResourceToken}'
    entraAppDisplayName: !empty(mcpEntraApplicationDisplayName) ? mcpEntraApplicationDisplayName : 'MCP-OAuth-${abbrs.applications}${apimResourceToken}'
    apimServiceName: apimService.name
    oauthScopes: oauth_scopes
    entraAppUserAssignedIdentityPrincipleId: apimService.outputs.entraAppUserAssignedIdentityPrincipleId
    entraAppUserAssignedIdentityClientId: apimService.outputs.entraAppUserAssignedIdentityClientId
    existingEntraAppId: existingEntraAppId
  }
}

// MCP server API endpoints pointing to AKS service
module mcpApiModule './app/apim-mcp/mcp-api.bicep' = {
  name: 'mcpApiModule'
  scope: rg
  params: {
    apimServiceName: apimService.name
    mcpServerBackendUrl: mcpServerBackendUrl
  }
  dependsOn: [
    aksCluster
    oauthAPIModule
  ]
}


// User assigned managed identity for AKS cluster
module aksUserAssignedIdentity './core/identity/userAssignedIdentity.bicep' = {
  name: 'aksUserAssignedIdentity'
  scope: rg
  params: {
    location: location
    tags: tags
    identityName: !empty(apiUserAssignedIdentityName) ? apiUserAssignedIdentityName : '${abbrs.managedIdentityUserAssignedIdentities}aks-${resourceToken}'
  }
}

// User assigned managed identity for MCP server workload
module mcpUserAssignedIdentity './core/identity/userAssignedIdentity.bicep' = {
  name: 'mcpUserAssignedIdentity'
  scope: rg
  params: {
    location: location
    tags: tags
    identityName: '${abbrs.managedIdentityUserAssignedIdentities}mcp-${resourceToken}'
  }
}

// =========================================
// Entra Agent Identity for Digital Quality Orchestrator Agent
// =========================================

// Agent Identity Blueprint names
var agentBlueprintName = !empty(agentBlueprintDisplayName) ? agentBlueprintDisplayName : 'DigitalQualityOrchestrator-Blueprint-${resourceToken}'
var agentName = !empty(agentIdentityDisplayName) ? agentIdentityDisplayName : 'DigitalQualityOrchestrator-Agent-${resourceToken}'

// Agent Identity Blueprint - the template for creating agent identities
// This uses the MCP managed identity to authenticate and create the blueprint via Microsoft Graph API
module agentIdentityBlueprint './core/identity/agentIdentityBlueprint.bicep' = if (agentIdentityEnabled) {
  name: 'agentIdentityBlueprint'
  scope: rg
  params: {
    location: location
    tags: tags
    blueprintDisplayName: agentBlueprintName
    blueprintUniqueName: 'dqo-blueprint-${resourceToken}'
    federatedIdentityClientId: mcpUserAssignedIdentity.outputs.identityName
    federatedIdentityPrincipalId: mcpUserAssignedIdentity.outputs.identityPrincipalId
    sponsorPrincipalIds: !empty(agentSponsorPrincipalId) ? [agentSponsorPrincipalId] : []
    ownerPrincipalIds: !empty(agentSponsorPrincipalId) ? [agentSponsorPrincipalId] : []
    agentScopeValue: 'digital_quality_orchestrator'
  }
}

// Agent Identity - the actual identity used by the Digital Quality Orchestrator agent
module digitalQualityOrchestratorAgentIdentity './core/identity/agentIdentity.bicep' = if (agentIdentityEnabled) {
  name: 'digitalQualityOrchestratorAgentIdentity'
  scope: rg
  params: {
    location: location
    tags: tags
    agentDisplayName: agentName
    blueprintAppId: agentIdentityBlueprint!.outputs.blueprintAppId
    managedIdentityResourceId: mcpUserAssignedIdentity.outputs.identityId
    sponsorPrincipalIds: !empty(agentSponsorPrincipalId) ? [agentSponsorPrincipalId] : []
  }
}

// Virtual Network (created before AKS if vnetEnabled)
module serviceVirtualNetworkEarly 'app/vnet.bicep' = if (vnetEnabled) {
  name: 'serviceVirtualNetworkEarly'
  scope: rg
  params: {
    location: location
    tags: tags
    vNetName: serviceVirtualNetworkName
    appSubnetName: serviceVirtualNetworkAppSubnetName
    peSubnetName: serviceVirtualNetworkPrivateEndpointSubnetName
    apimSubnetName: serviceVirtualNetworkApimSubnetName
  }
}

// Azure Container Registry for Docker images
module containerRegistry './core/acr/container-registry.bicep' = {
  name: 'containerRegistry'
  scope: rg
  params: {
    containerRegistryName: '${abbrs.containerRegistryRegistries}${resourceToken}'
    location: location
    tags: tags
    sku: vnetEnabled ? 'Premium' : 'Standard' // Premium required when disabling public network access
    publicNetworkAccess: (vnetEnabled && empty(developerIpAddress)) ? 'Disabled' : 'Enabled'
    developerIpAddresses: !empty(developerIpAddress) ? [developerIpAddress] : []
  }
}

// =========================================
// Azure Monitor Workspace for Prometheus Metrics (deployed before AKS)
// =========================================
var azureMonitorWorkspaceName = 'amw-${resourceToken}'

// Azure Monitor Workspace - required for Prometheus metrics collection from AKS
module azureMonitorWorkspace './core/monitor/azure-monitor-workspace.bicep' = if (grafanaEnabled) {
  name: 'azureMonitorWorkspace'
  scope: rg
  params: {
    name: azureMonitorWorkspaceName
    location: location
    tags: tags
    publicNetworkAccess: 'Enabled'
  }
}

// AKS Cluster
module aksCluster './core/aks/aks-cluster.bicep' = {
  name: 'aksCluster'
  scope: rg
  params: {
    aksClusterName: '${abbrs.containerServiceManagedClusters}${resourceToken}'
    location: location
    tags: tags
    kubernetesVersion: '1.34'
    systemNodePoolName: aksSystemNodePoolName
    systemNodePoolVmSize: aksSystemNodePoolVmSize
    systemNodePoolCount: aksSystemNodePoolCount
    userAssignedIdentityId: aksUserAssignedIdentity.outputs.identityId
    logAnalyticsWorkspaceId: monitoring.outputs.logAnalyticsWorkspaceId
    subnetId: vnetEnabled ? '${rg.id}/providers/Microsoft.Network/virtualNetworks/${serviceVirtualNetworkName}/subnets/${serviceVirtualNetworkAppSubnetName}' : ''
    enablePrometheus: grafanaEnabled
    azureMonitorWorkspaceId: grafanaEnabled ? azureMonitorWorkspace!.outputs.id : ''
    nodeResourceGroup: 'MC_${abbrs.containerServiceManagedClusters}${resourceToken}_${location}'
  }
  dependsOn: vnetEnabled ? [
    serviceVirtualNetworkEarly
    monitoring
  ] : [
    monitoring
  ]
}



// Grant AKS pull access to ACR
var acrPullRoleDefinitionId = '7f951dda-4ed3-4680-a7ca-43fe172d538d'
module acrPullRoleAssignment 'core/acr/acr-role-assignment.bicep' = {
  name: 'acrPullRoleAssignment'
  scope: rg
  params: {
    containerRegistryName: containerRegistry.outputs.containerRegistryName
    roleDefinitionID: acrPullRoleDefinitionId
    principalID: aksUserAssignedIdentity.outputs.identityPrincipalId
  }
  dependsOn: [
    aksCluster
  ]
}

// Private endpoint for Azure Container Registry (when VNet enabled)
module acrPrivateEndpoint 'app/acr-PrivateEndpoint.bicep' = if (vnetEnabled) {
  name: 'acrPrivateEndpoint'
  scope: rg
  params: {
    location: location
    tags: tags
    virtualNetworkName: serviceVirtualNetworkName
    subnetName: vnetEnabled ? serviceVirtualNetworkPrivateEndpointSubnetName : ''
    acrName: containerRegistry.outputs.containerRegistryName
  }
  dependsOn: [
    serviceVirtualNetwork
  ]
}

// =========================================
// AKS Workload Identity Federation for Agent Identity
// =========================================

// Configure federated credential for the Agent Identity Blueprint to allow AKS pods to authenticate
module agentFederatedCredential './core/identity/aksFederatedCredential.bicep' = if (agentIdentityEnabled) {
  name: 'agentFederatedCredential'
  scope: rg
  params: {
    location: location
    tags: tags
    aksClusterName: aksCluster.outputs.aksClusterName
    serviceAccountNamespace: 'orchestrator'
    serviceAccountName: 'mcp-agent-sa'
    identityClientId: digitalQualityOrchestratorAgentIdentity!.outputs.agentIdentityAppId
    identityPrincipalId: digitalQualityOrchestratorAgentIdentity!.outputs.agentIdentityPrincipalId
    federatedCredentialName: 'aks-mcp-agent-fed'
    configurationIdentityResourceId: mcpUserAssignedIdentity.outputs.identityId
  }
}

// Backing storage for Azure functions api
module storage './core/storage/storage-account.bicep' = {
  name: 'storage'
  scope: rg
  params: {
    name: !empty(storageAccountName) ? storageAccountName : '${abbrs.storageStorageAccounts}${resourceToken}'
    location: location
    tags: tags
    containers: [{name: deploymentStorageContainerName}, {name: 'snippets'}]
    publicNetworkAccess: vnetEnabled ? 'Disabled' : 'Enabled'
    networkAcls: !vnetEnabled ? {} : {
      defaultAction: 'Deny'
    }
    // Shared key access is required for azd to upload the deployment package. The function runtime still uses managed identity.
    allowSharedKeyAccess: true
  }
}

var StorageBlobDataOwner = 'b7e6dc6d-f1e8-4753-8033-0f276bb0955b'
var StorageQueueDataContributor = '974c5e8b-45b9-4653-ba55-5f855dd0fb88'

// Allow access from MCP server workload identity to blob storage
module blobRoleAssignmentMcp 'app/storage-Access.bicep' = {
  name: 'blobRoleAssignmentMcp'
  scope: rg
  params: {
    storageAccountName: storage.outputs.name
    roleDefinitionID: StorageBlobDataOwner
    principalID: mcpUserAssignedIdentity.outputs.identityPrincipalId
  }
}

// Allow access from MCP server workload identity to queue storage
module queueRoleAssignmentMcp 'app/storage-Access.bicep' = {
  name: 'queueRoleAssignmentMcp'
  scope: rg
  params: {
    storageAccountName: storage.outputs.name
    roleDefinitionID: StorageQueueDataContributor
    principalID: mcpUserAssignedIdentity.outputs.identityPrincipalId
  }
}

// Virtual Network & private endpoint to blob storage
module serviceVirtualNetwork 'app/vnet.bicep' =  if (vnetEnabled) {
  name: 'serviceVirtualNetwork'
  scope: rg
  params: {
    location: location
    tags: tags
    vNetName: serviceVirtualNetworkName
    appSubnetName: serviceVirtualNetworkAppSubnetName
    peSubnetName: serviceVirtualNetworkPrivateEndpointSubnetName
    apimSubnetName: serviceVirtualNetworkApimSubnetName
  }
}

module storagePrivateEndpoint 'app/storage-PrivateEndpoint.bicep' = if (vnetEnabled) {
  name: 'servicePrivateEndpoint'
  scope: rg
  params: {
    location: location
    tags: tags
    virtualNetworkName: serviceVirtualNetworkName
    subnetName: vnetEnabled ? serviceVirtualNetworkPrivateEndpointSubnetName : ''
    resourceName: storage.outputs.name
  }
  dependsOn: [
    serviceVirtualNetwork
  ]
}

// Azure AI Foundry deployment
var foundryResourceName = !empty(foundryName) ? foundryName : '${abbrs.cognitiveServicesAccounts}${resourceToken}'
var bingResourceName = 'bing-${resourceToken}'

module foundry './core/ai/foundry.bicep' = {
  name: 'foundry'
  scope: rg
  params: {
    foundryName: foundryResourceName
    bingName: bingResourceName
    location: location
    modelDeploymentName: foundryModelDeploymentName
    modelName: foundryModelName
    modelVersion: foundryModelVersion
    modelCapacity: foundryModelCapacity
    enableModelDeployments: enableFoundryModelDeployments
    enableAgentsCapabilityHost: enableFoundryAgentsCapabilityHost
    fineTuneModelDeploymentName: fineTuneModelDeploymentName
    fineTuneModelName: fineTuneModelName
    fineTuneModelVersion: fineTuneModelVersion
    fineTuneModelCapacity: fineTuneModelCapacity
    embeddingModelDeploymentName: embeddingModelDeploymentName
    embeddingModelName: embeddingModelName
    embeddingModelVersion: embeddingModelVersion
    embeddingModelCapacity: embeddingModelCapacity
    tags: tags
    enablePrivateEndpoint: vnetEnabled
    publicNetworkAccess: vnetEnabled ? 'Disabled' : 'Enabled'
    restoreFoundryAccount: restoreFoundryAccount
  }
}

// Private endpoint for Foundry
module foundryPrivateEndpoint 'app/foundry-PrivateEndpoint.bicep' = if (vnetEnabled) {
  name: 'foundryPrivateEndpoint'
  scope: rg
  params: {
    location: location
    tags: tags
    virtualNetworkName: serviceVirtualNetworkName
    subnetName: vnetEnabled ? serviceVirtualNetworkPrivateEndpointSubnetName : ''
    resourceName: foundry.outputs.foundryAccountName
  }
  dependsOn: [
    serviceVirtualNetwork
  ]
}

// Role assignment: Cognitive Services OpenAI User for MCP server identity
var CognitiveServicesOpenAIUser = '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd'
module foundryRoleAssignmentMcp './app/foundry-RoleAssignment.bicep' = {
  name: 'foundryRoleAssignmentMcp'
  scope: rg
  params: {
    foundryAccountName: foundry.outputs.foundryAccountName
    roleDefinitionID: CognitiveServicesOpenAIUser
    principalID: mcpUserAssignedIdentity.outputs.identityPrincipalId
  }
}

// Role assignment: Azure AI Developer for MCP server identity (required for evaluation SDK)
// This role includes Microsoft.CognitiveServices/accounts/AIServices/agents/write data action
var AzureAIDeveloper = '64702f94-c441-49e6-a78b-ef80e0188fee'
module foundryRoleAssignmentMcpAIDeveloper './app/foundry-RoleAssignment.bicep' = {
  name: 'foundryRoleAssignmentMcpAIDeveloper'
  scope: rg
  params: {
    foundryAccountName: foundry.outputs.foundryAccountName
    roleDefinitionID: AzureAIDeveloper
    principalID: mcpUserAssignedIdentity.outputs.identityPrincipalId
  }
}

// =========================================
// CosmosDB NoSQL for Task and Plan Storage
// =========================================

var cosmosResourceName = !empty(cosmosDbAccountName) ? cosmosDbAccountName : '${abbrs.documentDBDatabaseAccounts}${resourceToken}'

// CosmosDB Account with Vector Search enabled for embeddings
module cosmosAccount './core/cosmos-db/nosql/account.bicep' = {
  name: 'cosmosAccount'
  scope: rg
  params: {
    name: cosmosResourceName
    location: location
    tags: tags
    enableServerless: true
    enableVectorSearch: true
    disableKeyBasedAuth: true
    ipRules: !empty(developerIpAddress) ? [developerIpAddress] : []
  }
}

// CosmosDB Database
module cosmosDatabase './core/cosmos-db/nosql/database.bicep' = {
  name: 'cosmosDatabase'
  scope: rg
  params: {
    name: cosmosDatabaseName
    parentAccountName: cosmosAccount.outputs.name
    tags: tags
  }
}

// Container for storing tasks with embeddings
module cosmosTasksContainer './core/cosmos-db/nosql/container.bicep' = {
  name: 'cosmosTasksContainer'
  scope: rg
  params: {
    name: 'tasks'
    parentAccountName: cosmosAccount.outputs.name
    parentDatabaseName: cosmosDatabase.outputs.name
    partitionKeyPaths: ['/id']
    tags: tags
    vectorEmbeddingPolicy: {
      vectorEmbeddings: [
        {
          path: '/embedding'
          dataType: 'float32'
          dimensions: 3072
          distanceFunction: 'cosine'
        }
      ]
    }
    indexingPolicy: {
      automatic: true
      indexingMode: 'consistent'
      includedPaths: [
        {
          path: '/*'
        }
      ]
      excludedPaths: [
        {
          path: '/embedding/*'
        }
      ]
      vectorIndexes: [
        {
          path: '/embedding'
          type: 'quantizedFlat'
        }
      ]
    }
  }
}

// Container for storing planned steps
module cosmosPlansContainer './core/cosmos-db/nosql/container.bicep' = {
  name: 'cosmosPlansContainer'
  scope: rg
  params: {
    name: 'plans'
    parentAccountName: cosmosAccount.outputs.name
    parentDatabaseName: cosmosDatabase.outputs.name
    partitionKeyPaths: ['/taskId']
    tags: tags
    indexingPolicy: {
      automatic: true
      indexingMode: 'consistent'
      includedPaths: [
        {
          path: '/*'
        }
      ]
    }
  }
}

// Container for short-term memory with TTL support
module cosmosShortTermMemoryContainer './core/cosmos-db/nosql/container.bicep' = {
  name: 'cosmosShortTermMemoryContainer'
  scope: rg
  params: {
    name: 'chat'
    parentAccountName: cosmosAccount.outputs.name
    parentDatabaseName: cosmosDatabase.outputs.name
    partitionKeyPaths: ['/session_id']
    tags: tags
    vectorEmbeddingPolicy: {
      vectorEmbeddings: [
        {
          path: '/embedding'
          dataType: 'float32'
          dimensions: 3072
          distanceFunction: 'cosine'
        }
      ]
    }
    indexingPolicy: {
      automatic: true
      indexingMode: 'consistent'
      includedPaths: [
        {
          path: '/*'
        }
      ]
      excludedPaths: [
        {
          path: '/embedding/*'
        }
      ]
      vectorIndexes: [
        {
          path: '/embedding'
          type: 'quantizedFlat'
        }
      ]
    }
  }
}

// =========================================
// Quality Measures Workbench containers (under `dq` database)
// - catalog: measures + tags + regulatory-agencies (programs)
//            (docType discriminator: 'measure' | 'tag' | 'agency')
//            'agency' docs hold programs + reporting periods + required measures.
// - cohorts: cohort definitions + member FHIR bundles
//            (docType discriminator: 'cohort' | 'member')
//            'member' docs replace the legacy `clinical/patients` container.
// =========================================
module cosmosCatalogContainer './core/cosmos-db/nosql/container.bicep' = {
  name: 'cosmosCatalogContainer'
  scope: rg
  params: {
    name: 'catalog'
    parentAccountName: cosmosAccount.outputs.name
    parentDatabaseName: cosmosDatabase.outputs.name
    partitionKeyPaths: ['/docType']
    tags: tags
    indexingPolicy: {
      automatic: true
      indexingMode: 'consistent'
      includedPaths: [
        { path: '/*' }
      ]
    }
  }
}

module cosmosCohortsContainer './core/cosmos-db/nosql/container.bicep' = {
  name: 'cosmosCohortsContainer'
  scope: rg
  params: {
    name: 'cohorts'
    parentAccountName: cosmosAccount.outputs.name
    parentDatabaseName: cosmosDatabase.outputs.name
    partitionKeyPaths: ['/docType']
    tags: tags
    indexingPolicy: {
      automatic: true
      indexingMode: 'consistent'
      includedPaths: [
        { path: '/*' }
      ]
    }
  }
}

// =========================================
// Azure SQL Database for receiver reporting and Power BI analytics
// =========================================
module receiverReportingSql './core/sql/sql-database.bicep' = {
  name: 'receiverReportingSql'
  scope: rg
  params: {
    serverName: sqlReportingServerName
    databaseName: sqlReportingDatabaseName
    location: location
    tags: tags
    administratorLogin: sqlAdministratorLogin
    administratorPassword: sqlAdministratorPassword
    publicNetworkAccess: vnetEnabled ? 'Disabled' : 'Enabled'
    developerIpAddress: developerIpAddress
    skuName: sqlDatabaseSkuName
    skuTier: sqlDatabaseSkuTier
    entraAdminObjectId: sqlEntraAdminObjectId
    entraAdminLogin: sqlEntraAdminLogin
    entraAdminTenantId: tenant().tenantId
    azureAdOnlyAuthentication: sqlAzureAdOnlyAuthentication
  }
}

module receiverReportingSqlPrivateEndpoint './app/sql-PrivateEndpoint.bicep' = if (vnetEnabled) {
  name: 'receiverReportingSqlPrivateEndpoint'
  scope: rg
  params: {
    location: location
    tags: tags
    virtualNetworkName: serviceVirtualNetworkName
    subnetName: vnetEnabled ? serviceVirtualNetworkPrivateEndpointSubnetName : ''
    serverName: receiverReportingSql.outputs.serverName
  }
  dependsOn: [
    serviceVirtualNetwork
  ]
}

// =========================================
// Agent Learning Cosmos DB Resources
// Database and containers for the Agent Learning SDK's RL loop.
// Database name `dq_rl` aligns with the dq workbench naming.
// =========================================
module learningCosmos './app/learning-cosmos.bicep' = {
  name: 'learningCosmos'
  scope: rg
  params: {
    parentAccountName: cosmosAccount.outputs.name
    databaseName: 'dq_rl'
    tags: tags
  }
}

// NOTE: The legacy `clinical` database (with the `patients` container) and
// the `mcpdb` database have been retired. FHIR bundles now live in
// `dq/cohorts` (docType=member). If either database still exists in a
// deployed Cosmos account, delete it manually after running the migration
// script in scripts/ — Bicep no longer manages them.

// Private endpoint for CosmosDB
module cosmosPrivateEndpoint 'app/cosmos-PrivateEndpoint.bicep' = if (vnetEnabled) {
  name: 'cosmosPrivateEndpoint'
  scope: rg
  params: {
    location: location
    tags: tags
    virtualNetworkName: serviceVirtualNetworkName
    subnetName: vnetEnabled ? serviceVirtualNetworkPrivateEndpointSubnetName : ''
    resourceName: cosmosAccount.outputs.name
  }
  dependsOn: [
    serviceVirtualNetwork
  ]
}

// RBAC: Cosmos DB Built-in Data Contributor role for MCP server identity
// Built-in role ID: 00000000-0000-0000-0000-000000000002
var CosmosDBDataContributor = '00000000-0000-0000-0000-000000000002'
module cosmosRoleAssignmentMcp './app/cosmos-RoleAssignment.bicep' = {
  name: 'cosmosRoleAssignmentMcp'
  scope: rg
  params: {
    cosmosAccountName: cosmosAccount.outputs.name
    roleDefinitionID: CosmosDBDataContributor
    principalID: mcpUserAssignedIdentity.outputs.identityPrincipalId
  }
}

// RBAC: Cosmos DB Built-in Data Contributor role for developer (local development)
module cosmosRoleAssignmentDeveloper './app/cosmos-RoleAssignment.bicep' = if (!empty(developerPrincipalId)) {
  name: 'cosmosRoleAssignmentDeveloper'
  scope: rg
  params: {
    cosmosAccountName: cosmosAccount.outputs.name
    roleDefinitionID: CosmosDBDataContributor
    principalID: developerPrincipalId
    principalType: 'User'
  }
}

// RBAC: Cosmos DB Built-in Data Contributor role for AKS kubelet identity
module cosmosRoleAssignmentAksKubelet './app/cosmos-RoleAssignment.bicep' = {
  name: 'cosmosRoleAssignmentAksKubelet'
  scope: rg
  params: {
    cosmosAccountName: cosmosAccount.outputs.name
    roleDefinitionID: CosmosDBDataContributor
    principalID: aksCluster.outputs.kubeletIdentityObjectId
  }
}



// Monitor application with Azure Monitor
module monitoring './core/monitor/monitoring.bicep' = {
  name: 'monitoring'
  scope: rg
  params: {
    location: location
    tags: tags
    logAnalyticsName: !empty(logAnalyticsName) ? logAnalyticsName : '${abbrs.operationalInsightsWorkspaces}${resourceToken}'
    applicationInsightsName: !empty(applicationInsightsName) ? applicationInsightsName : '${abbrs.insightsComponents}${resourceToken}'
    disableLocalAuth: disableLocalAuth  
  }
}

var monitoringRoleDefinitionId = '3913510d-42f4-4e42-8a64-420c390055eb' // Monitoring Metrics Publisher role ID

// Prometheus Data Collection Rule Association with AKS
// This enables Prometheus metrics scraping from the AKS cluster
module prometheusDcrAssociation './core/monitor/prometheus-dcr-association.bicep' = if (grafanaEnabled) {
  name: 'prometheusDcrAssociation'
  scope: rg
  params: {
    aksClusterName: aksCluster.outputs.aksClusterName
    dataCollectionRuleId: azureMonitorWorkspace!.outputs.dataCollectionRuleId
    dataCollectionEndpointId: azureMonitorWorkspace!.outputs.dataCollectionEndpointId
  }
}

// =========================================
// Azure Managed Grafana for AKS Monitoring
// =========================================
var grafanaResourceName = !empty(grafanaName) ? grafanaName : 'amg-${resourceToken}'

// Azure Managed Grafana with Azure Monitor Workspace (Prometheus) integration
module grafana './core/monitor/grafana.bicep' = if (grafanaEnabled) {
  name: 'grafana'
  scope: rg
  params: {
    grafanaName: grafanaResourceName
    location: location
    tags: tags
    skuName: 'Standard'
    publicNetworkAccess: 'Enabled'
    enableSystemAssignedIdentity: true
    azureMonitorWorkspaceId: azureMonitorWorkspace!.outputs.id
  }
}

// Role assignments for Grafana to access monitoring data
// Monitoring Reader role on Log Analytics Workspace
var MonitoringReader = '43d0d8ad-25c7-4714-9337-8ba259a9fe05'
module grafanaLogAnalyticsRole './app/grafana-RoleAssignment.bicep' = if (grafanaEnabled) {
  name: 'grafanaLogAnalyticsRole'
  scope: rg
  params: {
    resourceName: monitoring.outputs.logAnalyticsWorkspaceName
    resourceType: 'logAnalytics'
    roleDefinitionID: MonitoringReader
    principalID: grafana!.outputs.principalId
  }
}

// Reader role on AKS cluster for Grafana
var ReaderRole = 'acdd72a7-3385-48ef-bd42-f606fba81ae7'
module grafanaAksRole './app/grafana-RoleAssignment.bicep' = if (grafanaEnabled) {
  name: 'grafanaAksRole'
  scope: rg
  params: {
    resourceName: aksCluster.outputs.aksClusterName
    resourceType: 'aks'
    roleDefinitionID: ReaderRole
    principalID: grafana!.outputs.principalId
  }
}

// Monitoring Reader role on Application Insights
module grafanaAppInsightsRole './app/grafana-RoleAssignment.bicep' = if (grafanaEnabled) {
  name: 'grafanaAppInsightsRole'
  scope: rg
  params: {
    resourceName: monitoring.outputs.applicationInsightsName
    resourceType: 'appInsights'
    roleDefinitionID: MonitoringReader
    principalID: grafana!.outputs.principalId
  }
}

// Monitoring Data Reader role on Azure Monitor Workspace (for Prometheus)
var MonitoringDataReader = 'b0d8363b-8ddd-447d-831f-62ca05bff136'
module grafanaAzureMonitorWorkspaceRole './app/grafana-RoleAssignment.bicep' = if (grafanaEnabled) {
  name: 'grafanaAzureMonitorWorkspaceRole'
  scope: rg
  params: {
    resourceName: azureMonitorWorkspace!.outputs.name
    resourceType: 'azureMonitorWorkspace'
    roleDefinitionID: MonitoringDataReader
    principalID: grafana!.outputs.principalId
  }
}

// Allow access from MCP server workload identity to application insights
module appInsightsRoleAssignmentMcp './core/monitor/appinsights-access.bicep' = {
  name: 'appInsightsRoleAssignmentMcp'
  scope: rg
  params: {
    appInsightsName: monitoring.outputs.applicationInsightsName
    roleDefinitionID: monitoringRoleDefinitionId
    principalID: mcpUserAssignedIdentity.outputs.identityPrincipalId
  }
}

// =========================================
// Digital Quality Orchestrator Agent Identity Role Assignments
// =========================================
// Comprehensive role assignments for the Entra Agent Identity
// Grants access to: CosmosDB, Storage, Foundry Project

module agentRoleAssignments './app/agent-RoleAssignments.bicep' = if (agentIdentityEnabled) {
  name: 'agentRoleAssignments'
  scope: rg
  params: {
    agentPrincipalId: digitalQualityOrchestratorAgentIdentity!.outputs.agentIdentityPrincipalId
    cosmosAccountName: cosmosAccount.outputs.name
    storageAccountName: storage.outputs.name
    foundryAccountName: foundry.outputs.foundryAccountName
  }
}

// Allow access from Agent Identity to application insights (monitoring)
module appInsightsRoleAssignmentAgent './core/monitor/appinsights-access.bicep' = if (agentIdentityEnabled) {
  name: 'appInsightsRoleAssignmentAgent'
  scope: rg
  params: {
    appInsightsName: monitoring.outputs.applicationInsightsName
    roleDefinitionID: monitoringRoleDefinitionId
    principalID: digitalQualityOrchestratorAgentIdentity!.outputs.agentIdentityPrincipalId
  }
}

// =========================================
// Agents Approval Logic App
// =========================================
// Deploys Azure Logic App for Teams-based approval workflow with CosmosDB audit logging
module agentsApprovalLogicApp './app/agents-approval-logicapp.bicep' = if (approvalLogicAppEnabled) {
  name: 'agentsApprovalLogicApp'
  scope: rg
  params: {
    logicAppName: '${abbrs.logicWorkflows}approval-${resourceToken}'
    location: location
    tags: tags
    cosmosDbAccountName: cosmosAccount.outputs.name
    cosmosDbDatabaseName: cosmosDatabaseName
    cosmosDbContainerName: 'approvals'
    teamsChannelId: teamsChannelId
    teamsGroupId: teamsGroupId
    approvalTimeoutHours: approvalTimeoutHours
    userAssignedIdentityId: mcpUserAssignedIdentity.outputs.identityId
  }
}

// =========================================
// Microsoft Defender for Cloud
// =========================================
// Deploy Defender for Cloud plans for threat protection and security monitoring
module defender './core/security/defender.bicep' = if (defenderEnabled && !empty(defenderSecurityContactEmail)) {
  name: 'defender'
  params: {
    securityContactEmail: defenderSecurityContactEmail
    securityContactPhone: defenderSecurityContactPhone
    enableDefenderForContainers: defenderForContainersEnabled
    enableDefenderForKeyVault: defenderForKeyVaultEnabled
    enableDefenderForCosmosDB: defenderForCosmosDBEnabled
    enableDefenderForAPIs: defenderForAPIsEnabled
    enableDefenderForResourceManager: defenderForResourceManagerEnabled
    enableDefenderForContainerRegistry: defenderForContainerRegistryEnabled
  }
}

// =========================================
// Microsoft Purview
// =========================================
// Deploy Microsoft Purview for data governance, classification, lineage, and compliance
var purviewResourceName = !empty(purviewAccountName) ? purviewAccountName : 'purview-${resourceToken}'

module purviewAccount './core/purview/purview.bicep' = if (purviewEnabled) {
  name: 'purviewAccount'
  scope: rg
  params: {
    name: purviewResourceName
    location: location
    tags: tags
    managedIdentityType: 'SystemAssigned'
    publicNetworkAccess: vnetEnabled ? 'Disabled' : 'Enabled'
  }
}

// Purview Private Endpoints (when VNet is enabled)
module purviewPrivateEndpoint './app/purview-PrivateEndpoint.bicep' = if (purviewEnabled && vnetEnabled) {
  name: 'purviewPrivateEndpoint'
  scope: rg
  params: {
    location: location
    tags: tags
    virtualNetworkName: serviceVirtualNetworkName
    subnetName: vnetEnabled ? serviceVirtualNetworkPrivateEndpointSubnetName : ''
    resourceName: purviewResourceName
  }
  dependsOn: [
    serviceVirtualNetwork
    purviewAccount
  ]
}

// Built-in Purview RBAC roles
// Purview Data Reader - Read access to data catalog
var PurviewDataReader = '4c48d476-69c1-41d0-88c2-9ac66e4b64f4'

// Assign Purview Data Reader role to agent identity for runtime classification checks
module purviewAgentRole './app/purview-RoleAssignment.bicep' = if (purviewEnabled && agentIdentityEnabled) {
  name: 'purviewAgentRole'
  scope: rg
  params: {
    purviewAccountName: purviewResourceName
    roleDefinitionID: PurviewDataReader
    principalID: digitalQualityOrchestratorAgentIdentity!.outputs.agentIdentityPrincipalId
    principalType: 'ServicePrincipal'
  }
}

// Grant Purview managed identity access to scan Cosmos DB data sources
module purviewScanCosmosRole './app/cosmos-RoleAssignment.bicep' = if (purviewEnabled) {
  name: 'purviewScanCosmosRole'
  scope: rg
  params: {
    cosmosAccountName: cosmosAccount.outputs.name
    roleDefinitionID: '00000000-0000-0000-0000-000000000001'  // Cosmos DB Built-in Data Reader
    principalID: purviewAccount!.outputs.principalId
    principalType: 'ServicePrincipal'
  }
}


// App outputs
output APPLICATIONINSIGHTS_CONNECTION_STRING string = monitoring.outputs.applicationInsightsConnectionString
output AZURE_LOCATION string = location
output AZURE_TENANT_ID string = tenant().tenantId
output AKS_CLUSTER_NAME string = aksCluster.outputs.aksClusterName
output CONTAINER_REGISTRY string = containerRegistry.outputs.containerRegistryLoginServer
output AZURE_STORAGE_ACCOUNT_URL string = storage.outputs.primaryEndpoints.blob
output MCP_SERVER_IDENTITY_CLIENT_ID string = mcpUserAssignedIdentity.outputs.identityClientId
output SERVICE_API_ENDPOINTS array = [ '${apimService.outputs.gatewayUrl}/mcp/sse' ]
output APIM_GATEWAY_URL string = apimService.outputs.gatewayUrl
output MCP_BASE_URL string = '${apimService.outputs.gatewayUrl}/mcp'
output MCP_OAUTH_AUTHORIZE_URL string = '${apimService.outputs.gatewayUrl}/mcp/oauth/authorize'
output MCP_OAUTH_TOKEN_URL string = '${apimService.outputs.gatewayUrl}/mcp/oauth/token'
output MCP_CLIENT_ID string = existingEntraAppId
output AZURE_RESOURCE_GROUP_NAME string = rg.name
output AZURE_SUBSCRIPTION_ID string = subscription().subscriptionId


// Foundry outputs
output FOUNDRY_PROJECT_ENDPOINT string = foundry.outputs.projectEndpoint
output FOUNDRY_MODEL_DEPLOYMENT_NAME string = foundryModelDeploymentName
output FOUNDRY_ACCOUNT_NAME string = foundry.outputs.foundryAccountName
output EMBEDDING_MODEL_DEPLOYMENT_NAME string = embeddingModelDeploymentName

// Azure SQL reporting outputs
output AZURE_SQL_SERVER_NAME string = receiverReportingSql.outputs.serverName
output AZURE_SQL_DATABASE_NAME string = receiverReportingSql.outputs.databaseName
output AZURE_SQL_SERVER_FQDN string = receiverReportingSql.outputs.fullyQualifiedDomainName
output AZURE_SQL_CONNECTION_STRING string = 'Driver={ODBC Driver 18 for SQL Server};Server=tcp:${receiverReportingSql.outputs.fullyQualifiedDomainName},1433;Database=${receiverReportingSql.outputs.databaseName};Encrypt=yes;TrustServerCertificate=no;Authentication=ActiveDirectoryMsi;UID=${mcpUserAssignedIdentity.outputs.identityClientId};'

// CosmosDB outputs
output COSMOSDB_ENDPOINT string = cosmosAccount.outputs.endpoint
output COSMOSDB_DATABASE_NAME string = cosmosDatabaseName
output COSMOSDB_ACCOUNT_NAME string = cosmosAccount.outputs.name

// Quality Measures Workbench container outputs (under `dq` database)
// `regulatory-agencies` (programs) is a logical doc-type inside `catalog`
// (docType=agency) — it is not a separate container.
output COSMOSDB_CATALOG_COLLECTION string = cosmosCatalogContainer.outputs.name
output COSMOSDB_COHORTS_COLLECTION string = cosmosCohortsContainer.outputs.name



// =========================================
// Entra Agent Identity outputs
// =========================================
output AGENT_IDENTITY_ENABLED bool = agentIdentityEnabled
output AGENT_IDENTITY_BLUEPRINT_APP_ID string = agentIdentityEnabled ? agentIdentityBlueprint!.outputs.blueprintAppId : ''
output AGENT_IDENTITY_APP_ID string = agentIdentityEnabled ? digitalQualityOrchestratorAgentIdentity!.outputs.agentIdentityAppId : ''
output AGENT_IDENTITY_PRINCIPAL_ID string = agentIdentityEnabled ? digitalQualityOrchestratorAgentIdentity!.outputs.agentIdentityPrincipalId : ''
output AGENT_IDENTITY_DISPLAY_NAME string = agentIdentityEnabled ? digitalQualityOrchestratorAgentIdentity!.outputs.agentDisplayName : ''

// =========================================
// Agents Approval Logic App outputs
// =========================================
output APPROVAL_LOGIC_APP_ENABLED bool = approvalLogicAppEnabled
output APPROVAL_LOGIC_APP_TRIGGER_URL string = approvalLogicAppEnabled ? agentsApprovalLogicApp!.outputs.logicAppTriggerUrl : ''
output APPROVAL_LOGIC_APP_NAME string = approvalLogicAppEnabled ? agentsApprovalLogicApp!.outputs.logicAppName : ''

// =========================================
// Azure Managed Grafana outputs
// =========================================
output GRAFANA_ENABLED bool = grafanaEnabled
output GRAFANA_NAME string = grafanaEnabled ? grafana!.outputs.name : ''
output GRAFANA_ENDPOINT string = grafanaEnabled ? grafana!.outputs.endpoint : ''
output GRAFANA_RESOURCE_ID string = grafanaEnabled ? grafana!.outputs.id : ''

// =========================================
// Microsoft Defender for Cloud outputs
// =========================================
output DEFENDER_ENABLED bool = defenderEnabled
output DEFENDER_DEPLOYED bool = defenderEnabled && !empty(defenderSecurityContactEmail)
output DEFENDER_FOR_CONTAINERS_ENABLED bool = defenderForContainersEnabled
output DEFENDER_FOR_KEY_VAULT_ENABLED bool = defenderForKeyVaultEnabled
output DEFENDER_FOR_COSMOS_DB_ENABLED bool = defenderForCosmosDBEnabled
output DEFENDER_FOR_APIS_ENABLED bool = defenderForAPIsEnabled
output DEFENDER_FOR_RESOURCE_MANAGER_ENABLED bool = defenderForResourceManagerEnabled

// =========================================
// Microsoft Purview outputs
// =========================================
output PURVIEW_ENABLED bool = purviewEnabled
output PURVIEW_ACCOUNT_NAME string = purviewEnabled ? purviewAccount!.outputs.name : ''
output PURVIEW_ENDPOINT string = purviewEnabled ? purviewAccount!.outputs.endpoint : ''
output PURVIEW_CATALOG_ENDPOINT string = purviewEnabled ? purviewAccount!.outputs.catalogEndpoint : ''
output PURVIEW_SCAN_ENDPOINT string = purviewEnabled ? purviewAccount!.outputs.scanEndpoint : ''
output PURVIEW_MANAGED_RESOURCE_GROUP string = purviewEnabled ? purviewAccount!.outputs.managedResourceGroupName : ''


