@description('The name of the API Management service')
param apimServiceName string

@description('The backend URL for the MCP server (AKS service endpoint)')
param mcpServerBackendUrl string

// Get reference to the existing APIM service
resource apimService 'Microsoft.ApiManagement/service@2023-05-01-preview' existing = {
  name: apimServiceName
}

// Create the MCP API definition in APIM
resource mcpApi 'Microsoft.ApiManagement/service/apis@2023-05-01-preview' = {
  parent: apimService
  name: 'mcp'
  properties: {
    displayName: 'MCP API'
    description: 'Model Context Protocol API endpoints - powered by AKS'
    subscriptionRequired: false
    path: 'mcp'
    protocols: [
      'https'
    ]
    serviceUrl: mcpServerBackendUrl
  }
}

// Apply policy at the API level for all operations
resource mcpApiPolicy 'Microsoft.ApiManagement/service/apis/policies@2023-05-01-preview' = {
  parent: mcpApi
  name: 'policy'
  properties: {
    format: 'rawxml'
    value: loadTextContent('mcp-api.policy.simple.xml')
  }
}

// Create the SSE endpoint operation
resource mcpSseOperation 'Microsoft.ApiManagement/service/apis/operations@2023-05-01-preview' = {
  parent: mcpApi
  name: 'mcp-sse'
  properties: {
    displayName: 'MCP SSE Endpoint'
    method: 'GET'
    urlTemplate: '/sse'
    description: 'Server-Sent Events endpoint for MCP Server'
  }
}

// Create the message endpoint operation
resource mcpMessageOperation 'Microsoft.ApiManagement/service/apis/operations@2023-05-01-preview' = {
  parent: mcpApi
  name: 'mcp-message'
  properties: {
    displayName: 'MCP Message Endpoint'
    method: 'POST'
    urlTemplate: '/message'
    description: 'Message endpoint for MCP Server'
  }
}

// Output the API ID for reference
output apiId string = mcpApi.id
