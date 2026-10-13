import clsx from 'clsx';

import { errorMessage, isApiError } from '../../api/client';

interface ErrorStateProps {
  error: unknown;
  onRetry?: () => void;
  title?: string;
  className?: string;
  compact?: boolean;
}

export function ErrorState({ error, onRetry, title = 'Request failed', className, compact }: ErrorStateProps) {
  const message = errorMessage(error);
  const url = isApiError(error) ? error.url : null;
  return (
    <div
      role="alert"
      className={clsx(
        'rounded-lg border border-red-500/40 bg-red-500/5 text-sm',
        compact ? 'px-3 py-2' : 'px-4 py-4',
        className,
      )}
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="font-semibold text-red-300">{title}</div>
          <div className="mt-0.5 break-words text-slate-300">{message}</div>
          {url && !compact && <div className="mt-1 break-all font-mono text-xs text-slate-500">{url}</div>}
        </div>
        {onRetry && (
          <button type="button" onClick={onRetry} className="btn-secondary shrink-0">
            Retry
          </button>
        )}
      </div>
    </div>
  );
}
