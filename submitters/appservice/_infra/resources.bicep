metadata description = '''
Headless Azure App Service deployment for the Submitters backend.

Provisions everything the FastAPI submitter API needs to run WITHOUT Docker
or AKS, for customers (e.g. MultiCare) that cannot support containers or
Kubernetes:

  - Log Analytics workspace + workspace-based Application Insights
  - User-assigned managed identity (used for Cosmos DB data-plane RBAC and
    as the token audience for the backend's Entra ID JWT validation, mirroring
    the AKS workload-identity model)
  - Cosmos DB for NoSQL (serverless, AAD-only / local auth disabled) with the
    `catalog` and `cohorts` containers the backend initializes at startup
  - Linux App Service Plan + Web App running Python 3.11 natively (Oryx build,
    no container image), tagged so `azd deploy` publishes the backend source

This module is intentionally self-contained and does NOT reuse the AKS/VNet
core modules under submitters/_infra, whose Cosmos account defaults to private
networking. Here Cosmos keeps public network access enabled but is locked to
AAD RBAC only, so the App Service can reach it over its managed identity
without a VNet + private endpoint.
'''

@description('Primary location for all resources.')
param location string

@description('Tags applied to every resource (includes azd-env-name).')
param tags object = {}

@description('Short unique token used to name globally-unique resources.')
param resourceToken string

@description('azd service name; must match the service key in azure.yaml so azd deploys code to the Web App.')
param serviceName string = 'submitter'

@description('Value for the backend DEVELOPMENT_MODE flag. Keep "false" for production (Entra ID auth enforced).')
param developmentMode string = 'false'

@description('Base URL of an external Digital Quality Orchestrator, if available. Leave empty to run the core submitter API without measure delegation.')
param orchestratorBaseUrl string = ''

@description('Cosmos SQL database name the backend reads from.')
param cosmosDatabaseName string = 'dq'

@description('App Service Plan SKU name (e.g. B2, S1, P1v3).')
param appServicePlanSkuName string = 'B2'

@description('Optional object ID of a developer/admin to also grant Cosmos data-plane access for local debugging.')
param developerPrincipalId string = ''

// ---------------------------------------------------------------------------
// Naming
// ---------------------------------------------------------------------------
var logAnalyticsName = 'log-${resourceToken}'
var appInsightsName = 'appi-${resourceToken}'
var identityName = 'id-submitter-${resourceToken}'
var cosmosAccountName = 'cosmos-${resourceToken}'
var appServicePlanName = 'plan-${resourceToken}'
var appServiceName = 'app-submitter-${resourceToken}'

// Cosmos DB built-in "Data Contributor" data-plane role.
var cosmosDataContributorRoleId = '00000000-0000-0000-0000-000000000002'

// Map SKU name -> tier so the plan is valid across Basic/Standard/PremiumV3.
var skuTierMap = {
  B1: 'Basic'
  B2: 'Basic'
  B3: 'Basic'
  S1: 'Standard'
  S2: 'Standard'
  S3: 'Standard'
  P1v3: 'PremiumV3'
  P2v3: 'PremiumV3'
  P3v3: 'PremiumV3'
}
var appServicePlanTier = skuTierMap[?appServicePlanSkuName] ?? 'Basic'

// ---------------------------------------------------------------------------
// Monitoring
// ---------------------------------------------------------------------------
resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: logAnalyticsName
  location: location
  tags: tags
  properties: {
    retentionInDays: 30
    sku: {
      name: 'PerGB2018'
    }
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: appInsightsName
  location: location
  tags: tags
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logAnalytics.id
    IngestionMode: 'LogAnalytics'
  }
}

// ---------------------------------------------------------------------------
// Managed identity (Cosmos RBAC + backend JWT audience)
// ---------------------------------------------------------------------------
resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-07-31-preview' = {
  name: identityName
  location: location
  tags: tags
}

// ---------------------------------------------------------------------------
// Cosmos DB (serverless, AAD-only)
// ---------------------------------------------------------------------------
resource cosmos 'Microsoft.DocumentDB/databaseAccounts@2024-05-15' = {
  name: cosmosAccountName
  location: location
  tags: tags
  kind: 'GlobalDocumentDB'
  properties: {
    databaseAccountOfferType: 'Standard'
    consistencyPolicy: {
      defaultConsistencyLevel: 'Session'
    }
    locations: [
      {
        locationName: location
        failoverPriority: 0
        isZoneRedundant: false
      }
    ]
    // AAD-only: no account keys, so public network access is safe (RBAC-gated).
    disableLocalAuth: true
    publicNetworkAccess: 'Enabled'
    enableAutomaticFailover: false
    enableMultipleWriteLocations: false
    capabilities: [
      {
        name: 'EnableServerless'
      }
    ]
  }
}

resource cosmosDatabase 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases@2024-05-15' = {
  name: cosmosDatabaseName
  parent: cosmos
  tags: tags
  properties: {
    resource: {
      id: cosmosDatabaseName
    }
    options: {}
  }
}

// Containers the backend initializes at startup (both partitioned by /docType).
resource catalogContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-05-15' = {
  name: 'catalog'
  parent: cosmosDatabase
  tags: tags
  properties: {
    resource: {
      id: 'catalog'
      partitionKey: {
        paths: ['/docType']
        kind: 'Hash'
      }
    }
    options: {}
  }
}

