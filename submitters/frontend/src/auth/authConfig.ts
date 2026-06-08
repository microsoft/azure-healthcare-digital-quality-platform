import { Configuration, PopupRequest, RedirectRequest } from "@azure/msal-browser";

// Validate required environment variables
const validateRequiredEnvVars = () => {
  const required = {
    clientId: import.meta.env.VITE_AZURE_CLIENT_ID,
    authority: import.meta.env.VITE_AZURE_AUTHORITY,
    redirectUri: import.meta.env.VITE_AZURE_REDIRECT_URI
  };

  const missing = Object.entries(required)
    .filter(([_, value]) => !value || value.trim() === '')
    .map(([key]) => `VITE_AZURE_${key.toUpperCase()}`);

  if (missing.length > 0) {
    const error = `Missing required Azure AD configuration: ${missing.join(', ')}. Please check your .env file.`;
    console.error(error);
    throw new Error(error);
  }

  // Validate redirect URI format
  const redirectUri = required.redirectUri;
  if (!redirectUri.startsWith('http://') && !redirectUri.startsWith('https://')) {
    throw new Error(`Invalid redirect URI format: ${redirectUri}. Must start with http:// or https://`);
  }

  // Log current configuration for debugging (without sensitive data)
  console.info('[MSAL Config] Redirect URI:', redirectUri);
  console.info('[MSAL Config] Authority:', required.authority);
  console.info('[MSAL Config] Current Origin:', window.location.origin);

  return required;
};

// Get validated environment variables
const envVars = validateRequiredEnvVars();

// Get the correct redirect URI based on environment
const getRedirectUri = (): string => {
  // Always prefer the current origin so the same build works in both
  // localhost dev and cloud (e.g. https://dq-submitters-frontend.*.cloudapp.azure.com).
  // The build-time env var is used only as a last-resort fallback.
  if (typeof window !== "undefined" && window.location && window.location.origin) {
    return window.location.origin;
  }

  const envRedirectUri = import.meta.env.VITE_AZURE_REDIRECT_URI;
  if (envRedirectUri) {
    return envRedirectUri;
  }

  return "http://localhost:5173";
};

// MSAL configuration following Azure best practices
export const msalConfig: Configuration = {
  auth: {
    clientId: envVars.clientId,
    authority: envVars.authority,
    redirectUri: getRedirectUri(),
    postLogoutRedirectUri: window.location.origin,
    navigateToLoginRequestUrl: false, // Recommended for SPAs
    clientCapabilities: ["CP1"] // Enable Conditional Access evaluation
  },
  cache: {
    cacheLocation: "localStorage", // Changed to localStorage for better persistence in healthcare apps
    storeAuthStateInCookie: true, // Enable for IE11/Edge compatibility and security
    secureCookies: window.location.protocol === "https:" // Ensure cookies are secure in production
  },
  system: {
    allowNativeBroker: false, // Disable for web apps
    windowHashTimeout: 60000, // Increase timeout for healthcare networks
    iframeHashTimeout: 6000,
    loadFrameTimeout: 0,
    loggerOptions: {
      loggerCallback: (level, message, containsPii) => {
        if (containsPii) {
          return;
        }
        // Only log errors and warnings in production
        const logLevel = import.meta.env.PROD ? 1 : 3;
        if (level <= logLevel) {
          switch (level) {
            case 0: // LogLevel.Error
              console.error(`[MSAL Error]: ${message}`);
              return;
            case 1: // LogLevel.Warning
              console.warn(`[MSAL Warning]: ${message}`);
              return;
            case 2: // LogLevel.Info
              console.info(`[MSAL Info]: ${message}`);
              return;
            case 3: // LogLevel.Verbose
              console.debug(`[MSAL Debug]: ${message}`);
              return;
          }
        }
      },
      piiLoggingEnabled: false // Disable PII logging for healthcare compliance
    }
  }
};

