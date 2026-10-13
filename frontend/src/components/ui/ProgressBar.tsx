import clsx from 'clsx';

import { clamp } from '../../lib/format';

interface ProgressBarProps {
  /** 0-1 fraction or 0-100 percent; values > 1 are treated as percent. */
  value: number | null | undefined;
  label?: string;
  tone?: 'teal' | 'amber' | 'red';
  className?: string;
  indeterminate?: boolean;
}

const TONE = {
  teal: 'bg-teal-400',
  amber: 'bg-amber-400',
  red: 'bg-red-500',
};

export function ProgressBar({ value, label, tone = 'teal', className, indeterminate }: ProgressBarProps) {
  const raw = value ?? 0;
  const pct = clamp(raw > 1 ? raw : raw * 100, 0, 100);
  return (
    <div className={className}>
      {label && (
        <div className="mb-1 flex items-center justify-between text-xs text-slate-400">
          <span>{label}</span>
          {!indeterminate && <span className="tabular">{pct.toFixed(0)}%</span>}
        </div>
      )}
      <div className="h-2 w-full overflow-hidden rounded-full bg-slate-800">
        <div
          className={clsx('h-full rounded-full transition-all', TONE[tone], indeterminate && 'animate-pulse')}
          style={{ width: indeterminate ? '100%' : `${pct}%` }}
        />
      </div>
    </div>
  );
}

/** Thin inline bar for "contribution" style lists. */
export function MiniBar({
  fraction,
  color,
  className,
}: {
  fraction: number;
  color: string;
  className?: string;
}) {
  const pct = clamp(Math.abs(fraction) * 100, 0, 100);
  return (
    <div className={clsx('h-1.5 w-full overflow-hidden rounded-full bg-slate-800', className)}>
      <div className="h-full rounded-full" style={{ width: `${pct}%`, backgroundColor: color }} />
    </div>
  );
}
