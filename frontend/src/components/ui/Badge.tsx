import clsx from 'clsx';
import type { ReactNode } from 'react';

import type { AssumptionKind, CoverageStatus, QualityCheckStatus } from '../../api/types';
import { COLORS, floodZoneColor, hazardClassColor, wildfireClassColor } from '../../lib/hazardColors';

export type BadgeTone = 'neutral' | 'teal' | 'amber' | 'red' | 'blue' | 'purple' | 'green';

const TONE: Record<BadgeTone, string> = {
  neutral: 'border-slate-700 bg-slate-800 text-slate-300',
  teal: 'border-teal-500/40 bg-teal-500/10 text-teal-300',
  amber: 'border-amber-500/40 bg-amber-500/10 text-amber-300',
  red: 'border-red-500/40 bg-red-500/10 text-red-300',
  blue: 'border-blue-500/40 bg-blue-500/10 text-blue-300',
  purple: 'border-purple-500/40 bg-purple-500/10 text-purple-300',
  green: 'border-emerald-500/40 bg-emerald-500/10 text-emerald-300',
};

interface BadgeProps {
  children: ReactNode;
  tone?: BadgeTone;
  /** Explicit colour swatch (hex) rendered as a dot before the label. */
  color?: string;
  className?: string;
  title?: string;
}

export function Badge({ children, tone = 'neutral', color, className, title }: BadgeProps) {
  return (
    <span
      title={title}
      className={clsx(
        'inline-flex items-center gap-1.5 whitespace-nowrap rounded-full border px-2 py-0.5 text-xs font-medium',
        TONE[tone],
        className,
      )}
    >
      {color && <span className="h-2 w-2 rounded-full" style={{ backgroundColor: color }} />}
      {children}
    </span>
  );
}

/** Badge whose dot colour follows the hazard class palette. */
export function HazardClassBadge({ hazardId, value }: { hazardId: string; value: string | null | undefined }) {
  if (!value) return <span className="text-slate-500">—</span>;
  return <Badge color={hazardClassColor(hazardId, value)}>{value}</Badge>;
}

export function WildfireBadge({ value }: { value: string | null | undefined }) {
  if (!value) return <span className="text-slate-500">—</span>;
  return <Badge color={wildfireClassColor(value)}>{value}</Badge>;
}

export function FloodZoneBadge({ value }: { value: string | null | undefined }) {
  if (!value) return <span className="text-slate-500">—</span>;
  return <Badge color={floodZoneColor(value)}>{value}</Badge>;
}

const COVERAGE_TONE: Record<CoverageStatus, BadgeTone> = {
  covered: 'teal',
  not_in_hazard_area: 'blue',
  outside_coverage: 'amber',
  unavailable: 'neutral',
};

const COVERAGE_LABEL: Record<CoverageStatus, string> = {
  covered: 'Covered',
  not_in_hazard_area: 'Not in hazard area',
  outside_coverage: 'Outside coverage',
  unavailable: 'Unavailable',
};

export function CoverageBadge({ status }: { status: CoverageStatus | string | null | undefined }) {
  if (!status) return <Badge>Unknown</Badge>;
  const tone = (COVERAGE_TONE as Record<string, BadgeTone>)[status] ?? 'neutral';
  const label = (COVERAGE_LABEL as Record<string, string>)[status] ?? status;
  return <Badge tone={tone}>{label}</Badge>;
}

export function ConfidenceBadge({ value }: { value: string | null | undefined }) {
  if (!value) return <span className="text-slate-500">—</span>;
  const tone: BadgeTone = value === 'high' ? 'teal' : value === 'medium' ? 'amber' : 'red';
  return <Badge tone={tone}>{value}</Badge>;
}

const KIND_TONE: Record<AssumptionKind | 'missing', BadgeTone> = {
  observed: 'teal',
  externally_derived: 'blue',
  assumed: 'amber',
  missing: 'red',
};

export function AssumptionKindBadge({ kind }: { kind: string }) {
  const tone = (KIND_TONE as Record<string, BadgeTone>)[kind] ?? 'neutral';
  return <Badge tone={tone}>{kind.replace(/_/g, ' ')}</Badge>;
}

const CHECK_TONE: Record<QualityCheckStatus, BadgeTone> = {
  pass: 'green',
  warn: 'amber',
  fail: 'red',
  skipped: 'neutral',
};

export function CheckStatusBadge({ status }: { status: string }) {
  const tone = (CHECK_TONE as Record<string, BadgeTone>)[status] ?? 'neutral';
  return <Badge tone={tone}>{status}</Badge>;
}

export function VerificationBadge({ status }: { status: string }) {
  const tone: BadgeTone = status === 'verified' ? 'green' : status === 'local_only' ? 'blue' : 'amber';
  return <Badge tone={tone}>{status.replace(/_/g, ' ')}</Badge>;
}

export function IngestionBadge({ status }: { status: string }) {
  const tone: BadgeTone =
    status === 'ingested' ? 'teal' : status === 'failed' ? 'red' : status === 'unavailable' ? 'amber' : 'neutral';
  return <Badge tone={tone}>{status.replace(/_/g, ' ')}</Badge>;
}

export function JobStatusBadge({ status }: { status: string }) {
  const tone: BadgeTone =
    status === 'completed' ? 'teal' : status === 'failed' ? 'red' : status === 'running' ? 'blue' : 'neutral';
  return <Badge tone={tone}>{status}</Badge>;
}

export function ScoreBadge({ score, label }: { score: number | null | undefined; label?: string }) {
  if (score === null || score === undefined) return <Badge>{label ?? 'No score'}</Badge>;
  const color =
    score >= 75 ? COLORS.wildfireVeryHigh : score >= 50 ? COLORS.wildfireHigh : score >= 25 ? COLORS.wildfireModerate : COLORS.teal;
  return (
    <Badge color={color} title="Screening index (0-100), not a probability">
      {label ?? `Index ${score.toFixed(1)}`}
    </Badge>
  );
}