// Login request with User.Read scope for Microsoft Graph
export const loginRequest: PopupRequest = {
  scopes: ["User.Read"], // Removed Directory.Read.All - requires admin consent
  prompt: "select_account",
  extraQueryParameters: {
    domain_hint: import.meta.env.VITE_AZURE_DOMAIN_HINT || undefined // Optional domain hint for faster login
  }
};

// Redirect login request as fallback
export const loginRedirectRequest: RedirectRequest = {
  scopes: ["User.Read"], // Removed Directory.Read.All - requires admin consent
  prompt: "select_account",
  extraQueryParameters: {
    domain_hint: import.meta.env.VITE_AZURE_DOMAIN_HINT || undefined
  }
};

// Silent token request for API calls
export const silentRequest = {
  scopes: ["User.Read"], // Removed Directory.Read.All - requires admin consent
  forceRefresh: false // Set to true if you want to skip the cache lookup
};

// Microsoft Graph configuration
export const graphConfig = {
  graphMeEndpoint: "https://graph.microsoft.com/v1.0/me",
  graphProfilePhotoEndpoint: "https://graph.microsoft.com/v1.0/me/photo/$value",
  graphMemberOfEndpoint: "https://graph.microsoft.com/v1.0/me/memberOf"
};

// API scopes for your backend (add your custom API scopes here)
export const apiRequest = {
  scopes: [
    import.meta.env.VITE_API_SCOPE || `api://${import.meta.env.VITE_AZURE_CLIENT_ID}/access_as_user`
  ]
};

// Security Groups Configuration
export const securityGroups = {
  // Define your security group IDs here
  clinicalStaff: import.meta.env.VITE_CLINICAL_STAFF_GROUP_ID || "",
  administrators: import.meta.env.VITE_ADMIN_GROUP_ID || "",
  authorizedUsers: [
    import.meta.env.VITE_CLINICAL_STAFF_GROUP_ID || "",
    import.meta.env.VITE_ADMIN_GROUP_ID || ""
  ].filter(id => id.length > 0)
};

// Token validation settings
export const tokenValidation = {
  clockSkew: 300, // 5 minutes clock skew tolerance
  validateIssuer: true,
  validateAudience: true
};

// Admin consent request for elevated permissions (when needed)
export const adminConsentRequest: PopupRequest = {
  scopes: ["Directory.Read.All", "Group.Read.All"],
  prompt: "admin_consent",
  extraQueryParameters: {
    domain_hint: import.meta.env.VITE_AZURE_DOMAIN_HINT || undefined
  }
};

// Generate admin consent URL for manual approval
export const getAdminConsentUrl = (): string => {
  const tenantId = import.meta.env.VITE_AZURE_TENANT_ID || 'common';
  const clientId = import.meta.env.VITE_AZURE_CLIENT_ID;
  const redirectUri = encodeURIComponent(import.meta.env.VITE_AZURE_REDIRECT_URI);
  
  return `https://login.microsoftonline.com/${tenantId}/adminconsent?client_id=${clientId}&redirect_uri=${redirectUri}&scope=https://graph.microsoft.com/Directory.Read.All%20https://graph.microsoft.com/Group.Read.All`;
};

// User-level scopes that don't require admin consent
export const userScopes = ["User.Read", "User.ReadBasic.All"];

// Admin-required scopes for elevated operations
export const adminScopes = ["Directory.Read.All", "Group.Read.All"];

// Debug helper to check current redirect URI configuration
export const getRedirectUriDebugInfo = () => {
  return {
    configuredRedirectUri: msalConfig.auth.redirectUri,
    currentOrigin: window.location.origin,
    currentUrl: window.location.href,
    environmentRedirectUri: import.meta.env.VITE_AZURE_REDIRECT_URI,
    recommendedRedirectUris: [
      `${window.location.origin}`,
      `${window.location.origin}/`,
      `${window.location.origin}/auth/callback`,
      `${window.location.origin}/redirect`
    ]
  };
};
