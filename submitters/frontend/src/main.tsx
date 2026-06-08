import React from 'react'
import ReactDOM from 'react-dom/client'
import { Provider } from 'react-redux'
import { BrowserRouter } from 'react-router-dom'
import { PublicClientApplication } from '@azure/msal-browser'
import { MsalProvider } from '@azure/msal-react'
import App from './App.tsx'
import { AuthProvider } from './auth/AuthProvider.tsx'
import store from './store'
import { msalConfig } from './auth/authConfig.ts'
import './app.css'

// Create MSAL instance
const msalInstance = new PublicClientApplication(msalConfig);

// Make MSAL instance available globally for API calls
(window as any).msalInstance = msalInstance;

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <MsalProvider instance={msalInstance}>
      <AuthProvider>
        <Provider store={store}>
          <BrowserRouter>
            <App />
          </BrowserRouter>
        </Provider>
      </AuthProvider>
    </MsalProvider>
  </React.StrictMode>,
)
