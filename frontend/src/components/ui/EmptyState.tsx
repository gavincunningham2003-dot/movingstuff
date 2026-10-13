import clsx from 'clsx';
import type { ReactNode } from 'react';

interface EmptyStateProps {
  title?: ReactNode;
  message?: ReactNode;
  action?: ReactNode;
  className?: string;
  compact?: boolean;
}

export const PIPELINE_NOT_RUN =
  'The ingestion pipeline has not run yet, so the API returned no records. Run the backend pipeline to populate this view.';

export function EmptyState({
  title = 'No data',
  message = PIPELINE_NOT_RUN,
  action,
  className,
  compact,
}: EmptyStateProps) {
  return (
    <div
      className={clsx(
        'flex flex-col items-center justify-center rounded-lg border border-dashed border-slate-700 text-center',
        compact ? 'px-3 py-4' : 'px-4 py-10',
        className,
      )}
    >
      <div className="text-sm font-semibold text-slate-300">{title}</div>
      {message && <div className="mt-1 max-w-md text-sm text-slate-500">{message}</div>}
      {action && <div className="mt-3">{action}</div>}
    </div>
  );
}
