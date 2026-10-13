import clsx from 'clsx';
import { useEffect, useState } from 'react';
import { NavLink, Outlet, useLocation } from 'react-router-dom';

import { API_BASE_URL, errorMessage } from '../../api/client';
import { useHealth } from '../../api/hooks';
import { fmtDateTime } from '../../lib/format';

interface NavItem {
  to: string;
  label: string;
  abbr: string;
  end?: boolean;
}

const NAV: NavItem[] = [
  { to: '/', label: 'Executive overview', abbr: 'EX', end: true },
  { to: '/map', label: 'Hazard map', abbr: 'MP' },
  { to: '/properties', label: 'Property explorer', abbr: 'PR' },
  { to: '/models', label: 'Model analytics', abbr: 'ML' },
  { to: '/portfolio', label: 'Portfolio analysis', abbr: 'PF' },
  { to: '/data-quality', label: 'Data quality', abbr: 'DQ' },
];

function HealthIndicator() {
  const q = useHealth();
  let dot = 'bg-slate-500';
  let text = 'Checking API…';
  let title = '';
  if (q.isError) {
    dot = 'bg-red-500';
    text = 'API unreachable';
    title = errorMessage(q.error);
  } else if (q.data) {
    const h = q.data;
    if (h.status === 'ok' && h.database === 'ok') {
      dot = 'bg-emerald-400';
      text = 'API ok';
    } else {
      dot = 'bg-amber-400';
      text = h.database !== 'ok' ? 'Database unavailable' : 'API degraded';
    }
    title = `v${h.version} · database ${h.database} · ${fmtDateTime(h.timestamp)}${h.detail ? ` · ${h.detail}` : ''}`;
  }
  return (
    <div title={title} className="flex items-center gap-2 text-xs text-slate-300">
      <span className={clsx('h-2.5 w-2.5 rounded-full', dot, q.isFetching && 'animate-pulse')} />
      <span>{text}</span>
      {q.data && <span className="hidden text-slate-500 sm:inline">v{q.data.version}</span>}
    </div>
  );
}

function EnvBadge() {
  const mode = import.meta.env.MODE;
  const api = API_BASE_URL || 'same-origin /api';
  return (
    <div className="flex items-center gap-2 text-xs">
      <span
        className={clsx(
          'rounded border px-1.5 py-0.5 font-mono uppercase',
          mode === 'production'
            ? 'border-teal-500/40 bg-teal-500/10 text-teal-300'
            : 'border-amber-500/40 bg-amber-500/10 text-amber-300',
        )}
      >
        {mode}
      </span>
      <span className="hidden font-mono text-slate-500 md:inline" title="API base URL">
        {api}
      </span>
    </div>
  );
}

function NavLinks({ onNavigate }: { onNavigate?: () => void }) {
  return (
    <nav className="flex flex-col gap-1">
      {NAV.map((item) => (
        <NavLink
          key={item.to}
          to={item.to}
          end={item.end}
          onClick={onNavigate}
          className={({ isActive }) =>
            clsx(
              'flex items-center gap-3 rounded-md px-3 py-2 text-sm transition-colors',
              isActive
                ? 'bg-teal-500/10 text-teal-300'
                : 'text-slate-300 hover:bg-slate-800 hover:text-slate-100',
            )
          }
        >
          <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded border border-slate-700 bg-slate-950 font-mono text-[10px] text-slate-400">
            {item.abbr}
          </span>
          <span>{item.label}</span>
        </NavLink>
      ))}
    </nav>
  );
}

export function AppShell() {
  const [drawerOpen, setDrawerOpen] = useState(false);
  const location = useLocation();

  useEffect(() => {
    setDrawerOpen(false);
  }, [location.pathname]);

  return (
    <div className="flex h-screen w-full overflow-hidden bg-slate-950 text-slate-100">
      {/* Sidebar (md and up) */}
      <aside className="hidden w-60 shrink-0 flex-col border-r border-slate-800 bg-slate-900 md:flex">
        <div className="flex h-14 items-center gap-2 border-b border-slate-800 px-4">
          <span className="flex h-7 w-7 items-center justify-center rounded bg-teal-500 font-bold text-slate-950">C</span>
          <div className="leading-tight">
            <div className="text-sm font-semibold tracking-wide">CATRIX</div>
            <div className="text-[10px] uppercase tracking-wider text-slate-500">CA catastrophe risk</div>
          </div>
        </div>
        <div className="flex-1 overflow-y-auto p-3">
          <NavLinks />
        </div>
        <div className="border-t border-slate-800 p-3 text-[11px] leading-snug text-slate-500">
          Screening analytics for portfolio review. Scores are relative indices, not probabilities, premiums or
          real-time feeds.
        </div>
      </aside>

      {/* Main column */}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="relative z-20 flex h-14 shrink-0 items-center gap-3 border-b border-slate-800 bg-slate-900 px-4">
          <button
            type="button"
            aria-label={drawerOpen ? 'Close navigation' : 'Open navigation'}
            aria-expanded={drawerOpen}
            onClick={() => setDrawerOpen((o) => !o)}
            className="btn-secondary h-9 w-9 !p-0 md:hidden"
          >
            <span className="flex flex-col gap-1">
              <span className="block h-0.5 w-4 bg-slate-200" />
              <span className="block h-0.5 w-4 bg-slate-200" />
              <span className="block h-0.5 w-4 bg-slate-200" />
            </span>
          </button>
          <div className="flex items-center gap-2 md:hidden">
            <span className="flex h-6 w-6 items-center justify-center rounded bg-teal-500 text-xs font-bold text-slate-950">
              C
            </span>
            <span className="text-sm font-semibold">CATRIX</span>
          </div>
          <div className="ml-auto flex items-center gap-4">
            <EnvBadge />
            <HealthIndicator />
          </div>
        </header>

        {/* Mobile drawer */}
        {drawerOpen && (
          <div className="relative z-10 md:hidden">
            <div className="absolute inset-x-0 top-0 border-b border-slate-800 bg-slate-900 p-3 shadow-2xl">
              <NavLinks onNavigate={() => setDrawerOpen(false)} />
            </div>
            <button
              type="button"
              aria-label="Close navigation"
              className="fixed inset-0 top-14 bg-slate-950/60"
              onClick={() => setDrawerOpen(false)}
            />
          </div>
        )}

        <main className="min-h-0 flex-1 overflow-y-auto">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
