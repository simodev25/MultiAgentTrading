import { Suspense, lazy } from 'react';
import { Navigate, Route, Routes } from 'react-router-dom';
import { Layout } from './components/Layout';
import { RouteLoader } from './components/LoadingIndicators';
import { AuthProvider, useAuth } from './hooks/useAuth';

const TerminalPage = lazy(() => import('./pages/TerminalPage').then((module) => ({ default: module.TerminalPage })));
const BacktestsPage = lazy(() => import('./pages/BacktestsPage').then((module) => ({ default: module.BacktestsPage })));
const RunDetailPage = lazy(() => import('./pages/RunDetailPage').then((module) => ({ default: module.RunDetailPage })));
const OrdersPage = lazy(() => import('./pages/OrdersPage').then((module) => ({ default: module.OrdersPage })));
const ConnectorsPage = lazy(() => import('./pages/ConnectorsPage').then((module) => ({ default: module.ConnectorsPage })));
const LoginPage = lazy(() => import('./pages/LoginPage').then((module) => ({ default: module.LoginPage })));

function withLayout(element: React.ReactNode): React.ReactNode {
  return (
    <Protected>
      <Layout>
        <Suspense fallback={<RouteLoader />}>{element}</Suspense>
      </Layout>
    </Protected>
  );
}

function Protected({ children }: { children: React.ReactNode }) {
  const { token, loading } = useAuth();
  if (loading) return <RouteLoader />;
  if (!token) return <Navigate to="/login" replace />;
  return <>{children}</>;
}

function AppRoutes() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route
        path="/"
        element={withLayout(<TerminalPage />)}
      />
      <Route
        path="/backtests"
        element={withLayout(<BacktestsPage />)}
      />
      <Route
        path="/runs/:runId"
        element={withLayout(<RunDetailPage />)}
      />
      <Route
        path="/orders"
        element={withLayout(<OrdersPage />)}
      />
      <Route
        path="/connectors"
        element={withLayout(<ConnectorsPage />)}
      />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

export function App() {
  return (
    <AuthProvider>
      <Suspense fallback={<RouteLoader />}>
        <AppRoutes />
      </Suspense>
    </AuthProvider>
  );
}
