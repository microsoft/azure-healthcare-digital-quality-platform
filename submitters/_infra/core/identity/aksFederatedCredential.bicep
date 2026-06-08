@description('Name of the AKS cluster')
param aksClusterName string

@description('Namespace for the Kubernetes service account')
param serviceAccountNamespace string

@description('Name of the Kubernetes service account')
param serviceAccountName string

@description('App ID (client ID) of the Agent Identity or User Assigned Identity')
param identityClientId string

@description('Principal ID of the Agent Identity or User Assigned Identity')
param identityPrincipalId string

@description('Name for the federated credential')
param federatedCredentialName string

@description('Subject identifier for the service account (format: system:serviceaccount:namespace:serviceAccountName)')
param subjectIdentifier string = 'system:serviceaccount:${serviceAccountNamespace}:${serviceAccountName}'

@description('Location for deployment scripts')
param location string = resourceGroup().location

@description('Tags for resources')
param tags object = {}

@description('Resource ID of a managed identity with permissions to configure federated credentials')
param configurationIdentityResourceId string

// Get AKS cluster to obtain OIDC issuer URL
resource aksCluster 'Microsoft.ContainerService/managedClusters@2024-02-01' existing = {
  name: aksClusterName
}

// Unique identifier for the deployment script
var deploymentScriptName = 'ds-fed-cred-${uniqueString(identityClientId, serviceAccountName)}'

// Deployment script to create federated identity credential for the agent identity
// This allows AKS workloads to authenticate as the agent identity
resource federatedCredentialScript 'Microsoft.Resources/deploymentScripts@2023-08-01' = {
  name: deploymentScriptName
  location: location
  tags: tags
  kind: 'AzurePowerShell'
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${configurationIdentityResourceId}': {}
    }
  }
  properties: {
    azPowerShellVersion: '12.0'
    timeout: 'PT30M'
    retentionInterval: 'P1D'
    cleanupPreference: 'OnSuccess'
    arguments: '-IdentityPrincipalId "${identityPrincipalId}" -FederatedCredentialName "${federatedCredentialName}" -OidcIssuerUrl "${aksCluster.properties.oidcIssuerProfile.issuerURL}" -Subject "${subjectIdentifier}"'
    scriptContent: '''
      param(
        [string]$IdentityPrincipalId,
        [string]$FederatedCredentialName,
        [string]$OidcIssuerUrl,
        [string]$Subject
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

      # Get the service principal (agent identity)
      $sp = Get-MgServicePrincipal -ServicePrincipalId $IdentityPrincipalId -ErrorAction SilentlyContinue

      if (-not $sp) {
        Write-Error "Service Principal not found with ID: $IdentityPrincipalId"
        throw "Service principal not found"
      }

      # Get the associated application
      $app = Get-MgApplication -Filter "appId eq '$($sp.AppId)'" -ErrorAction SilentlyContinue

      if (-not $app) {
        # For agent identities, the federated credential is added to the blueprint, not the agent identity itself
        # Try to get the blueprint application
        Write-Host "Application not found for service principal. Agent identities inherit credentials from their blueprint."
        Write-Host "Skipping federated credential creation - ensure the blueprint has appropriate credentials configured."
        
        $DeploymentScriptOutputs = @{}
        $DeploymentScriptOutputs['status'] = 'skipped'
        $DeploymentScriptOutputs['message'] = 'Agent identities use blueprint credentials'
        return
      }

      # Check if federated credential already exists
      $existingCreds = Get-MgApplicationFederatedIdentityCredential -ApplicationId $app.Id -ErrorAction SilentlyContinue
      $existingCred = $existingCreds | Where-Object { $_.Name -eq $FederatedCredentialName }

      if ($existingCred) {
        $existingAudience = if ($existingCred.Audiences -and $existingCred.Audiences.Count -gt 0) { $existingCred.Audiences[0] } else { '' }
        $needsUpdate = ($existingCred.Issuer -ne $OidcIssuerUrl) -or ($existingCred.Subject -ne $Subject) -or ($existingAudience -ne 'api://AzureADTokenExchange')

        if (-not $needsUpdate) {
          Write-Host "Federated credential '$FederatedCredentialName' already matches desired issuer and subject"
          $DeploymentScriptOutputs = @{}
          $DeploymentScriptOutputs['status'] = 'exists'
          $DeploymentScriptOutputs['credentialId'] = $existingCred.Id
          return
        }

        Write-Host "Federated credential '$FederatedCredentialName' exists but is stale. Recreating with current issuer/subject."
        Remove-MgApplicationFederatedIdentityCredential -ApplicationId $app.Id -FederatedIdentityCredentialId $existingCred.Id
      }

      # Create federated identity credential
      $credBody = @{
        name = $FederatedCredentialName
        issuer = $OidcIssuerUrl
        subject = $Subject
        audiences = @('api://AzureADTokenExchange')
        description = "AKS Workload Identity for MCP Agent"
      }

      try {
        $headers = @{
          'OData-Version' = '4.0'
          'Content-Type' = 'application/json'
        }
        
        $newCred = Invoke-MgGraphRequest -Method POST -Uri "https://graph.microsoft.com/beta/applications/$($app.Id)/federatedIdentityCredentials" -Body ($credBody | ConvertTo-Json) -Headers $headers
        Write-Host "Created federated identity credential: $FederatedCredentialName"
        
        $DeploymentScriptOutputs = @{}
        $DeploymentScriptOutputs['status'] = 'created'
        $DeploymentScriptOutputs['credentialId'] = $newCred.id
      } catch {
        Write-Error "Failed to create federated identity credential: $_"
        throw
      }
    '''
  }
}

// Outputs
output oidcIssuerUrl string = aksCluster.properties.oidcIssuerProfile.issuerURL
output subjectIdentifier string = subjectIdentifier
