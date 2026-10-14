/**
 * Derived statistics from hazard_distribution / coverage dictionaries that are
 * shared by the executive overview and portfolio analysis pages.
 */

import type { CoverageCounts, HazardDistribution } from '../api/types';
import { isSfhaZone } from './hazardColors';

export function sumCounts(counts: Record<string, number> | undefined): number {
  if (!counts) return 0;
  return Object.values(counts).reduce((a, b) => a + (Number.isFinite(b) ? b : 0), 0);
}

/** Find the hazard_distribution key for a hazard family ("wildfire", "flood", ...). */
export function findHazardKey(dist: HazardDistribution | undefined, prefix: string): string | null {
  if (!dist) return null;
  const keys = Object.keys(dist);
  const exact = keys.find((k) => k === prefix);
  if (exact) return exact;
  return keys.find((k) => k.toLowerCase().startsWith(prefix.toLowerCase())) ?? null;
}

export function veryHighFhszCount(dist: HazardDistribution | undefined): number | null {
  const key = findHazardKey(dist, 'wildfire');
  if (!key || !dist) return null;
  const counts = dist[key];
  let n = 0;
  for (const [cls, c] of Object.entries(counts)) {
    const l = cls.trim().toLowerCase();
    if (l === 'very high' || l === 'very_high' || l === 'veryhigh') n += c;
  }
  return n;
}

export function sfhaCount(dist: HazardDistribution | undefined): number | null {
  const key = findHazardKey(dist, 'flood');
  if (!key || !dist) return null;
  const counts = dist[key];
  let n = 0;
  for (const [zone, c] of Object.entries(counts)) {
    if (isSfhaZone(zone)) n += c;
  }
  return n;
}

export function pctOf(part: number | null, total: number): number | null {
  if (part === null || !total) return null;
  return (part / total) * 100;
}

/** Share of (hazard, property) pairs with coverage_status === "covered". */
export function coverageCompleteness(coverage: CoverageCounts | undefined): number | null {
  if (!coverage) return null;
  let covered = 0;
  let total = 0;
  for (const counts of Object.values(coverage)) {
    for (const [status, c] of Object.entries(counts)) {
      total += c;
      if (status === 'covered') covered += c;
    }
  }
  return total ? (covered / total) * 100 : null;
}

export interface DistributionRow {
  label: string;
  count: number;
}

export function distributionRows(counts: Record<string, number> | undefined): DistributionRow[] {
  if (!counts) return [];
  return Object.entries(counts)
    .map(([label, count]) => ({ label, count }))
    .sort((a, b) => b.count - a.count);
}

/** Known summary keys of PortfolioAnalysisOut.summary with display labels. */
export const PORTFOLIO_SUMMARY_LABELS: Record<string, string> = {
  n_properties: 'Exposures',
  n_exposures: 'Exposures',
  n_locations: 'Distinct locations',
  n_rows: 'Rows received',
  n_valid: 'Valid rows',
  n_invalid: 'Invalid rows',
  n_duplicates: 'Exact duplicates',
  n_duplicates_excluded: 'Exact duplicates excluded',
  n_shared_location_exposures: 'Shared-location exposures',
  n_repeated_ids: 'Repeated ids',
  n_geocoded: 'Geocoded',
  n_counties: 'Counties',
  total_insured_value: 'Total insured value',
  total_property_value: 'Total property value',
  mean_composite_score: 'Mean screening index',
  median_composite_score: 'Median screening index',
  pct_fhsz_very_high: '% in Very High FHSZ',
  pct_sfha: '% in SFHA',
  n_fhsz_very_high: 'In Very High FHSZ',
  n_sfha: 'In SFHA',
  mean_pga_2pc50_g: 'Mean PGA (2% in 50 yr)',
};
