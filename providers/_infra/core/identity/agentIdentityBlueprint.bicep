@description('Display name for the Entra Agent Identity Blueprint application')
param blueprintDisplayName string

@description('Unique name for the Entra Agent Identity Blueprint (used for identifierUri)')
param blueprintUniqueName string

@description('Location for deployment scripts')
param location string = resourceGroup().location

@description('Tags for resources')
param tags object = {}

@description('Principal IDs of sponsors (users or groups) for the agent identity blueprint')
param sponsorPrincipalIds array = []

@description('Principal IDs of owners for the agent identity blueprint')
param ownerPrincipalIds array = []

@description('Client ID of the managed identity to use as federated credential for the blueprint')
param federatedIdentityClientId string

@description('Principal ID of the managed identity to use as federated credential')
param federatedIdentityPrincipalId string

@description('Tenant ID for the Entra ID tenant')
param tenantId string = tenant().tenantId

@description('OAuth2 scope value for the agent (default: access_agent)')
param agentScopeValue string = 'access_agent'

// Entra login endpoint - uses environment() for cloud compatibility
var entraLoginEndpoint = environment().authentication.loginEndpoint

// Unique identifier for the deployment script
var deploymentScriptName = 'ds-agent-blueprint-${uniqueString(blueprintUniqueName)}'

