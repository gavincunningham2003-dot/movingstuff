import clsx from 'clsx';
import type { ReactNode } from 'react';

export interface KeyValue {
  key: string;
  label: ReactNode;
  value: ReactNode;
}

interface KeyValueListProps {
  items: KeyValue[];
  className?: string;
  columns?: 1 | 2;
}

export function KeyValueList({ items, className, columns = 1 }: KeyValueListProps) {
  return (
    <dl className={clsx('grid gap-x-6 gap-y-2 text-sm', columns === 2 ? 'grid-cols-1 sm:grid-cols-2' : 'grid-cols-1', className)}>
      {items.map((it) => (
        <div key={it.key} className="flex items-baseline justify-between gap-3 border-b border-slate-800/70 pb-1.5 last:border-b-0">
          <dt className="shrink-0 text-slate-400">{it.label}</dt>
          <dd className="tabular min-w-0 truncate text-right text-slate-100">{it.value}</dd>
        </div>
      ))}
    </dl>
  );
}
