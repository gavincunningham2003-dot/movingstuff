import clsx from 'clsx';
import type { ReactNode } from 'react';

interface CardProps {
  title?: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  bodyClassName?: string;
  /** Remove body padding (for tables / maps that fill the card). */
  flush?: boolean;
}

export function Card({ title, subtitle, actions, children, className, bodyClassName, flush }: CardProps) {
  return (
    <section className={clsx('panel flex flex-col', className)}>
      {(title || actions) && (
        <header className="flex items-start justify-between gap-3 border-b border-slate-800 px-4 py-3">
          <div className="min-w-0">
            {title && <h2 className="truncate text-sm font-semibold text-slate-100">{title}</h2>}
            {subtitle && <p className="mt-0.5 text-xs text-slate-400">{subtitle}</p>}
          </div>
          {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
        </header>
      )}
      <div className={clsx('min-w-0 flex-1', !flush && 'p-4', bodyClassName)}>{children}</div>
    </section>
  );
}
