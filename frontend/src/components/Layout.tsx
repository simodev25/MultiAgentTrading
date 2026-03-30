import { useState } from 'react';
import { NavLink } from 'react-router-dom';
import { useAuth } from '../hooks/useAuth';
import {
  Receipt,
  FlaskConical,
  Settings,
  LogOut,
  Cpu,
  Terminal,
  PanelLeftClose,
  PanelLeftOpen,
} from 'lucide-react';

const navItems = [
  { to: '/', label: 'TERMINAL', icon: Terminal, node: '01' },
  { to: '/orders', label: 'ORDRES', icon: Receipt, node: '02' },
  { to: '/backtests', label: 'BACKTESTS', icon: FlaskConical, node: '03' },
  { to: '/connectors', label: 'CONFIG', icon: Settings, node: '04' },
];

export function Layout({ children }: { children: React.ReactNode }) {
  const { user, logout } = useAuth();
  const [collapsed, setCollapsed] = useState(true);

  return (
    <div className="flex h-screen overflow-hidden bg-bg">
      {/* ── Sidebar ─────────────────────────────────────── */}
      <aside
        className={`flex flex-col bg-surface shrink-0 border-r border-border transition-all duration-200 ${
          collapsed ? 'w-[68px]' : 'w-[280px]'
        }`}
      >
        {/* Brand header */}
        <div className="px-3 py-4 border-b border-border">
          <div className="flex items-center gap-3 justify-between">
            <div className="flex items-center gap-3 min-w-0">
              <div className="w-8 h-8 rounded-lg bg-accent/15 border border-accent/25 flex items-center justify-center shrink-0">
                <Cpu className="w-4 h-4 text-accent" />
              </div>
              {!collapsed && (
                <div className="min-w-0">
                  <span className="text-[11px] font-bold tracking-[0.14em] text-accent uppercase">
                    AGENT_TERMINAL
                  </span>
                  <span className="text-[9px] text-text-dim tracking-widest ml-1.5">v4.2</span>
                </div>
              )}
            </div>
            <button
              onClick={() => setCollapsed((prev) => !prev)}
              className="w-7 h-7 rounded-lg flex items-center justify-center text-text-dim hover:text-text-muted hover:bg-surface-alt transition-colors shrink-0"
              aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
            >
              {collapsed ? (
                <PanelLeftOpen className="w-3.5 h-3.5" />
              ) : (
                <PanelLeftClose className="w-3.5 h-3.5" />
              )}
            </button>
          </div>
        </div>

        {/* Navigation stack */}
        <div className="flex-1 p-3 flex flex-col">
          {!collapsed && (
            <div className="section-header">
              <span className="section-title">NAV_STACK</span>
              <Terminal className="section-icon" />
            </div>
          )}

          <nav className="flex flex-col gap-1.5">
            {navItems.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.to === '/'}
                title={collapsed ? item.label : undefined}
                className={({ isActive }) =>
                  `flex items-center gap-3 rounded-xl transition-all duration-150 group ${
                    collapsed ? 'px-0 py-2.5 justify-center' : 'px-3 py-2.5'
                  } ${
                    isActive
                      ? 'bg-surface-alt border border-accent/20'
                      : 'border border-transparent hover:bg-surface-alt/50'
                  }`
                }
              >
                {({ isActive }) => (
                  <>
                    <div className={`w-8 h-8 rounded-lg flex items-center justify-center shrink-0 ${
                      isActive
                        ? 'bg-accent/15 border border-accent/30'
                        : 'bg-surface-raised border border-border'
                    }`}>
                      <item.icon className={`w-3.5 h-3.5 ${isActive ? 'text-accent' : 'text-text-dim'}`} />
                    </div>
                    {!collapsed && (
                      <>
                        <div className="flex flex-col min-w-0">
                          <span className="text-[8px] text-text-dim tracking-[0.15em] leading-none">
                            NODE_{item.node}
                          </span>
                          <span className={`text-[11px] font-semibold tracking-[0.1em] ${
                            isActive ? 'text-accent' : 'text-text-muted group-hover:text-text'
                          }`}>
                            {item.label}
                          </span>
                        </div>
                        {isActive && (
                          <div className="ml-auto led led-blue" />
                        )}
                      </>
                    )}
                  </>
                )}
              </NavLink>
            ))}
          </nav>
        </div>

        {/* Session footer */}
        <div className="p-3 border-t border-border">
          {collapsed ? (
            <div className="flex flex-col items-center gap-2">
              <div className="led led-green" />
              <button
                onClick={logout}
                className="w-8 h-8 rounded-lg flex items-center justify-center text-text-dim hover:text-danger hover:bg-danger/10 transition-colors"
                title="DISCONNECT"
              >
                <LogOut className="w-3.5 h-3.5" />
              </button>
            </div>
          ) : (
            <>
              <div className="flex items-center gap-2 mb-3">
                <div className="led led-green" />
                <span className="text-[9px] text-text-muted tracking-[0.14em] uppercase">
                  SESSION_ACTIVE
                </span>
              </div>
              <div className="text-[9px] text-text-dim tracking-[0.1em] mb-3">
                {user?.email}
              </div>
              <button
                onClick={logout}
                className="btn-ghost w-full flex items-center justify-center gap-2"
              >
                <LogOut className="w-3 h-3" />
                DISCONNECT
              </button>
            </>
          )}
        </div>
      </aside>

      {/* ── Main ────────────────────────────────────────── */}
      <div className="flex-1 flex flex-col overflow-hidden">
        {/* Top bar */}
        <header className="h-11 shrink-0 border-b border-border bg-surface flex items-center justify-between px-5">
          <div className="flex items-center gap-4">
            <span className="text-[11px] font-bold tracking-[0.14em] text-accent uppercase">
              AGENT_TERMINAL_v4.2
            </span>
            <div className="h-4 w-px bg-border" />
            <div className="flex items-center gap-2">
              <div className="led led-green" />
              <span className="text-[9px] text-text-muted tracking-[0.1em] uppercase">
                SYSTEM_OK
              </span>
            </div>
            <div className="h-4 w-px bg-border" />
            <span className="text-[9px] text-text-dim tracking-[0.1em] uppercase">
              CPU_LOAD: --
            </span>
          </div>
          <div className="flex items-center gap-3">
            <span className="badge role">{user?.role}</span>
            <Settings className="w-3.5 h-3.5 text-text-dim cursor-pointer hover:text-text-muted transition-colors" />
          </div>
        </header>

        {/* Content */}
        <main className="flex-1 overflow-y-auto p-4">
          {children}
        </main>

        {/* Bottom bar */}
        <footer className="h-7 shrink-0 border-t border-border bg-surface flex items-center justify-between px-5">
          <div className="flex items-center gap-4">
            <span className="text-[8px] text-text-dim tracking-[0.14em] uppercase">
              LOGIC_STREAM
            </span>
            <span className="text-[8px] text-text-dim tracking-[0.1em]">
              BUFFER_SIZE: 0
            </span>
          </div>
          <span className="text-[8px] text-text-dim tracking-[0.14em] uppercase">
            TAURIC_MARKETS // MULTI_AGENT_PLATFORM
          </span>
        </footer>
      </div>
    </div>
  );
}
