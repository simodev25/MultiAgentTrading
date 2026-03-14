import { Link, NavLink } from 'react-router-dom';
import { useAuth } from '../hooks/useAuth';

const navItems = [
  { to: '/', label: 'Dashboard' },
  { to: '/backtests', label: 'Backtests' },
  { to: '/orders', label: 'Ordres' },
  { to: '/connectors', label: 'Config' },
];

export function Layout({ children }: { children: React.ReactNode }) {
  const { user, logout } = useAuth();

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <h1>Forex Desk</h1>
          <p>Multi-Agent Platform</p>
        </div>
        <nav className="nav">
          {navItems.map((item) => (
            <NavLink key={item.to} to={item.to} end={item.to === '/'} className={({ isActive }) => (isActive ? 'nav-link active' : 'nav-link')}>
              {item.label}
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-footer">
          <p>{user?.email}</p>
          <button onClick={logout}>Logout</button>
        </div>
      </aside>
      <main className="content">
        <header className="topbar">
          <Link to="/" className="topbar-title">
            Trading Control Room
          </Link>
          <span className="badge role">{user?.role}</span>
        </header>
        {children}
      </main>
    </div>
  );
}
