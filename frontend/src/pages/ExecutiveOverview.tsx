import { Link } from 'react-router-dom';

import { useModelsMetrics, usePortfolioSummary } from '../api/hooks';
import type { ModelsMetricsResponse, OverviewModelSummary } from '../api/types';
import { DistributionBarChart } from '../components/charts/DistributionBarChart';
import { HazardDistributionPanel } from '../components/charts/HazardDistributionPanel';
import { PageBody } from '../components/layout/PageBody';
import { countyColumns, countyRowKey } from '../components/tables/countyColumns';
import { Badge } from '../components/ui/Badge';
import { Card } from '../components/ui/Card';
import { CaveatBanner, ScreeningIndexCaveat } from '../components/ui/CaveatBanner';
import { DataTable } from '../components/ui/DataTable';
import { EmptyState } from '../components/ui/EmptyState';
import { ErrorState } from '../components/ui/ErrorState';
import { KeyValueList } from '../components/ui/KeyValueList';
import { LoadingState } from '../components/ui/LoadingState';
import { PageHeader } from '../components/ui/PageHeader';
import { StatTile } from '../components/ui/StatTile';
import { fmtAny, fmtDateTime, fmtInt, fmtNum, fmtPct, fmtUsdCompact, humanize, isNum } from '../lib/format';
import { COLORS } from '../lib/hazardColors';
import { metricValue } from '../lib/modelData';
import { coverageCompleteness, pctOf, sfhaCount, veryHighFhszCount } from '../lib/summaryStats';

function exposureTotal(ev: Record<string, unknown>): { key: string; value: number } | null {
  for (const k of ['total_insured_value', 'total_value', 'tiv', 'total_property_value', 'total', 'sum']) {
    const v = ev[k];
    if (isNum(v)) return { key: k, value: v };
  }
  for (const [k, v] of Object.entries(ev)) {
    if (isNum(v) && /value|tiv|exposure/i.test(k)) return { key: k, value: v };
  }
  return null;
}

function formatExposureValue(key: string, v: unknown): string {
  if (isNum(v) && /value|tiv|exposure|total|sum/i.test(key) && !/count|n_|pct|share/i.test(key)) return fmtUsdCompact(v);
  if (isNum(v) && /pct|share/i.test(key)) return fmtPct(v);
  return fmtAny(v);
}

interface ModelPanelProps {
  metrics: ModelsMetricsResponse | undefined;
  isLoading: boolean;
  error: unknown;
  onRetry: () => void;
  summaryModel: OverviewModelSummary | null;
}

function ModelPerformancePanel({ metrics, isLoading, error, onRetry, summaryModel }: ModelPanelProps) {
  if (isLoading) return <LoadingState compact label="Loading model metrics…" />;
  if (error) return <ErrorState compact error={error} onRetry={onRetry} />;

  const status = metrics?.status ?? summaryModel?.status ?? 'not_trained';
  const selected =
    metrics?.models.find((m) => m.is_selected) ??
    metrics?.models.find((m) => m.name === metrics.selected_model) ??
    metrics?.models[0];

  if (status !== 'available' || !selected) {
    return (
      <EmptyState
        compact
        title={status === 'insufficient_data' ? 'Insufficient data to train the model' : 'Model not trained'}
        message={
          metrics?.status_detail ??
          summaryModel?.status_detail ??
          'No wildfire model has been trained yet. Run the training pipeline to populate performance metrics.'
        }
        action={
          <Link to="/models" className="text-xs text-teal-300 hover:underline">
            Open model analytics
          </Link>
        }
      />
    );
  }

  const roc = metricValue(selected.metrics, 'roc_auc', ['auc']) ?? summaryModel?.roc_auc ?? null;
  const pr = metricValue(selected.metrics, 'pr_auc', ['average_precision', 'ap']) ?? summaryModel?.pr_auc ?? null;
  const brier = metricValue(selected.metrics, 'brier', ['brier_score']) ?? summaryModel?.brier ?? null;
  const prevalence = isNum(selected.prevalence_test) ? selected.prevalence_test : summaryModel?.prevalence ?? null;

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2 text-xs text-slate-400">
        <Badge tone="teal">{selected.name}</Badge>
        <span className="font-mono">{selected.version}</span>
        <span>· {selected.algorithm}</span>
      </div>
      <div className="grid grid-cols-2 gap-3">
        <MetricCell label="ROC-AUC" value={fmtNum(roc, 3)} />
        <MetricCell label="PR-AUC" value={fmtNum(pr, 3)} />
        <MetricCell label="Brier score" value={fmtNum(brier, 4)} />
        <MetricCell label="Test prevalence" value={isNum(prevalence) ? fmtPct(prevalence * 100) : '—'} />
      </div>
      <div className="text-xs text-slate-500">
        Test period {selected.test_period}
        {selected.spatial_holdout ? ` · spatial holdout: ${selected.spatial_holdout}` : ''}
      </div>
      <Link to="/models" className="inline-block text-xs text-teal-300 hover:underline">
        Full model analytics →
      </Link>
    </div>
  );
}

