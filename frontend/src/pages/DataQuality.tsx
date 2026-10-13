import { useDataQuality, useDataSources } from '../api/hooks';
import type { DataSourceOut, DatasetQualityOut, QualityCheckOut } from '../api/types';
import { PageBody } from '../components/layout/PageBody';
import { Badge, CheckStatusBadge, IngestionBadge, VerificationBadge } from '../components/ui/Badge';
import { Card } from '../components/ui/Card';
import { CaveatBanner } from '../components/ui/CaveatBanner';
import { DataTable, type Column } from '../components/ui/DataTable';
import { EmptyState } from '../components/ui/EmptyState';
import { ErrorState } from '../components/ui/ErrorState';
import { LoadingState } from '../components/ui/LoadingState';
import { PageHeader } from '../components/ui/PageHeader';
import { StatTile } from '../components/ui/StatTile';
import { fmtAny, fmtDateTime, fmtInt, fmtPct, humanize } from '../lib/format';

const sourceColumns: Column<DataSourceOut>[] = [
  {
    key: 'name',
    header: 'Dataset',
    render: (s) => (
      <div>
        <div className="text-slate-100">
          {s.url ? (
            <a href={s.url} target="_blank" rel="noreferrer" className="hover:text-teal-300 hover:underline">
              {s.name}
            </a>
          ) : (
            s.name
          )}
        </div>
        <div className="font-mono text-[11px] text-slate-500">{s.id}</div>
      </div>
    ),
  },
  { key: 'provider', header: 'Provider' },
  { key: 'category', header: 'Category', render: (s) => <Badge>{s.category}</Badge> },
  { key: 'verification_status', header: 'Verification', render: (s) => <VerificationBadge status={s.verification_status} /> },
  { key: 'auth_required', header: 'Auth', align: 'center', value: (s) => (s.auth_required ? 1 : 0), render: (s) => (s.auth_required ? <Badge tone="amber">required</Badge> : <span className="text-slate-500">none</span>) },
  { key: 'verified_on', header: 'Verified on', value: (s) => s.verified_on ?? null, className: 'text-xs' },
  { key: 'licence', header: 'Licence', value: (s) => s.licence ?? null, className: 'max-w-[180px] truncate text-xs' },
  {
    key: 'coverage',
    header: 'Coverage',
    value: (s) => s.geographic_coverage ?? null,
    render: (s) => (
      <div className="text-xs">
        <div>{s.geographic_coverage ?? '—'}</div>
        {s.temporal_coverage && <div className="text-slate-500">{s.temporal_coverage}</div>}
      </div>
    ),
  },
  { key: 'format', header: 'Format', value: (s) => s.format ?? null, className: 'text-xs' },
  { key: 'last_ingested_at', header: 'Last ingested', value: (s) => s.last_ingested_at ?? null, render: (s) => fmtDateTime(s.last_ingested_at), className: 'text-xs' },
  { key: 'record_count', header: 'Records', align: 'right', value: (s) => s.record_count ?? null, render: (s) => fmtInt(s.record_count) },
  { key: 'version', header: 'Version', value: (s) => s.version ?? null, className: 'font-mono text-xs' },
  { key: 'ingestion_status', header: 'Ingestion', render: (s) => <IngestionBadge status={s.ingestion_status} /> },
];

const checkColumns: Column<QualityCheckOut>[] = [
  { key: 'name', header: 'Check' },
  { key: 'status', header: 'Status', render: (c) => <CheckStatusBadge status={c.status} /> },
  { key: 'n_checked', header: 'Checked', align: 'right', render: (c) => fmtInt(c.n_checked) },
  {
    key: 'n_failed',
    header: 'Failed',
    align: 'right',
    render: (c) => (
      <span className={c.n_failed > 0 ? 'text-red-300' : undefined}>
        {fmtInt(c.n_failed)}
        {c.n_checked > 0 && <span className="ml-1 text-xs text-slate-500">({fmtPct((c.n_failed / c.n_checked) * 100)})</span>}
      </span>
    ),
  },
  { key: 'detail', header: 'Detail', value: (c) => c.detail ?? null, className: 'max-w-md whitespace-normal text-xs text-slate-400' },
];

const datasetQualityColumns: Column<DatasetQualityOut>[] = [
  {
    key: 'name',
    header: 'Dataset',
    render: (d) => (
      <div>
        <div className="text-slate-100">{d.name}</div>
        <div className="font-mono text-[11px] text-slate-500">{d.source_id}</div>
      </div>
    ),
  },
  { key: 'verification_status', header: 'Verification', render: (d) => <VerificationBadge status={d.verification_status} /> },
  { key: 'ingestion_status', header: 'Ingestion', render: (d) => <IngestionBadge status={d.ingestion_status} /> },
  { key: 'last_ingested_at', header: 'Last ingested', render: (d) => fmtDateTime(d.last_ingested_at), className: 'text-xs' },
  { key: 'record_count', header: 'Records', align: 'right', render: (d) => fmtInt(d.record_count) },
  { key: 'version', header: 'Version', className: 'font-mono text-xs' },
  { key: 'coverage_pct', header: 'Coverage', align: 'right', render: (d) => fmtPct(d.coverage_pct) },
  { key: 'missing_rate_pct', header: 'Missing', align: 'right', render: (d) => fmtPct(d.missing_rate_pct) },
  {
    key: 'errors',
    header: 'Errors',
    value: (d) => d.errors.length,
    render: (d) =>
      d.errors.length === 0 ? (
        <span className="text-slate-500">none</span>
      ) : (
        <ul className="max-w-md list-disc whitespace-normal pl-4 text-xs text-red-200">
          {d.errors.map((e, i) => (
            <li key={i}>{e}</li>
          ))}
        </ul>
      ),
  },
];

