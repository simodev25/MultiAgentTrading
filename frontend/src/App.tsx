import { Component, Suspense, lazy } from 'react';
import type { ErrorInfo, ReactNode } from 'react';
import { Navigate, Route, Routes } from 'react-router-dom';
import { Layout } from './components/Layout';
import { RouteLoader } from './components/LoadingIndicators';
import { AuthProvider, useAuth } from './hooks/useAuth';

const TerminalPage = lazy(() => import('./pages/TerminalPage').then((module) => ({ default: module.TerminalPage })));
const BacktestsPage = lazy(() => import('./pages/BacktestsPage').then((module) => ({ default: module.BacktestsPage })));
const RunDetailPage = lazy(() => import('./pages/RunDetailPage').then((module) => ({ default: module.RunDetailPage })));
const OrdersPage = lazy(() => import('./pages/OrdersPage').then((module) => ({ default: module.OrdersPage })));
const ConnectorsPage = lazy(() => import('./pages/ConnectorsPage').then((module) => ({ default: module.ConnectorsPage })));
const StrategiesPage = lazy(() => import('./pages/StrategiesPage').then((module) => ({ default: module.StrategiesPage })));
const PortfolioPage = lazy(() => import('./pages/PortfolioPage'));
const LoginPage = lazy(() => import('./pages/LoginPage').then((module) => ({ default: module.LoginPage })));


class ErrorBoundary extends Component<{ children: ReactNode }, { hasError: boolean; error: Error | null }> {
  state = { hasError: false, error: null as Error | null };

  static getDerivedStateFromError(error: Error) {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('[ErrorBoundary]', error, info.componentStack);
  }

  render() {
    if (this.state.hasError) {
      return (
        <div style={{ padding: 32, color: '#FF4757', fontFamily: 'JetBrains Mono, monospace', fontSize: 12 }}>
          <h2 style={{ color: '#C8CBD0', marginBottom: 12 }}>RUNTIME_ERROR</h2>
          <p style={{ marginBottom: 8 }}>{this.state.error?.message || 'An unexpected error occurred.'}</p>
          <button
            onClick={() => { this.setState({ hasError: false, error: null }); window.location.reload(); }}
            style={{
              background: 'transparent', border: '1px solid #4B7BF5', color: '#4B7BF5',
              padding: '6px 16px', borderRadius: 6, cursor: 'pointer', fontFamily: 'inherit',
            }}
          >
            RELOAD
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}


function withLayout(element: React.ReactNode): React.ReactNode {
  return (
    <Protected>
      <Layout>
        <ErrorBoundary>
          <Suspense fallback={<RouteLoader />}>{element}</Suspense>
        </ErrorBoundary>
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
        element={withLayout(<PortfolioPage />)}
      />
      <Route
        path="/terminal"
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
      <Route
        path="/strategies"
        element={withLayout(<StrategiesPage />)}
      />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

export function App() {
  return (
    <AuthProvider>
      <ErrorBoundary>
        <Suspense fallback={<RouteLoader />}>
          <AppRoutes />
        </Suspense>
      </ErrorBoundary>
    </AuthProvider>
  );
}
