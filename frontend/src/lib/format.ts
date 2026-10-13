const intFmt = new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 });
const pctFmt = new Intl.NumberFormat('en-US', { minimumFractionDigits: 1, maximumFractionDigits: 1 });
const usdFmt = new Intl.NumberFormat('en-US', {
  style: 'currency',
  currency: 'USD',
  maximumFractionDigits: 0,
});
const usdCompactFmt = new Intl.NumberFormat('en-US', {
  style: 'currency',
  currency: 'USD',
  notation: 'compact',
  maximumFractionDigits: 1,
});
const compactFmt = new Intl.NumberFormat('en-US', { notation: 'compact', maximumFractionDigits: 1 });

export const DASH = '—';

export function isNum(v: unknown): v is number {
  return typeof v === 'number' && Number.isFinite(v);
}

/** Integer with thousands separators. */
export function fmtInt(v: number | null | undefined): string {
  return isNum(v) ? intFmt.format(v) : DASH;
}

/** Number with a fixed number of decimals. */
export function fmtNum(v: number | null | undefined, decimals = 2): string {
  if (!isNum(v)) return DASH;
  return new Intl.NumberFormat('en-US', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  }).format(v);
}

/** Compact number (1.2K, 3.4M). */
export function fmtCompact(v: number | null | undefined): string {
  return isNum(v) ? compactFmt.format(v) : DASH;
}

/** Percentage with one decimal. `v` is already in percent units (0-100). */
export function fmtPct(v: number | null | undefined): string {
  return isNum(v) ? `${pctFmt.format(v)}%` : DASH;
}

/** Fraction (0-1) rendered as a percentage with one decimal. */
export function fmtFracPct(v: number | null | undefined): string {
  return isNum(v) ? `${pctFmt.format(v * 100)}%` : DASH;
}

export function fmtUsd(v: number | null | undefined): string {
  return isNum(v) ? usdFmt.format(v) : DASH;
}

export function fmtUsdCompact(v: number | null | undefined): string {
  return isNum(v) ? usdCompactFmt.format(v) : DASH;
}

export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return DASH;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString('en-GB', { year: 'numeric', month: 'short', day: '2-digit' });
}

export function fmtDateTime(iso: string | null | undefined): string {
  if (!iso) return DASH;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString('en-GB', {
    year: 'numeric',
    month: 'short',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}

/** Score (0-100 screening index) with one decimal. Never a probability. */
export function fmtScore(v: number | null | undefined): string {
  return isNum(v) ? pctFmt.format(v) : DASH;
}

export function fmtCoord(v: number | null | undefined): string {
  return isNum(v) ? v.toFixed(5) : DASH;
}

/** snake_case / kebab-case to "Title case" words. */
export function humanize(key: string): string {
  const s = key.replace(/[_-]+/g, ' ').trim();
  return s.charAt(0).toUpperCase() + s.slice(1);
}

/** Generic value formatter for unknown API dict values. */
export function fmtAny(v: unknown): string {
  if (v === null || v === undefined || v === '') return DASH;
  if (typeof v === 'number') return Number.isInteger(v) ? fmtInt(v) : fmtNum(v, 2);
  if (typeof v === 'boolean') return v ? 'Yes' : 'No';
  if (typeof v === 'string') return v;
  if (Array.isArray(v)) return v.map(fmtAny).join(', ');
  try {
    return JSON.stringify(v);
  } catch {
    return String(v);
  }
}

export function clamp(v: number, lo: number, hi: number): number {
  return Math.min(hi, Math.max(lo, v));
}
