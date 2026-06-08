import './app.css';
import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { version } from "./version";
import CatalogPage from "./pages/CatalogPage";
import CohortsPage from "./pages/CohortsPage";
import ErrorBoundary from "./components/ErrorBoundary";
import UserProfile from "./components/UserProfile";
import LoginComponent from "./components/LoginComponent";
import { useScrollDirection } from "./hooks/useScrollDirection";
import {
  AuthenticatedTemplate,
  UnauthenticatedTemplate,
} from "./auth/AuthProvider";
import orgLogo from './assets/org.png';

function tabIsActive(pathname: string, branch: "catalog" | "cohorts"): boolean {
  const onCohorts = pathname === "/cohorts" || pathname.startsWith("/cohorts/");
  return branch === "cohorts" ? onCohorts : !onCohorts;
}

function App() {
  const { isAtBottom } = useScrollDirection();
  const isVersionVisible = isAtBottom;

  const navTab = (
    to: string,
    branch: "catalog" | "cohorts",
    label: string,
    sub: string,
  ) => (
    <NavLink
      key={branch}
      to={to}
      className={({ isActive }) => {
        const active = isActive || tabIsActive(window.location.pathname, branch);
        return `px-4 py-2 text-sm font-medium rounded-t border border-b-0 transition ${
          active
            ? "bg-white text-blue-700 border-gray-300"
            : "bg-gray-100 text-gray-600 border-transparent hover:bg-gray-200"
        }`;
      }}
      aria-current={tabIsActive(window.location.pathname, branch) ? "page" : undefined}
    >
      <span>{label}</span>
      <span className="block text-[10px] font-normal text-gray-500 leading-tight">
        {sub}
      </span>
    </NavLink>
  );

  return (
    <main className="p-8 flex flex-col min-h-screen">
      <UnauthenticatedTemplate>
        <LoginComponent />
      </UnauthenticatedTemplate>

      <AuthenticatedTemplate>
        <header className="my-6">
          <div className="absolute top-8 left-4 md:left-12 z-50 logo-container flex items-center">
            <img
              src={orgLogo}
              alt="orgLogo"
              className="org-logo"
            />
          </div>
          <div className="absolute top-4 right-4 md:right-20 z-50">
            <UserProfile />
          </div>
        </header>

        <div className="flex flex-col lg:flex-row lg:space-x-8 mt-8">
          <section className="w-full max-w-none lg:max-w-7xl xl:max-w-none mx-auto flex-grow mt-8 lg:mt-0">
            <nav
              className="flex gap-1 px-2 lg:mx-4 border-b border-gray-300"
              aria-label="Primary"
            >
              {navTab("/catalog", "catalog", "Catalog", "Measures · Tags · Agencies")}
              {navTab("/cohorts", "cohorts", "Cohorts", "Members · Evaluate · Submit")}
            </nav>
            <div className="bg-white shadow-md rounded-b rounded-tr lg:mx-4">
              <div className="p-4 lg:p-8">
                <ErrorBoundary>
                  <Routes>
                    <Route path="/" element={<Navigate to="/catalog" replace />} />
                    <Route path="/catalog" element={<CatalogPage />} />
                    <Route path="/measures/:focusId" element={<CatalogPage initialSection="measures" />} />
                    <Route path="/tags/:focusId" element={<CatalogPage initialSection="tags" />} />
                    <Route path="/agencies/:focusId" element={<CatalogPage initialSection="agencies" />} />
                    <Route path="/programs/:focusId" element={<CatalogPage initialSection="agencies" focusKind="program" />} />
                    <Route path="/cohorts" element={<CohortsPage />} />
                    <Route path="/cohorts/:cohortId" element={<CohortsPage />} />
                    <Route path="*" element={<Navigate to="/catalog" replace />} />
                  </Routes>
                </ErrorBoundary>
              </div>
            </div>
          </section>
        </div>

        <footer>
          <div 
            className={`fixed right-12 bottom-2 text-gray-400 text-xs font-mono z-40 transition-all duration-200 ease-in-out pointer-events-none ${
              isVersionVisible ? 'opacity-70' : 'opacity-0'
            }`}
          >
            v{version}
          </div>
        </footer>
      </AuthenticatedTemplate>
    </main>
  );
}

export default App;