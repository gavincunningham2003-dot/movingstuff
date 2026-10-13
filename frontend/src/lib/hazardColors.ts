/**
 * Hazard class colour conventions shared by badges, charts and map layers.
 */

export const COLORS = {
  wildfireVeryHigh: '#dc2626',
  wildfireHigh: '#f97316',
  wildfireModerate: '#facc15',
  none: '#64748b',
  floodSfha: '#3b82f6',
  floodXShaded: '#93c5fd',
  floodX: '#475569',
  tsunami: '#a855f7',
  teal: '#2dd4bf',
  tealDark: '#0d9488',
  amber: '#f59e0b',
  red: '#ef4444',
  slate: '#94a3b8',
  slateDark: '#334155',
  blue: '#60a5fa',
  purple: '#a78bfa',
} as const;

/** Categorical palette for charts (hazard-neutral series). */
export const SERIES_PALETTE = [
  '#2dd4bf',
  '#60a5fa',
  '#f59e0b',
  '#a78bfa',
  '#f472b6',
  '#34d399',
  '#fb923c',
  '#94a3b8',
];

export function seriesColor(i: number): string {
  return SERIES_PALETTE[i % SERIES_PALETTE.length];
}

export function wildfireClassColor(cls: string | null | undefined): string {
  const c = (cls ?? '').trim().toLowerCase();
  if (c === 'very high' || c === 'very_high' || c === 'veryhigh') return COLORS.wildfireVeryHigh;
  if (c === 'high') return COLORS.wildfireHigh;
  if (c === 'moderate' || c === 'medium') return COLORS.wildfireModerate;
  return COLORS.none;
}

/** True for NFIP Special Flood Hazard Area zones (A*, V*). */
export function isSfhaZone(zone: string | null | undefined): boolean {
  const z = (zone ?? '').trim().toUpperCase();
  if (!z) return false;
  // The flood module labels classes descriptively ("Special Flood Hazard Area (1% annual
  // chance)"); raw FEMA zone codes (A, AE, V, ...) are matched below.
  if (z.includes('SPECIAL FLOOD HAZARD')) return true;
  if (z.startsWith('A') || z.startsWith('V')) return true;
  return z === 'SFHA' || z === 'IN_SFHA';
}

export function isShadedX(zone: string | null | undefined): boolean {
  const z = (zone ?? '').trim().toUpperCase();
  return (
    z === 'X SHADED' ||
    z === 'X_SHADED' ||
    z === 'X (SHADED)' ||
    z === 'X500' ||
    z === 'B' ||
    z.includes('0.2 PCT') ||
    z.includes('0.2%') ||
    z.includes('SHADED')
  );
}

export function floodZoneColor(zone: string | null | undefined): string {
  const z = (zone ?? '').trim().toUpperCase();
  if (!z) return COLORS.none;
  if (isSfhaZone(z)) return COLORS.floodSfha;
  if (isShadedX(z)) return COLORS.floodXShaded;
  if (z.startsWith('X') || z === 'C' || z === 'NOT_IN_SFHA' || z === 'NON-SFHA') return COLORS.floodX;
  return COLORS.none;
}

export function floodZoneLabel(zone: string | null | undefined): string {
  const z = (zone ?? '').trim();
  if (!z) return 'Unknown';
  if (isSfhaZone(z)) return `${z} (SFHA)`;
  if (isShadedX(z)) return `${z} (0.2% annual chance)`;
  return z;
}

/** Interpolated colour for a PGA value in g (0-1). */
export function pgaColor(g: number | null | undefined): string {
  if (g === null || g === undefined || !Number.isFinite(g)) return COLORS.none;
  if (g >= 0.8) return '#7f1d1d';
  if (g >= 0.6) return COLORS.wildfireVeryHigh;
  if (g >= 0.4) return COLORS.wildfireHigh;
  if (g >= 0.2) return COLORS.wildfireModerate;
  return '#86efac';
}

/** Colour for the 0-100 composite screening index (not a probability). */
export function scoreColor(score: number | null | undefined): string {
  if (score === null || score === undefined || !Number.isFinite(score)) return COLORS.none;
  if (score >= 75) return COLORS.wildfireVeryHigh;
  if (score >= 50) return COLORS.wildfireHigh;
  if (score >= 25) return COLORS.wildfireModerate;
  return COLORS.teal;
}

/** Best-effort colour for any hazard class label given the hazard id. */
export function hazardClassColor(hazardId: string, cls: string | null | undefined): string {
  const h = hazardId.toLowerCase();
  if (h.includes('wildfire') || h.includes('fhsz') || h.includes('fire')) return wildfireClassColor(cls);
  if (h.includes('flood') || h.includes('nfhl')) return floodZoneColor(cls);
  if (h.includes('tsunami')) {
    const c = (cls ?? '').toLowerCase();
    return c && c !== 'none' && c !== 'outside' && c !== 'not in zone' ? COLORS.tsunami : COLORS.none;
  }
  return COLORS.slate;
}

export const COVERAGE_STATUS_COLORS: Record<string, string> = {
  covered: COLORS.teal,
  not_in_hazard_area: COLORS.blue,
  outside_coverage: COLORS.amber,
  unavailable: COLORS.none,
};
