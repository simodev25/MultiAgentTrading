import { Link, NavLink } from 'react-router-dom';
import { useAuth } from '../hooks/useAuth';

const navItems = [
  { to: '/', label: 'Dashboard', icon: 'D' },
  { to: '/orders', label: 'Ordres', icon: 'O' },
  { to: '/backtests', label: 'Backtests', icon: 'B' },
  { to: '/connectors', label: 'Paramètres', icon: 'P' },
];

export function Layout({ children }: { children: React.ReactNode }) {
  const { user, logout } = useAuth();

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark" aria-hidden>
            MA
          </div>
          <div>
            <h1>Tauric Markets</h1>
            <p>Plateforme Multi-Agent</p>
          </div>
        </div>
        <nav className="nav">
          {navItems.map((item) => (
            <NavLink key={item.to} to={item.to} end={item.to === '/'} className={({ isActive }) => (isActive ? 'nav-link active' : 'nav-link')}>
              <span className="nav-icon" aria-hidden>
                {item.icon}
              </span>
              <span>{item.label}</span>
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-footer">
          <p>Session active</p>
          <button className="btn-ghost" onClick={logout}>Logout</button>
        </div>
      </aside>
      <main className="content">
        <header className="topbar">
          <div className="topbar-heading">
            <Link to="/" className="topbar-title">
              Plateforme Trading Multi-Actifs
            </Link>
            <p className="topbar-subtitle">Control Room Multi-Marchés</p>
          </div>
          <div className="topbar-actions">
            <span className="badge role">{user?.role}</span>
            <span className="topbar-user">{user?.email}</span>
          </div>
        </header>
        {children}
      </main>
    </div>
  );
}
