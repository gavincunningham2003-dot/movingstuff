import clsx from 'clsx';

interface LoadingStateProps {
  label?: string;
  className?: string;
  compact?: boolean;
}

export function LoadingState({ label = 'Loading…', className, compact }: LoadingStateProps) {
  return (
    <div
      role="status"
      aria-live="polite"
      className={clsx(
        'flex items-center justify-center gap-3 text-sm text-slate-400',
        compact ? 'py-3' : 'py-12',
        className,
      )}
    >
      <span className="inline-block h-4 w-4 animate-spin rounded-full border-2 border-slate-600 border-t-teal-400" />
      <span>{label}</span>
    </div>
  );
}

/** Grey placeholder block for skeleton layouts. */
export function Skeleton({ className }: { className?: string }) {
  return <div className={clsx('animate-pulse rounded-md bg-slate-800', className)} />;
}