export function DataQuality() {
  const sourcesQ = useDataSources();
  const qualityQ = useDataQuality();

  const sources = sourcesQ.data ?? [];
  const quality = qualityQ.data;
  const ingested = sources.filter((s) => s.ingestion_status === 'ingested').length;
  const checks = quality?.checks ?? [];
  const nPass = checks.filter((c) => c.status === 'pass').length;
  const nWarn = checks.filter((c) => c.status === 'warn').length;
  const nFail = checks.filter((c) => c.status === 'fail').length;

  return (
    <PageBody>
      <PageHeader
        title="Data quality & provenance"
        description="Where every dataset comes from, when it was last ingested, and which integrity checks passed."
        meta={quality ? `Quality report generated ${fmtDateTime(quality.generated_at)}` : undefined}
      />

      <CaveatBanner tone="blue" title="Nothing on this platform is real-time" className="mb-4">
        All hazard layers, property attributes and model outputs are snapshots from the last ingestion run. Use the
        ingestion timestamps below to judge staleness before relying on any figure.
      </CaveatBanner>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-5">
        <StatTile label="Registered sources" value={sourcesQ.isLoading ? '…' : fmtInt(sources.length)} />
        <StatTile label="Ingested" value={sourcesQ.isLoading ? '…' : fmtInt(ingested)} hint={sources.length ? `${fmtPct((ingested / sources.length) * 100)} of sources` : undefined} accent="teal" />
        <StatTile label="Checks passed" value={qualityQ.isLoading ? '…' : fmtInt(nPass)} hint={`${fmtInt(nWarn)} warn · ${fmtInt(nFail)} fail`} accent={nFail > 0 ? 'red' : nWarn > 0 ? 'amber' : 'default'} />
        <StatTile label="Unavailable sources" value={qualityQ.isLoading ? '…' : fmtInt(quality?.unavailable_sources.length ?? 0)} accent={(quality?.unavailable_sources.length ?? 0) > 0 ? 'amber' : 'default'} />
        <StatTile label="Model version" value={<span className="font-mono text-base">{quality?.model_version ?? '—'}</span>} hint={quality && !quality.model_version ? 'No trained model registered' : undefined} />
      </div>

      <Card title="Dataset provenance" subtitle="Registered data sources and their ingestion state" className="mt-4" flush>
        {sourcesQ.isLoading ? (
          <LoadingState label="Loading data sources…" />
        ) : sourcesQ.isError ? (
          <div className="p-4">
            <ErrorState error={sourcesQ.error} onRetry={() => void sourcesQ.refetch()} />
          </div>
        ) : sources.length === 0 ? (
          <div className="p-4">
            <EmptyState title="No data sources registered" message="The API returned no sources. The data_sources.yaml registry may not have been loaded." />
          </div>
        ) : (
          <DataTable columns={sourceColumns} rows={sources} rowKey={(s) => s.id} dense maxHeight="70vh" />
        )}
      </Card>

      {qualityQ.isLoading ? (
        <Card className="mt-4">
          <LoadingState label="Loading quality report…" />
        </Card>
      ) : qualityQ.isError ? (
        <Card className="mt-4">
          <ErrorState error={qualityQ.error} onRetry={() => void qualityQ.refetch()} />
        </Card>
      ) : quality ? (
        <>
          <Card title="Quality checks" subtitle="Integrity checks run after ingestion" className="mt-4" flush>
            {checks.length === 0 ? (
              <div className="p-4">
                <EmptyState compact title="No checks recorded" message="The pipeline has not produced a quality report yet." />
              </div>
            ) : (
              <DataTable columns={checkColumns} rows={checks} rowKey={(c) => c.name} dense />
            )}
          </Card>

          <Card title="Dataset quality" subtitle="Coverage and missing-rate per ingested dataset" className="mt-4" flush>
            {quality.datasets.length === 0 ? (
              <div className="p-4">
                <EmptyState compact title="No dataset metrics" message="No datasets have been profiled yet." />
              </div>
            ) : (
              <DataTable columns={datasetQualityColumns} rows={quality.datasets} rowKey={(d) => d.source_id} dense />
            )}
          </Card>

          <div className="mt-4 grid grid-cols-1 gap-4 xl:grid-cols-2">
            <Card title="Processing errors" subtitle={`${fmtInt(quality.processing_errors.length)} recorded`}>
              {quality.processing_errors.length === 0 ? (
                <div className="text-sm text-slate-500">No processing errors recorded.</div>
              ) : (
                <ul className="space-y-2">
                  {quality.processing_errors.map((e, i) => (
                    <li key={i} className="rounded border border-red-500/30 bg-red-500/5 p-2 text-xs">
                      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5">
                        {Object.entries(e).map(([k, v]) => (
                          <div key={k} className="contents">
                            <dt className="text-slate-400">{humanize(k)}</dt>
                            <dd className="break-words text-slate-200">{fmtAny(v)}</dd>
                          </div>
                        ))}
                      </dl>
                    </li>
                  ))}
                </ul>
              )}
            </Card>
            <div className="space-y-4">
              <Card title="Unavailable sources" subtitle="Registered but not reachable or not ingested">
                {quality.unavailable_sources.length === 0 ? (
                  <div className="text-sm text-slate-500">All registered sources are available.</div>
                ) : (
                  <div className="flex flex-wrap gap-1.5">
                    {quality.unavailable_sources.map((s) => (
                      <Badge key={s} tone="amber" className="font-mono">
                        {s}
                      </Badge>
                    ))}
                  </div>
                )}
              </Card>
              <Card title="Report note">
                <p className="text-sm text-slate-300">{quality.note}</p>
              </Card>
            </div>
          </div>
        </>
      ) : null}
    </PageBody>
  );
}
