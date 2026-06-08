import { configureStore } from "@reduxjs/toolkit";
import patientReducer from "./patientSlice";
import { githubDevSubsPort } from "../utils/ghutils";
export interface IMessage {
  type: "message" | "researcher" | "marketing" | "writer" | "editor" | "error" | "partial";
  message: string;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  data?: any;
}

export interface IPatientCollection {
  current: number;
  patients: string[];
  currentPatient: string; 
}

// Make sure reducer name matches the component selector
const store = configureStore({
  reducer: {
    patient: patientReducer,
  },
});

export default store;  // Export the store

export interface IChatTurn {
  name: string;
  avatar: string;
  image: string | null;
  message: string;
  status: "waiting" | "done";
  type: "user" | "assistant";
}

export const startWritingTask = async (
  research: string,
  products: string,
  assignment: string,
  addMessage: { (message: IMessage): void },
  createArticle: { (patient: string): void },
  addToArticle: { (text: string): void }
) => {
  // internal function to read chunks from a stream
  function readChunks(reader: ReadableStreamDefaultReader<Uint8Array>) {
    return {
      async *[Symbol.asyncIterator]() {
        let readResult = await reader.read();
        while (!readResult.done) {
          yield readResult.value;
          readResult = await reader.read();
        }
      },
    };
  }

  // Get authentication token
  const token = await getAuthToken();
  
  const headers: HeadersInit = {
    "Content-Type": "application/json",
    "Connection": "keep-alive",
  };
  
  // Add Authorization header if token is available
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }

  const configuration = {
    method: "POST",
    headers,
    body: JSON.stringify({
      research: research,
      products: products,
      assignment: assignment,
    }),
  };

  const hostname = window.location.hostname;
  const apiPort = 8000;
  let endpoint = "";
  if (hostname === 'localhost' || hostname === '127.0.0.1') {
    const apiBaseUrl = import.meta.env.VITE_API_URL as string | undefined;
    endpoint = apiBaseUrl || `http://localhost:${apiPort}`;
  } else if (hostname.endsWith('github.dev')) {
    endpoint = `${githubDevSubsPort(hostname, apiPort)}/`;
  }
  // else: production — use relative paths proxied by nginx

  const url = `${
    endpoint.endsWith("/") ? endpoint : endpoint + "/"
  }api/patient`;

  const callApi = async () => {
    try {
      const response = await fetch(url, configuration);
      
      // Handle authentication errors
      if (response.status === 401) {
        console.warn('Authentication failed - redirecting to login');
        // Trigger re-authentication
        const msalInstance = (window as any).msalInstance;
        if (msalInstance) {
          try {
            await msalInstance.loginPopup();
            // Retry the request with new token
            const newToken = await getAuthToken();
            if (newToken) {
              configuration.headers = {
                ...configuration.headers,
                'Authorization': `Bearer ${newToken}`
              };
              return callApi(); // Retry with new token
            }
          } catch (error) {
            console.error('Re-authentication failed:', error);
            addMessage({
              type: "error",
              message: "Authentication failed. Please refresh the page and try again."
            });
            return;
          }
        }
      }
      
      const reader = response.body?.getReader();
      if (!reader) return;

      const chunks = readChunks(reader);
      for await (const chunk of chunks) {
        const text = new TextDecoder().decode(chunk);
        const parts = text.split("\n");
        for (let part of parts) {
          part = part.trim();
          if (!part || part.length === 0) continue;
          const message = JSON.parse(part) as IMessage;
          addMessage(message);
          if (message.type === "writer") {
            if (message.data && message.data.start) {
              createArticle("");
            }
          } else if (message.type === "partial") {
            if (message.data?.text && message.data.text.length > 0) {
              addToArticle(message.data?.text || "");
            }
          }
        }
      }
    } catch (e) {
      console.error('API call failed:', e);
      addMessage({
        type: "error",
        message: "API call failed. Please check your connection and try again."
      });
    }
  };

  callApi();

};

