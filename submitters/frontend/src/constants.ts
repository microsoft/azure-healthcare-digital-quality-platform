import { githubDevSubsPort } from "./utils/ghutils";

function getApiBase(): string {
    const hostname = window.location.hostname;
    const apiPort = 8000;

    // @ts-expect-error
    if (window['endpoint']) return window['endpoint'];

    if (hostname.endsWith('github.dev'))
        return `${githubDevSubsPort(hostname, apiPort)}/`;

    if (hostname === 'localhost' || hostname === '127.0.0.1') {
        const apiBaseUrl = import.meta.env.VITE_API_URL as string | undefined;
        return apiBaseUrl || `http://localhost:${apiPort}`;
    }

    // Production: use relative paths proxied by nginx
    return "";
}

const apiBase = getApiBase();
const endpoint = `${apiBase.endsWith("/") ? apiBase : apiBase + "/"}api/patient`;
export { endpoint };

const image_endpoint = `${apiBase.endsWith("/") ? apiBase : apiBase + "/"}api/upload-image`;
export { image_endpoint };

// uploadLocation.ts
let uploadLocation: string | null = null;

export const setUploadLocation = (location: string) => {
    uploadLocation = location;
};

export const getUploadLocation = () => uploadLocation || "";