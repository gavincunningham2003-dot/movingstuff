import clsx from 'clsx';
import type { ReactNode } from 'react';

export function PageBody({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={clsx('mx-auto w-full max-w-[1600px] p-4 md:p-6', className)}>{children}</div>;
}
