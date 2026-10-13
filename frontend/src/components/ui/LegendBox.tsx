import clsx from 'clsx';
import type { ReactNode } from 'react';

export interface LegendItem {
  label: ReactNode;
  color: string;
  shape?: 'circle' | 'square' | 'line';
}

interface LegendBoxProps {
  title?: ReactNode;
  items: LegendItem[];
  note?: ReactNode;
  className?: string;
  inline?: boolean;
}

export function LegendBox({ title, items, note, className, inline }: LegendBoxProps) {
  return (
    <div className={clsx('text-xs text-slate-300', className)}>
      {title && <div className="mb-1 font-semibold uppercase tracking-wide text-slate-400">{title}</div>}
      <ul className={clsx(inline ? 'flex flex-wrap gap-x-4 gap-y-1' : 'space-y-1')}>
        {items.map((it, i) => (
          <li key={i} className="flex items-center gap-2">
            <span
              aria-hidden
              className={clsx(
                'inline-block shrink-0',
                it.shape === 'line' ? 'h-0.5 w-4' : 'h-3 w-3',
                it.shape === 'circle' && 'rounded-full',
                it.shape !== 'line' && it.shape !== 'circle' && 'rounded-sm',
              )}
              style={{ backgroundColor: it.color }}
            />
            <span className="truncate">{it.label}</span>
          </li>
        ))}
      </ul>
      {note && <div className="mt-1 text-slate-500">{note}</div>}
    </div>
  );
}
