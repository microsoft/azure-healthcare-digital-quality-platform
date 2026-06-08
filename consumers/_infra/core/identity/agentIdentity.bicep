@description('Display name for the Entra Agent Identity')
param agentDisplayName string

@description('App ID of the Agent Identity Blueprint')
param blueprintAppId string

@description('Principal IDs of sponsors (users or groups) for the agent identity')
param sponsorPrincipalIds array = []

@description('Location for deployment scripts')
param location string = resourceGroup().location

@description('Tags for resources')
param tags object = {}

@description('Resource ID of the managed identity (full resource ID)')
param managedIdentityResourceId string

// Unique identifier for the deployment script
var deploymentScriptName = 'ds-agent-identity-${uniqueString(agentDisplayName)}'

// Deployment script to create the Agent Identity via Microsoft Graph API
resource agentIdentityScript 'Microsoft.Resources/deploymentScripts@2023-08-01' = {
  name: deploymentScriptName
  location: location
  tags: tags
  kind: 'AzurePowerShell'
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${managedIdentityResourceId}': {}
    }
  }
  properties: {
    azPowerShellVersion: '12.0'
    timeout: 'PT30M'
    retentionInterval: 'P1D'
    cleanupPreference: 'OnSuccess'
    arguments: '-AgentDisplayName "${agentDisplayName}" -BlueprintAppId "${blueprintAppId}" -SponsorIds "${join(sponsorPrincipalIds, ',')}"'
    scriptContent: '''
      param(
        [string]$AgentDisplayName,
        [string]$BlueprintAppId,
        [string]$SponsorIds
      )

      # Install Microsoft.Graph modules if needed
      $modules = @('Microsoft.Graph.Authentication')
      foreach ($module in $modules) {
        if (-not (Get-Module -ListAvailable -Name $module)) {
          Install-Module -Name $module -Force -Scope CurrentUser -AllowClobber
        }
        Import-Module $module -Force
      }

      # Connect to Microsoft Graph using managed identity
      Connect-MgGraph -Identity -NoWelcome

      # Get the blueprint service principal to obtain token
      $blueprintSp = Get-MgServicePrincipal -Filter "appId eq '$BlueprintAppId'" -ErrorAction SilentlyContinue

      if (-not $blueprintSp) {
        Write-Error "Blueprint service principal not found for App ID: $BlueprintAppId"
        throw "Blueprint not found"
      }

      # Check if agent identity already exists
      $existingSp = Get-MgServicePrincipal -Filter "displayName eq '$AgentDisplayName' and servicePrincipalType eq 'AgentIdentity'" -ErrorAction SilentlyContinue

      if ($existingSp) {
        Write-Host "Agent Identity already exists with ID: $($existingSp.Id)"
        $agentIdentityId = $existingSp.Id
        $agentIdentityAppId = $existingSp.AppId
      } else {
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

        # Create Agent Identity via Graph API
        $headers = @{
          'OData-Version' = '4.0'
          'Content-Type' = 'application/json'
        }

        $agentBody = @{
          displayName = $AgentDisplayName
          agentIdentityBlueprintId = $BlueprintAppId
        }

        if ($sponsors.Count -gt 0) {
          $agentBody['sponsors@odata.bind'] = $sponsors
        }

        try {
          $agentResponse = Invoke-MgGraphRequest -Method POST -Uri 'https://graph.microsoft.com/beta/serviceprincipals/Microsoft.Graph.AgentIdentity' -Body ($agentBody | ConvertTo-Json -Depth 10) -Headers $headers
          $agentIdentityId = $agentResponse.id
          $agentIdentityAppId = $agentResponse.appId
          Write-Host "Created Agent Identity with ID: $agentIdentityId"
        } catch {
          Write-Error "Failed to create Agent Identity: $_"
          throw
        }
      }

      # Get the full service principal details for the agent identity
      $agentSp = Invoke-MgGraphRequest -Method GET -Uri "https://graph.microsoft.com/beta/serviceprincipals/$agentIdentityId" -ErrorAction SilentlyContinue

      # Output results
      $DeploymentScriptOutputs = @{}
      $DeploymentScriptOutputs['agentIdentityId'] = $agentIdentityId
      $DeploymentScriptOutputs['agentIdentityAppId'] = if ($agentIdentityAppId) { $agentIdentityAppId } else { $agentSp.appId }
      $DeploymentScriptOutputs['agentIdentityPrincipalId'] = $agentIdentityId
      $DeploymentScriptOutputs['agentDisplayName'] = $AgentDisplayName

      Write-Host "Agent Identity creation complete"
      Write-Host "Agent Identity ID: $agentIdentityId"
    '''
  }
}

// Outputs
output agentIdentityId string = agentIdentityScript.properties.outputs.agentIdentityId
output agentIdentityAppId string = agentIdentityScript.properties.outputs.agentIdentityAppId
output agentIdentityPrincipalId string = agentIdentityScript.properties.outputs.agentIdentityPrincipalId
output agentDisplayName string = agentIdentityScript.properties.outputs.agentDisplayName
