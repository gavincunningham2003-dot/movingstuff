import type { ReactNode } from 'react';

import { ErrorState } from './ErrorState';
import { LoadingState } from './LoadingState';

interface QueryGateProps {
  isLoading: boolean;
  error: unknown;
  onRetry?: () => void;
  loadingLabel?: string;
  compact?: boolean;
  children: ReactNode;
}

/** Renders loading / error states for a query, otherwise its children. */
export function QueryGate({ isLoading, error, onRetry, loadingLabel, compact, children }: QueryGateProps) {
  if (isLoading) return <LoadingState label={loadingLabel} compact={compact} />;
  if (error) return <ErrorState error={error} onRetry={onRetry} compact={compact} />;
  return <>{children}</>;
}
