import React, { useState, useEffect } from 'react';
import { useAuth } from '../auth/AuthProvider';
import orgLogo from '../assets/org.png';
import appLogo from '../assets/app.png';

const LoginComponent: React.FC = () => {
  const { login, isLoading } = useAuth();
  const [configError, setConfigError] = useState<string>('');

  useEffect(() => {
    // Check if required environment variables are set
    const clientId = import.meta.env.VITE_AZURE_CLIENT_ID;
    const authority = import.meta.env.VITE_AZURE_AUTHORITY;
    const redirectUri = import.meta.env.VITE_AZURE_REDIRECT_URI;

    if (!clientId || !authority || !redirectUri) {
      setConfigError('Azure AD configuration is incomplete. Please check environment variables.');
    }
  }, []);

  const handleLogin = async () => {
    try {
      await login();
    } catch (error) {
      console.error('Login error:', error);
      setConfigError('Login failed. Please try again or contact support.');
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-50 py-12 px-4 sm:px-6 lg:px-8">
      <div className="max-w-md w-full space-y-8">
        <div>
          <header className="my-6">
            <div className="logo-container flex items-center justify-center mb-4">
              <img 
                src={orgLogo} 
                alt="orgLogo"
                className="org-logo"
              />
              <div className="logo-divider"></div>
              <img 
                src={appLogo} 
                alt="appLogo"
              />
            </div>
          </header>
        </div>
        
        {configError && (
          <div className="bg-red-50 border border-red-200 rounded-md p-4">
            <div className="flex">
              <div className="flex-shrink-0">
                <svg className="h-5 w-5 text-red-400" viewBox="0 0 20 20" fill="currentColor">
                  <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.707 7.293a1 1 0 00-1.414 1.414L8.586 10l-1.293 1.293a1 1 0 101.414 1.414L10 11.414l1.293 1.293a1 1 0 001.414-1.414L11.414 10l1.293-1.293a1 1 0 00-1.414-1.414L10 8.586 8.707 7.293z" clipRule="evenodd" />
                </svg>
              </div>
              <div className="ml-3">
                <h3 className="text-base font-medium text-red-800">Configuration Error</h3>
                <div className="mt-2 text-base text-red-700">
                  <p>{configError}</p>
                </div>
              </div>
            </div>
          </div>
        )}

        <div className="mt-8 space-y-6">
          <div className="flex justify-center">
            <button
              onClick={handleLogin}
              disabled={isLoading || !!configError}
              className="group relative flex justify-center py-3 px-8 border border-transparent text-base font-medium rounded-md text-white hover:opacity-90 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-500 disabled:opacity-50 disabled:cursor-not-allowed transition-opacity duration-200"
              style={{ backgroundColor: '#00AEEF', minWidth: '250px' }}
            >
              {isLoading ? (
                <>
                  <svg className="animate-spin -ml-1 mr-3 h-5 w-5 text-white" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                  </svg>
                  Logging in...
                </>
              ) : (
                'Log In'
              )}
            </button>
          </div>
          
          {!configError && (
            <div className="text-center text-base text-gray-600">
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export default LoginComponent;
