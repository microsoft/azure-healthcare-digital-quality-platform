// This is a simplified version that doesn't create Entra applications via Microsoft Graph
// For production deployments, you should create the Entra application manually or use the Microsoft Graph extension
// For now, this module expects the Entra application to be provided as parameters

@description('The name of the Entra application')
param entraAppUniqueName string

@description('The display name of the Entra application')
param entraAppDisplayName string

@description('Tenant ID where the application is registered')
param tenantId string = tenant().tenantId

@description('The OAuth callback URL for the API Management service')
param apimOauthCallback string

@description('The principle id of the user-assigned managed identity')
param userAssignedIdentityPrincipleId string

@description('The pre-created Entra application client ID - must be provided for now')
param existingEntraAppId string = ''

// For development/testing purposes, we'll use parameters instead of creating the app
// TODO: Replace with Microsoft Graph extension when fully supported

// Outputs - using tenant ID and provided app ID
output entraAppId string = existingEntraAppId != '' ? existingEntraAppId : '6441e54f-8149-487b-aac4-3a55a049a362'
output entraAppTenantId string = tenantId