resource cohortsContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-05-15' = {
  name: 'cohorts'
  parent: cosmosDatabase
  tags: tags
  properties: {
    resource: {
      id: 'cohorts'
      partitionKey: {
        paths: ['/docType']
        kind: 'Hash'
      }
    }
    options: {}
  }
}

// Data-plane RBAC: let the Web App identity read/write Cosmos documents.
resource cosmosRoleForApp 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-05-15' = {
  name: guid(cosmos.id, identity.id, cosmosDataContributorRoleId)
  parent: cosmos
  properties: {
    principalId: identity.properties.principalId
    roleDefinitionId: '${cosmos.id}/sqlRoleDefinitions/${cosmosDataContributorRoleId}'
    scope: cosmos.id
  }
}

// Optional: grant a developer/admin the same data-plane access for local debugging.
resource cosmosRoleForDeveloper 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-05-15' = if (!empty(developerPrincipalId)) {
  name: guid(cosmos.id, developerPrincipalId, cosmosDataContributorRoleId)
  parent: cosmos
  properties: {
    principalId: developerPrincipalId
    roleDefinitionId: '${cosmos.id}/sqlRoleDefinitions/${cosmosDataContributorRoleId}'
    scope: cosmos.id
  }
}

// ---------------------------------------------------------------------------
// App Service Plan + Web App (Linux, native Python — no container)
// ---------------------------------------------------------------------------
resource appServicePlan 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: appServicePlanName
  location: location
  tags: tags
  kind: 'linux'
  sku: {
    name: appServicePlanSkuName
    tier: appServicePlanTier
  }
  properties: {
    reserved: true
  }
}

// App settings mirror the env vars the FastAPI backend reads at startup.
var baseAppSettings = [
  // Enable Oryx remote build so requirements.txt is pip-installed on deploy.
  {
    name: 'SCM_DO_BUILD_DURING_DEPLOYMENT'
    value: 'true'
  }
  {
    name: 'ENABLE_ORYX_BUILD'
    value: 'true'
  }
  // AZURE_CLIENT_ID drives DefaultAzureCredential (Cosmos) and is the token
  // audience for the backend's Entra ID JWT validation (same as AKS).
  {
    name: 'AZURE_CLIENT_ID'
    value: identity.properties.clientId
  }
  {
    name: 'AZURE_TENANT_ID'
    value: tenant().tenantId
  }
  {
    name: 'REQUIRE_DATABASE'
    value: 'true'
  }
  {
    name: 'DEVELOPMENT_MODE'
    value: developmentMode
  }
  // Endpoint mode -> backend authenticates to Cosmos via managed identity.
  {
    name: 'COSMOSDB_ENDPOINT'
    value: cosmos.properties.documentEndpoint
  }
  {
    name: 'COSMOS_ENDPOINT'
    value: cosmos.properties.documentEndpoint
  }
  {
    name: 'COSMOSDB_DATABASE'
    value: cosmosDatabaseName
  }
  {
    name: 'COSMOSDB_CATALOG_COLLECTION'
    value: 'catalog'
  }
  {
    name: 'COSMOSDB_COHORTS_COLLECTION'
    value: 'cohorts'
  }
  {
    name: 'SAMPLE_DATA_DIR'
    value: '/home/site/wwwroot/data'
  }
  {
    name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
    value: appInsights.properties.ConnectionString
  }
  {
    name: 'APPINSIGHTS_CONNECTIONSTRING'
    value: appInsights.properties.ConnectionString
  }
]

// Only surface the orchestrator URL when the customer actually has one; an
// empty value would break the backend's measure-delegation URL building.
var appSettings = union(
  baseAppSettings,
  !empty(orchestratorBaseUrl)
    ? [
        {
          name: 'DIGITAL_QUALITY_ORCHESTRATOR_BASE_URL'
          value: orchestratorBaseUrl
        }
      ]
    : []
)

resource appService 'Microsoft.Web/sites@2023-12-01' = {
  name: appServiceName
  location: location
  // azd-service-name lets `azd deploy` publish the backend source to this app.
  tags: union(tags, {
    'azd-service-name': serviceName
  })
  kind: 'app,linux'
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${identity.id}': {}
    }
  }
  properties: {
    serverFarmId: appServicePlan.id
    httpsOnly: true
    reserved: true
    keyVaultReferenceIdentity: identity.id
    siteConfig: {
      linuxFxVersion: 'PYTHON|3.11'
      alwaysOn: true
      ftpsState: 'Disabled'
      minTlsVersion: '1.2'
      http20Enabled: true
      healthCheckPath: '/'
      // The backend package deploys to /home/site/wwwroot with the FastAPI app
      // under src/. --app-dir puts src on sys.path so `main:app` and its flat
      // sibling modules (cosmosdb_helper, auth_middleware, ...) import cleanly.
      appCommandLine: 'python -m uvicorn main:app --app-dir /home/site/wwwroot/src --host 0.0.0.0 --port 8000'
      appSettings: appSettings
    }
  }
}

output submitterName string = appService.name
output submitterUri string = 'https://${appService.properties.defaultHostName}'
output cosmosEndpoint string = cosmos.properties.documentEndpoint
output appInsightsConnectionString string = appInsights.properties.ConnectionString
output identityClientId string = identity.properties.clientId