function MetricCell({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-slate-800 bg-slate-950/60 px-3 py-2">
      <div className="text-[11px] uppercase tracking-wide text-slate-500">{label}</div>
      <div className="tabular text-lg font-semibold text-slate-100">{value}</div>
    </div>
  );
}

export function ExecutiveOverview() {
  const summaryQ = usePortfolioSummary();
  const metricsQ = useModelsMetrics();

  if (summaryQ.isLoading) {
    return (
      <PageBody>
        <PageHeader title="Executive overview" />
        <LoadingState label="Loading portfolio summary…" />
      </PageBody>
    );
  }

  if (summaryQ.isError) {
    return (
      <PageBody>
        <PageHeader title="Executive overview" />
        <ErrorState error={summaryQ.error} onRetry={() => void summaryQ.refetch()} />
      </PageBody>
    );
  }

  const summary = summaryQ.data;
  if (!summary) return null;

  const total = summary.total_properties;
  const vh = veryHighFhszCount(summary.hazard_distribution);
  const sfha = sfhaCount(summary.hazard_distribution);
  const completeness = coverageCompleteness(summary.coverage);
  const exposure = exposureTotal(summary.exposure_value);
  const hasInsuredValue = summary.counties.some((c) => isNum(c.total_insured_value));

  const countyRows = [...summary.counties].sort((a, b) => b.n_properties - a.n_properties);
  const countyChart = countyRows.slice(0, 15).map((c) => ({ label: c.county ?? 'Unknown', count: c.n_properties }));
  const exposureRows = [...summary.counties]
    .sort((a, b) =>
      hasInsuredValue
        ? (b.total_insured_value ?? -1) - (a.total_insured_value ?? -1)
        : b.n_properties - a.n_properties,
    )
    .slice(0, 12);

  const exposureItems = Object.entries(summary.exposure_value)
    .filter(([, v]) => v === null || ['number', 'string', 'boolean'].includes(typeof v))
    .map(([k, v]) => ({ key: k, label: humanize(k), value: formatExposureValue(k, v) }));

  return (
    <PageBody>
      <PageHeader
        title="Executive overview"
        description="Base property dataset as ingested by the pipeline: hazard exposure distribution, county concentration and current model status."
        meta={`Generated ${fmtDateTime(summary.generated_at)} · snapshot, not real-time`}
      />

      {total === 0 ? (
        <EmptyState
          title="No properties ingested"
          message="The API returned zero properties. The ingestion pipeline has not run, so there is nothing to summarise yet."
        />
      ) : (
        <>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 md:grid-cols-3 xl:grid-cols-6">
            <StatTile label="Total properties" value={fmtInt(total)} accent="teal" />
            <StatTile label="Counties" value={fmtInt(summary.counties.length)} />
            <StatTile
              label="In Very High FHSZ"
              value={fmtPct(pctOf(vh, total))}
              hint={vh !== null ? `${fmtInt(vh)} properties` : 'Wildfire FHSZ not in distribution'}
              accent="red"
            />
            <StatTile
              label="In SFHA (A/V zones)"
              value={fmtPct(pctOf(sfha, total))}
              hint={sfha !== null ? `${fmtInt(sfha)} properties` : 'Flood zones not in distribution'}
              accent="blue"
            />
            {exposure ? (
              <StatTile label="Exposure value" value={fmtUsdCompact(exposure.value)} hint={humanize(exposure.key)} />
            ) : (
              <StatTile label="Exposure value" value="—" hint="No value field in exposure_value" />
            )}
            <StatTile
              label="Coverage completeness"
              value={fmtPct(completeness)}
              hint="Share of hazard assessments with status covered"
              accent={completeness !== null && completeness < 80 ? 'amber' : 'default'}
            />
          </div>

          <div className="mt-4 grid grid-cols-1 gap-4 xl:grid-cols-3">
            <Card
              title="Counties by property count"
              subtitle={`Top ${countyChart.length} of ${summary.counties.length}`}
              className="xl:col-span-2"
            >
              {countyChart.length === 0 ? (
                <EmptyState compact title="No county breakdown" message="The summary contains no counties." />
              ) : (
                <DistributionBarChart
                  data={countyChart}
                  total={total}
                  colorFor={() => COLORS.teal}
                  height={Math.max(200, 24 * countyChart.length + 40)}
                  labelWidth={140}
                />
              )}
            </Card>
            <Card title="Wildfire model performance" subtitle="Selected model on the held-out test period">
              <ModelPerformancePanel
                metrics={metricsQ.data}
                isLoading={metricsQ.isLoading}
                error={metricsQ.error}
                onRetry={() => void metricsQ.refetch()}
                summaryModel={summary.model}
              />
            </Card>
          </div>

          <h2 className="mb-2 mt-6 text-sm font-semibold uppercase tracking-wide text-slate-400">Hazard distribution</h2>
          <HazardDistributionPanel distribution={summary.hazard_distribution} total={total} />

          <div className="mt-4 grid grid-cols-1 gap-4 xl:grid-cols-3">
            <Card
              title={hasInsuredValue ? 'Highest-exposure counties' : 'Largest counties by property count'}
              subtitle={
                hasInsuredValue
                  ? 'Ranked by total insured value'
                  : 'No insured values in the base dataset; ranked by property count'
              }
              className="xl:col-span-2"
              flush
            >
              <DataTable
                columns={countyColumns}
                rows={exposureRows}
                rowKey={countyRowKey}
                dense
                emptyMessage="No counties."
              />
            </Card>
            <Card title="Exposure value" subtitle="As reported by the API (exposure_value)">
              {exposureItems.length === 0 ? (
                <EmptyState compact title="No exposure value" message="The summary did not include any exposure value fields." />
              ) : (
                <KeyValueList items={exposureItems} />
              )}
            </Card>
          </div>

          <div className="mt-4 grid grid-cols-1 gap-4 xl:grid-cols-3">
            <Card title="Interpretation notes" subtitle="Returned with the summary" className="xl:col-span-2">
              {summary.interpretation_notes.length === 0 ? (
                <div className="text-sm text-slate-500">No interpretation notes were returned.</div>
              ) : (
                <ol className="list-decimal space-y-2 pl-5 text-sm text-slate-300">
                  {summary.interpretation_notes.map((n, i) => (
                    <li key={i}>{n}</li>
                  ))}
                </ol>
              )}
            </Card>
            <div className="space-y-3">
              <ScreeningIndexCaveat />
              <CaveatBanner tone="blue" title="Snapshot data">
                Every figure on this page comes from the last pipeline run. Nothing is real-time.
              </CaveatBanner>
            </div>
          </div>
        </>
      )}
    </PageBody>
  );
}
