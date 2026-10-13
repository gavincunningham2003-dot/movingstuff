import type { CountySummaryOut } from '../../api/types';
import { fmtInt, fmtNum, fmtPct, fmtScore, fmtUsdCompact } from '../../lib/format';
import { pctOf } from '../../lib/summaryStats';
import type { Column } from '../ui/DataTable';

/** Shared column set for CountySummaryOut tables (overview + portfolio analysis). */
export const countyColumns: Column<CountySummaryOut>[] = [
  { key: 'county', header: 'County', value: (r) => r.county ?? 'Unknown' },
  { key: 'county_fips', header: 'FIPS', value: (r) => r.county_fips ?? null, className: 'font-mono text-xs text-slate-400' },
  { key: 'n_properties', header: 'Properties', align: 'right', render: (r) => fmtInt(r.n_properties) },
  { key: 'share_pct', header: 'Share', align: 'right', render: (r) => fmtPct(r.share_pct) },
  {
    key: 'total_insured_value',
    header: 'Insured value',
    align: 'right',
    value: (r) => r.total_insured_value,
    render: (r) => fmtUsdCompact(r.total_insured_value),
  },
  {
    key: 'mean_composite_score',
    header: 'Mean index',
    align: 'right',
    value: (r) => r.mean_composite_score,
    render: (r) => fmtScore(r.mean_composite_score),
  },
  {
    key: 'n_fhsz_very_high',
    header: 'Very High FHSZ',
    align: 'right',
    render: (r) => (
      <span>
        {fmtInt(r.n_fhsz_very_high)}{' '}
        <span className="text-xs text-slate-500">({fmtPct(pctOf(r.n_fhsz_very_high, r.n_properties))})</span>
      </span>
    ),
  },
  {
    key: 'n_sfha',
    header: 'In SFHA',
    align: 'right',
    render: (r) => (
      <span>
        {fmtInt(r.n_sfha)} <span className="text-xs text-slate-500">({fmtPct(pctOf(r.n_sfha, r.n_properties))})</span>
      </span>
    ),
  },
  {
    key: 'mean_pga_2pc50_g',
    header: 'Mean PGA (g)',
    align: 'right',
    value: (r) => r.mean_pga_2pc50_g,
    render: (r) => fmtNum(r.mean_pga_2pc50_g, 3),
  },
];

export function countyRowKey(r: CountySummaryOut): string {
  return `${r.county_fips ?? ''}-${r.county ?? ''}`;
}