// Deployment script to create the Agent Identity Blueprint via Microsoft Graph API
// Note: This requires appropriate permissions (AgentIdentityBlueprint.Create, AgentIdentityBlueprint.AddRemoveCreds.All, AgentIdentityBlueprint.ReadWrite.All)
resource agentBlueprintScript 'Microsoft.Resources/deploymentScripts@2023-08-01' = {
  name: deploymentScriptName
  location: location
  tags: tags
  kind: 'AzurePowerShell'
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${resourceId('Microsoft.ManagedIdentity/userAssignedIdentities', federatedIdentityClientId)}': {}
    }
  }
  properties: {
    azPowerShellVersion: '12.0'
    timeout: 'PT30M'
    retentionInterval: 'P1D'
    cleanupPreference: 'OnSuccess'
    arguments: '-BlueprintDisplayName "${blueprintDisplayName}" -BlueprintUniqueName "${blueprintUniqueName}" -TenantId "${tenantId}" -FederatedIdentityPrincipalId "${federatedIdentityPrincipalId}" -SponsorIds "${join(sponsorPrincipalIds, ',')}" -OwnerIds "${join(ownerPrincipalIds, ',')}" -AgentScopeValue "${agentScopeValue}" -EntraLoginEndpoint "${entraLoginEndpoint}"'
    scriptContent: '''
      param(
        [string]$BlueprintDisplayName,
        [string]$BlueprintUniqueName,
        [string]$TenantId,
        [string]$FederatedIdentityPrincipalId,
        [string]$SponsorIds,
        [string]$OwnerIds,
        [string]$AgentScopeValue,
        [string]$EntraLoginEndpoint
      )

      # Install Microsoft.Graph modules if needed
      $modules = @('Microsoft.Graph.Authentication', 'Microsoft.Graph.Applications')
      foreach ($module in $modules) {
        if (-not (Get-Module -ListAvailable -Name $module)) {
          Install-Module -Name $module -Force -Scope CurrentUser -AllowClobber
        }
        Import-Module $module -Force
      }

      # Connect to Microsoft Graph using managed identity
      Connect-MgGraph -Identity -NoWelcome

      # Prepare sponsors array
      $sponsors = @()
      if ($SponsorIds -and $SponsorIds -ne "") {
        $sponsorIdList = $SponsorIds -split ','
        foreach ($id in $sponsorIdList) {
          if ($id.Trim()) {
            $sponsors += "https://graph.microsoft.com/v1.0/users/$($id.Trim())"
          }
        }
      }

      # Prepare owners array
      $owners = @()
      if ($OwnerIds -and $OwnerIds -ne "") {
        $ownerIdList = $OwnerIds -split ','
        foreach ($id in $ownerIdList) {
          if ($id.Trim()) {
            $owners += "https://graph.microsoft.com/v1.0/users/$($id.Trim())"
          }
        }
      }

      # Check if blueprint already exists
      $existingApp = Get-MgApplication -Filter "displayName eq '$BlueprintDisplayName'" -ErrorAction SilentlyContinue | Where-Object { $_.AdditionalProperties.'@odata.type' -eq '#microsoft.graph.agentIdentityBlueprint' }

      if ($existingApp) {
        Write-Host "Agent Identity Blueprint already exists with App ID: $($existingApp.AppId)"
        $blueprintAppId = $existingApp.AppId
        $blueprintObjectId = $existingApp.Id
      } else {
        # Create Agent Identity Blueprint application
        $blueprintBody = @{
          '@odata.type' = '#microsoft.graph.agentIdentityBlueprint'
          displayName = $BlueprintDisplayName
        }

        if ($sponsors.Count -gt 0) {
          $blueprintBody['sponsors@odata.bind'] = $sponsors
        }
        if ($owners.Count -gt 0) {
          $blueprintBody['owners@odata.bind'] = $owners
        }

        # Create the blueprint via Graph API
        $headers = @{
          'OData-Version' = '4.0'
          'Content-Type' = 'application/json'
        }

        try {
          $blueprintResponse = Invoke-MgGraphRequest -Method POST -Uri 'https://graph.microsoft.com/beta/applications' -Body ($blueprintBody | ConvertTo-Json -Depth 10) -Headers $headers
          $blueprintAppId = $blueprintResponse.appId
          $blueprintObjectId = $blueprintResponse.id
          Write-Host "Created Agent Identity Blueprint with App ID: $blueprintAppId"
        } catch {
          Write-Error "Failed to create Agent Identity Blueprint: $_"
          throw
        }

        # Create Service Principal for the blueprint
        $spBody = @{
          appId = $blueprintAppId
        }

        try {
          $spResponse = Invoke-MgGraphRequest -Method POST -Uri 'https://graph.microsoft.com/beta/serviceprincipals/graph.agentIdentityBlueprintPrincipal' -Body ($spBody | ConvertTo-Json) -Headers $headers
          Write-Host "Created Service Principal for Agent Identity Blueprint"
        } catch {
          Write-Warning "Service Principal may already exist or failed to create: $_"
        }

        # Add federated identity credential for managed identity
        $fedCredBody = @{
          name = 'mcp-agent-msi'
          issuer = "$EntraLoginEndpoint$TenantId/v2.0"
          subject = $FederatedIdentityPrincipalId
          audiences = @('api://AzureADTokenExchange')
        }

        try {
          Invoke-MgGraphRequest -Method POST -Uri "https://graph.microsoft.com/beta/applications/$blueprintObjectId/federatedIdentityCredentials" -Body ($fedCredBody | ConvertTo-Json) -Headers $headers
          Write-Host "Added federated identity credential for managed identity"
        } catch {
          Write-Warning "Federated credential may already exist: $_"
        }

        # Configure identifier URI and OAuth2 scope
        $scopeId = [guid]::NewGuid().ToString()
        $updateBody = @{
          identifierUris = @("api://$blueprintAppId")
          api = @{
            oauth2PermissionScopes = @(
              @{
                adminConsentDescription = "Allow the application to access the agent on behalf of the signed-in user."
                adminConsentDisplayName = "Access Agent"
                id = $scopeId
                isEnabled = $true
                type = "User"
                value = $AgentScopeValue
              }
            )
          }
        }

        try {
          Invoke-MgGraphRequest -Method PATCH -Uri "https://graph.microsoft.com/beta/applications/$blueprintObjectId" -Body ($updateBody | ConvertTo-Json -Depth 10) -Headers $headers
          Write-Host "Configured identifier URI and OAuth2 scope"
        } catch {
          Write-Warning "Failed to configure identifier URI: $_"
        }
      }

      # Output results
      $DeploymentScriptOutputs = @{}
      $DeploymentScriptOutputs['blueprintAppId'] = $blueprintAppId
      $DeploymentScriptOutputs['blueprintObjectId'] = $blueprintObjectId
      $DeploymentScriptOutputs['identifierUri'] = "api://$blueprintAppId"

      Write-Host "Agent Identity Blueprint setup complete"
      Write-Host "Blueprint App ID: $blueprintAppId"
      Write-Host "Blueprint Object ID: $blueprintObjectId"
    '''
  }
}

// Outputs
output blueprintAppId string = agentBlueprintScript.properties.outputs.blueprintAppId
output blueprintObjectId string = agentBlueprintScript.properties.outputs.blueprintObjectId
output identifierUri string = agentBlueprintScript.properties.outputs.identifierUri
