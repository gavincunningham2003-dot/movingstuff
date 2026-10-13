import { useEffect, useMemo, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';

import { useProperties, usePropertyCounties } from '../api/hooks';
import type { PropertySummaryOut } from '../api/types';
import { PageBody } from '../components/layout/PageBody';
import { FloodZoneBadge, WildfireBadge } from '../components/ui/Badge';
import { Card } from '../components/ui/Card';
import { ScreeningIndexCaveat } from '../components/ui/CaveatBanner';
import { DataTable, type Column } from '../components/ui/DataTable';
import { EmptyState } from '../components/ui/EmptyState';
import { ErrorState } from '../components/ui/ErrorState';
import { Field, Select, TextInput } from '../components/ui/Inputs';
import { LoadingState } from '../components/ui/LoadingState';
import { PageHeader } from '../components/ui/PageHeader';
import { fmtInt, fmtNum, fmtScore } from '../lib/format';
import { useDebouncedValue } from '../lib/useDebouncedValue';

const LIMIT = 25;

const columns: Column<PropertySummaryOut>[] = [
  { key: 'id', header: 'ID', align: 'right', className: 'font-mono text-xs text-slate-400', width: '72px' },
  { key: 'address', header: 'Address', value: (r) => r.address ?? null },
  { key: 'city', header: 'City', value: (r) => r.city ?? null },
  { key: 'postcode', header: 'ZIP', value: (r) => r.postcode ?? null, className: 'font-mono text-xs' },
  { key: 'county', header: 'County', value: (r) => r.county ?? null },
  { key: 'wildfire_fhsz', header: 'Wildfire FHSZ', value: (r) => r.wildfire_fhsz ?? null, render: (r) => <WildfireBadge value={r.wildfire_fhsz} /> },
  { key: 'flood_zone', header: 'Flood zone', value: (r) => r.flood_zone ?? null, render: (r) => <FloodZoneBadge value={r.flood_zone} /> },
  { key: 'pga_2pc50_g', header: 'PGA 2%/50yr (g)', align: 'right', value: (r) => r.pga_2pc50_g, render: (r) => fmtNum(r.pga_2pc50_g, 3) },
  { key: 'composite_score', header: 'Screening index', align: 'right', value: (r) => r.composite_score, render: (r) => fmtScore(r.composite_score) },
];

export function PropertyExplorer() {
  const navigate = useNavigate();
  const [q, setQ] = useState('');
  const [county, setCounty] = useState('');
  const [offset, setOffset] = useState(0);
  const debouncedQ = useDebouncedValue(q.trim(), 400);

  useEffect(() => {
    setOffset(0);
  }, [debouncedQ, county]);

  const countiesQ = usePropertyCounties();
  const listQ = useProperties(debouncedQ, county, LIMIT, offset);

  const countyOptions = useMemo(
    () =>
      (countiesQ.data ?? [])
        .filter((c) => !!c.county)
        .sort((a, b) => (a.county ?? '').localeCompare(b.county ?? ''))
        .map((c) => ({ value: c.county ?? '', label: `${c.county} (${fmtInt(c.n)})` })),
    [countiesQ.data],
  );

  const numericId = /^\d+$/.test(debouncedQ) ? debouncedQ : null;
  const data = listQ.data;
  const total = data?.total ?? 0;
  const from = total === 0 ? 0 : offset + 1;
  const to = Math.min(offset + LIMIT, total);
  const filtered = debouncedQ !== '' || county !== '';

  return (
    <PageBody>
      <PageHeader
        title="Property explorer"
        description="Search the ingested property dataset by address text or numeric property id, then open a property for its full hazard profile."
      />

      <Card className="mb-4">
        <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
          <Field label="Search (address or id)" className="md:col-span-2">
            <TextInput value={q} onChange={setQ} placeholder="e.g. 1200 Main St, Paradise or 4821" autoFocus />
          </Field>
          <Field label="County" hint={countiesQ.isError ? 'County list unavailable' : undefined}>
            <Select
              value={county}
              onChange={setCounty}
              options={countyOptions}
              placeholder={countiesQ.isLoading ? 'Loading counties…' : 'All counties'}
              disabled={countiesQ.isLoading}
            />
          </Field>
        </div>
        {numericId && (
          <div className="mt-3 text-sm text-slate-400">
            Looks like an id:{' '}
            <Link to={`/properties/${numericId}`} className="text-teal-300 hover:underline">
              open property #{numericId} directly
            </Link>
          </div>
        )}
      </Card>

      <Card
        title="Results"
        subtitle={data ? `Showing ${from}–${to} of ${fmtInt(total)}` : undefined}
        actions={
          data && total > LIMIT ? (
            <div className="flex items-center gap-2">
              <button type="button" className="btn-secondary text-xs" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - LIMIT))}>
                Previous
              </button>
              <button type="button" className="btn-secondary text-xs" disabled={to >= total} onClick={() => setOffset(offset + LIMIT)}>
                Next
              </button>
            </div>
          ) : undefined
        }
        flush
      >
        {listQ.isLoading ? (
          <LoadingState label="Searching properties…" />
        ) : listQ.isError ? (
          <div className="p-4">
            <ErrorState error={listQ.error} onRetry={() => void listQ.refetch()} />
          </div>
        ) : data && data.items.length === 0 ? (
          <div className="p-4">
            {filtered ? (
              <EmptyState title="No matching properties" message="Try a different address fragment, id, or county." />
            ) : (
              <EmptyState title="No properties ingested" />
            )}
          </div>
        ) : data ? (
          <DataTable
            columns={columns}
            rows={data.items}
            rowKey={(r) => r.id}
            onRowClick={(r) => navigate(`/properties/${r.id}`)}
            dense
            className={listQ.isFetching ? 'opacity-70' : undefined}
          />
        ) : null}
      </Card>

      <ScreeningIndexCaveat className="mt-4" />
    </PageBody>
  );
}