// Utility function to get auth token
export async function getAuthToken(): Promise<string | null> {
  try {
    // Get MSAL instance from window object (set by main.tsx)
    const msalInstance = (window as any).msalInstance;
    if (!msalInstance) {
      console.warn('MSAL instance not found on window object');
      return null;
    }
    
    const accounts = msalInstance.getAllAccounts();
    if (accounts.length === 0) {
      console.warn('No authenticated accounts found');
      return null;
    }
    
    const request = {
      scopes: ["User.Read"],
      account: accounts[0]
    };
    
    const response = await msalInstance.acquireTokenSilent(request);
    return response.accessToken;
  } catch (error) {
    console.error('Failed to get auth token:', error);
    
    // Try to get a new token via popup if silent acquisition fails
    try {
      const msalInstance = (window as any).msalInstance;
      if (msalInstance) {
        const response = await msalInstance.acquireTokenPopup({
          scopes: ["User.Read"]
        });
        return response.accessToken;
      }
    } catch (popupError) {
      console.error('Failed to get token via popup:', popupError);
    }
    
    return null;
  }
}

// Utility function to check if user is member of authorized security groups
export async function checkUserGroupMembership(): Promise<boolean> {
  try {
    const token = await getAuthToken();
    if (!token) return false;

    const response = await fetch('https://graph.microsoft.com/v1.0/me/memberOf', {
      headers: {
        'Authorization': `Bearer ${token}`,
        'Content-Type': 'application/json'
      }
    });

    if (!response.ok) {
      console.warn('Failed to fetch user group membership');
      return false;
    }

    const data = await response.json();
    const userGroups = data.value || [];
    
    // Get authorized group IDs from environment variables
    const authorizedGroups = [
      import.meta.env.VITE_CLINICAL_STAFF_GROUP_ID,
      import.meta.env.VITE_ADMIN_GROUP_ID
    ].filter(id => id && id.length > 0);

    // Check if user is member of any authorized group
    const isAuthorized = userGroups.some((group: any) => 
      authorizedGroups.includes(group.id)
    );

    return isAuthorized;
  } catch (error) {
    console.error('Error checking user group membership:', error);
    return false;
  }
}

// Enhanced utility function to get user profile with group validation
export async function getUserProfileWithValidation(): Promise<{ isAuthorized: boolean; profile?: any }> {
  try {
    const token = await getAuthToken();
    if (!token) return { isAuthorized: false };

    // Get user profile
    const profileResponse = await fetch('https://graph.microsoft.com/v1.0/me', {
      headers: {
        'Authorization': `Bearer ${token}`,
        'Content-Type': 'application/json'
      }
    });

    if (!profileResponse.ok) {
      return { isAuthorized: false };
    }

    const profile = await profileResponse.json();

    // Check group membership
    const isAuthorized = await checkUserGroupMembership();

    return { isAuthorized, profile };
  } catch (error) {
    console.error('Error getting user profile with validation:', error);
    return { isAuthorized: false };
  }
}

// Utility function to fetch patient by ID with authentication
export async function fetchPatient(patient_id: string) {
  const hostname = window.location.hostname;
  const apiPort = 8000;
  let endpoint = "";
  if (hostname === 'localhost' || hostname === '127.0.0.1') {
    const apiBaseUrl = import.meta.env.VITE_API_URL as string | undefined;
    endpoint = apiBaseUrl || `http://localhost:${apiPort}`;
  } else if (hostname.endsWith('github.dev')) {
    endpoint = `${githubDevSubsPort(hostname, apiPort)}/`;
  }
  // else: production — use relative paths proxied by nginx

  const url = `${endpoint.endsWith("/") ? endpoint : endpoint + "/"}api/patient/${patient_id}`;
  
  // Get authentication token
  const token = await getAuthToken();
  
  const headers: HeadersInit = {
    'Content-Type': 'application/json',
  };
  
  // Add Authorization header if token is available
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }

  const response = await fetch(url, {
    method: 'GET',
    headers
  });
  
  // Handle authentication errors
  if (response.status === 401) {
    throw new Error('Authentication failed. Please log in again.');
  }
  
  if (!response.ok) {
    throw new Error(`Failed to fetch member: ${response.status} ${response.statusText}`);
  }
  
  return response.json();
}

