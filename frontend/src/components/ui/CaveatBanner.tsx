import clsx from 'clsx';
import type { ReactNode } from 'react';

interface CaveatBannerProps {
  title?: ReactNode;
  children: ReactNode;
  className?: string;
  tone?: 'amber' | 'red' | 'blue';
}

const TONES = {
  amber: 'border-amber-500/40 bg-amber-500/10 text-amber-100',
  red: 'border-red-500/40 bg-red-500/10 text-red-100',
  blue: 'border-blue-500/40 bg-blue-500/10 text-blue-100',
};

const ICON_TONES = {
  amber: 'text-amber-400',
  red: 'text-red-400',
  blue: 'text-blue-400',
};

export function CaveatBanner({ title, children, className, tone = 'amber' }: CaveatBannerProps) {
  return (
    <div className={clsx('flex gap-3 rounded-lg border px-4 py-3 text-sm', TONES[tone], className)}>
      <span aria-hidden className={clsx('mt-0.5 shrink-0 font-bold', ICON_TONES[tone])}>
        !
      </span>
      <div className="min-w-0">
        {title && <div className="font-semibold">{title}</div>}
        <div className={clsx(title && 'mt-0.5', 'text-sm opacity-90')}>{children}</div>
      </div>
    </div>
  );
}

/** Standard caveat used wherever the composite score appears. */
export function ScreeningIndexCaveat({ className }: { className?: string }) {
  return (
    <CaveatBanner title="Screening index, not a probability" className={className}>
      The composite score is a 0-100 relative screening index built from hazard overlays. It is not a
      probability of loss, an expected loss, or a premium indication, and should not be used as one.
    </CaveatBanner>
  );
}

/** Bullet list of caveats returned by the API. */
export function CaveatList({ items, className }: { items: string[]; className?: string }) {
  if (!items.length) return null;
  return (
    <ul className={clsx('list-disc space-y-1 pl-5 text-sm text-amber-100/90', className)}>
      {items.map((c, i) => (
        <li key={i}>{c}</li>
      ))}
    </ul>
  );
}
