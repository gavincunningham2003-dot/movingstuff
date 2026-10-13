import clsx from 'clsx';
import type { ReactNode } from 'react';

interface StatTileProps {
  label: ReactNode;
  value: ReactNode;
  hint?: ReactNode;
  accent?: 'default' | 'teal' | 'amber' | 'red' | 'blue';
  className?: string;
}

const ACCENT: Record<NonNullable<StatTileProps['accent']>, string> = {
  default: 'text-slate-100',
  teal: 'text-teal-300',
  amber: 'text-amber-300',
  red: 'text-red-400',
  blue: 'text-blue-300',
};

export function StatTile({ label, value, hint, accent = 'default', className }: StatTileProps) {
  return (
    <div className={clsx('panel px-4 py-3', className)}>
      <div className="text-xs font-medium uppercase tracking-wide text-slate-400">{label}</div>
      <div className={clsx('tabular mt-1 truncate text-2xl font-semibold', ACCENT[accent])}>{value}</div>
      {hint && <div className="mt-1 text-xs text-slate-500">{hint}</div>}
    </div>
  );
}