export async function runPatientMeasurement(
  patient_id: string,
  useNativeCqlEngine: boolean,
  useAiCqlEngine: boolean,
) {
  const hostname = window.location.hostname;
  const apiPort = 8000;
  let endpoint = "";
  if (hostname === "localhost" || hostname === "127.0.0.1") {
    const apiBaseUrl = import.meta.env.VITE_API_URL as string | undefined;
    endpoint = apiBaseUrl || `http://localhost:${apiPort}`;
  } else if (hostname.endsWith("github.dev")) {
    endpoint = `${githubDevSubsPort(hostname, apiPort)}/`;
  }
  // else: production — use relative paths proxied by nginx

  const url = `${endpoint.endsWith("/") ? endpoint : endpoint + "/"}api/patient/${patient_id}/measure`;
  const token = await getAuthToken();

  const headers: HeadersInit = {
    "Content-Type": "application/json",
  };

  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }

  const response = await fetch(url, {
    method: "POST",
    headers,
    body: JSON.stringify({
      use_native_cql_engine: useNativeCqlEngine,
      use_ai_cql_engine: useAiCqlEngine,
    }),
  });

  if (response.status === 401) {
    throw new Error("Authentication failed. Please log in again.");
  }

  if (!response.ok) {
    throw new Error(`Failed to run measurement: ${response.status} ${response.statusText}`);
  }

  return response.json();
}

// ---------------------------------------------------------------------------
// Consumers stack: patient-facing helpers
// ---------------------------------------------------------------------------

function _apiBase(): string {
  const hostname = window.location.hostname;
  const apiPort = 8000;
  if (hostname === "localhost" || hostname === "127.0.0.1") {
    const apiBaseUrl = import.meta.env.VITE_API_URL as string | undefined;
    return apiBaseUrl || `http://localhost:${apiPort}`;
  }
  if (hostname.endsWith("github.dev")) {
    return githubDevSubsPort(hostname, apiPort);
  }
  return "";
}

async function _authedFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const base = _apiBase();
  const url = `${base.endsWith("/") || base === "" ? base : base + "/"}${path.replace(/^\//, "")}`;
  const token = await getAuthToken();
  const headers: HeadersInit = {
    "Content-Type": "application/json",
    ...(init.headers || {}),
  };
  if (token) {
    (headers as Record<string, string>)["Authorization"] = `Bearer ${token}`;
  }
  const response = await fetch(url, { ...init, headers });
  if (response.status === 401) {
    throw new Error("Authentication failed. Please log in again.");
  }
  if (!response.ok) {
    throw new Error(`${init.method || "GET"} ${path} failed: ${response.status} ${response.statusText}`);
  }
  return response;
}

export interface SamplePatientSummary {
  id: string;
  patient: {
    id?: string;
    mrn?: string;
    name?: string;
    gender?: string;
    birthDate?: string;
  } | null;
  counts: { encounters: number; conditions: number; observations: number };
  primaryMeasures: string[];
}

export async function fetchSamplePatients(): Promise<{ seedDir: string; count: number; samples: SamplePatientSummary[] }> {
  const response = await _authedFetch("api/sample-patients");
  return response.json();
}

export async function fetchSamplePatient(bundleId: string): Promise<{ id: string; bundle: unknown; summary: SamplePatientSummary }> {
  const response = await _authedFetch(`api/sample-patients/${bundleId}`);
  return response.json();
}

export async function runLocalMeasures(
  bundleId: string,
  periodStart = "2025-01-01",
  periodEnd = "2025-12-31",
) {
  const qs = new URLSearchParams({ period_start: periodStart, period_end: periodEnd });
  const response = await _authedFetch(`api/sample-patients/${bundleId}/measures/run-local?${qs.toString()}`, {
    method: "POST",
  });
  return response.json();
}

export interface SoapEntryInput {
  role: string;
  subjective?: string;
  objective?: string;
  assessment?: string;
  plan?: string;
  encounterId?: string;
  author?: string;
}

export async function fetchSoapNotes(patientId: string): Promise<{
  patientId: string;
  rounds: Record<string, Array<SoapEntryInput & { id?: string; createdAt?: string; updatedAt?: string }>>;
  count: number;
}> {
  const response = await _authedFetch(`api/patients/${patientId}/soap-notes`);
  return response.json();
}

export async function createSoapNote(
  patientId: string,
  round: number,
  entry: SoapEntryInput,
) {
  const response = await _authedFetch(`api/patients/${patientId}/soap-notes`, {
    method: "POST",
    body: JSON.stringify({ round, entry }),
  });
  return response.json();
}

export async function deleteSoapNote(patientId: string, noteId: string) {
  const response = await _authedFetch(`api/patients/${patientId}/soap-notes/${noteId}`, {
    method: "DELETE",
  });
  return response.json();
}
