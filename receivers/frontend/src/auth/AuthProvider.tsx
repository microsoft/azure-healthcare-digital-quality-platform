import React, { createContext, useContext, ReactNode } from 'react';
import { 
  useMsal, 
  useAccount, 
  useIsAuthenticated,
  AuthenticatedTemplate,
  UnauthenticatedTemplate 
} from '@azure/msal-react';
import { AccountInfo, InteractionStatus } from '@azure/msal-browser';
import { loginRequest } from './authConfig';

interface AuthContextType {
  isAuthenticated: boolean;
  user: AccountInfo | null;
  login: () => Promise<void>;
  logout: () => Promise<void>;
  isLoading: boolean;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export const useAuth = () => {
  const context = useContext(AuthContext);
  if (context === undefined) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
};

interface AuthProviderProps {
  children: ReactNode;
}

export const AuthProvider: React.FC<AuthProviderProps> = ({ children }) => {
  const { instance, inProgress } = useMsal();
  const isAuthenticated = useIsAuthenticated();
  const account = useAccount();

  // Debug account retrieval
  // console.log('useAccount result:', account);
  // console.log('All accounts:', instance.getAllAccounts());
  // console.log('Active account:', instance.getActiveAccount());
  
  // Try to get account from instance if useAccount returns null
  const currentAccount = account || instance.getActiveAccount() || instance.getAllAccounts()[0] || null;
  
  //console.log('Final account used:', currentAccount);

  const login = async () => {
    try {
      await instance.loginPopup(loginRequest);
    } catch (error) {
      console.error('Login failed:', error);
    }
  };

  const logout = async () => {
    try {
      await instance.logoutPopup();
    } catch (error) {
      console.error('Logout failed:', error);
    }
  };

  const value: AuthContextType = {
    isAuthenticated,
    user: currentAccount,
    login,
    logout,
    isLoading: inProgress === InteractionStatus.Login || inProgress === InteractionStatus.Logout
  };

  //console.log('AuthContext value:', value);

  return (
    <AuthContext.Provider value={value}>
      {children}
    </AuthContext.Provider>
  );
};

export { AuthenticatedTemplate, UnauthenticatedTemplate };
