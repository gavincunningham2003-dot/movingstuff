import { useMemo, useState } from 'react';
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';

import { useModelVersions, useModelsMetrics } from '../api/hooks';
import type { GeographicPerformanceEntry, ModelMetricsOut } from '../api/types';
import { AXIS_LINE, AXIS_TICK, CURSOR_FILL, GRID_STROKE, TOOLTIP_ITEM_STYLE, TOOLTIP_LABEL_STYLE, TOOLTIP_STYLE } from '../components/charts/chartTheme';
import { OperatingPointPanel } from '../components/charts/OperatingPointPanel';
import { PageBody } from '../components/layout/PageBody';
import { Badge } from '../components/ui/Badge';
import { Card } from '../components/ui/Card';
import { CaveatBanner } from '../components/ui/CaveatBanner';
import { DataTable, type Column } from '../components/ui/DataTable';
import { EmptyState } from '../components/ui/EmptyState';
import { ErrorState } from '../components/ui/ErrorState';
import { Field, Select } from '../components/ui/Inputs';
import { LoadingState } from '../components/ui/LoadingState';
import { PageHeader } from '../components/ui/PageHeader';
import { StatTile } from '../components/ui/StatTile';
import { gridResample } from '../lib/curves';
import { fmtDateTime, fmtFracPct, fmtInt, fmtNum, humanize, isNum } from '../lib/format';
import { COLORS, seriesColor } from '../lib/hazardColors';
import {
  calibrationBins,
  extraMetricKeys,
  featureImportance,
  geographicPerformance,
  METRIC_LABELS,
  metricValue,
  prPoints,
  rocPoints,
  shapSummary,
} from '../lib/modelData';

function seriesName(m: ModelMetricsOut, all: ModelMetricsOut[]): string {
  const dup = all.filter((x) => x.name === m.name).length > 1;
  return dup ? `${m.name} ${m.version}` : m.name;
}

function CurveChart({
  models,
  kind,
  inspected,
}: {
  models: ModelMetricsOut[];
  kind: 'roc' | 'pr';
  inspected: ModelMetricsOut | undefined;
}) {
  const data = useMemo(
    () =>
      gridResample(
        models.map((m) => ({
          name: seriesName(m, models),
          points:
            kind === 'roc'
              ? rocPoints(m).map((p) => ({ x: p.fpr, y: p.tpr }))
              : prPoints(m).map((p) => ({ x: p.recall, y: p.precision })),
        })),
        100,
      ),
    [models, kind],
  );
  const hasAny = models.some((m) => (kind === 'roc' ? m.roc_curve.length : m.pr_curve.length) > 0);
  if (!hasAny) {
    return <EmptyState compact title={`No ${kind === 'roc' ? 'ROC' : 'PR'} curve`} message="The API returned no curve points." />;
  }
  return (
    <ResponsiveContainer width="100%" height={280}>
      <LineChart data={data} margin={{ top: 8, right: 16, bottom: 8, left: 0 }}>
        <CartesianGrid stroke={GRID_STROKE} />
        <XAxis
          dataKey="x"
          type="number"
          domain={[0, 1]}
          tick={AXIS_TICK}
          axisLine={AXIS_LINE}
          tickLine={false}
          label={{ value: kind === 'roc' ? 'False positive rate' : 'Recall', position: 'insideBottom', offset: -4, fill: '#94a3b8', fontSize: 11 }}
        />
        <YAxis
          domain={[0, 1]}
          tick={AXIS_TICK}
          axisLine={AXIS_LINE}
          tickLine={false}
          label={{ value: kind === 'roc' ? 'True positive rate' : 'Precision', angle: -90, position: 'insideLeft', fill: '#94a3b8', fontSize: 11 }}
        />
        <Tooltip
          contentStyle={TOOLTIP_STYLE}
          labelStyle={TOOLTIP_LABEL_STYLE}
          itemStyle={TOOLTIP_ITEM_STYLE}
          labelFormatter={(label) => `${kind === 'roc' ? 'FPR' : 'Recall'} ${Number(label).toFixed(2)}`}
          formatter={(value) => Number(value).toFixed(3)}
        />
        <Legend wrapperStyle={{ fontSize: 11 }} />
        {kind === 'roc' ? (
          <ReferenceLine segment={[{ x: 0, y: 0 }, { x: 1, y: 1 }]} stroke="#475569" strokeDasharray="4 4" />
        ) : (
          inspected && (
            <ReferenceLine y={inspected.prevalence_test} stroke="#475569" strokeDasharray="4 4" label={{ value: 'prevalence', fill: '#64748b', fontSize: 10, position: 'right' }} />
          )
        )}
        {models.map((m, i) => {
          const name = seriesName(m, models);
          return (
            <Line
              key={name}
              type="monotone"
              dataKey={name}
              stroke={seriesColor(i)}
              strokeWidth={m.is_selected ? 2.5 : 1.5}
              dot={false}
              isAnimationActive={false}
              connectNulls
            />
          );
        })}
      </LineChart>
    </ResponsiveContainer>
  );
}

function CalibrationChart({ model }: { model: ModelMetricsOut }) {
  const bins = calibrationBins(model);
  if (bins.length === 0) {
    return <EmptyState compact title="No calibration bins" message="The API returned no reliability data for this model." />;
  }
  return (
    <ResponsiveContainer width="100%" height={280}>
      <LineChart data={bins} margin={{ top: 8, right: 16, bottom: 8, left: 0 }}>
        <CartesianGrid stroke={GRID_STROKE} />
        <XAxis
          dataKey="predicted"
          type="number"
          domain={[0, 1]}
          tick={AXIS_TICK}
          axisLine={AXIS_LINE}
          tickLine={false}
          label={{ value: 'Mean predicted probability', position: 'insideBottom', offset: -4, fill: '#94a3b8', fontSize: 11 }}
        />
        <YAxis
          domain={[0, 1]}
          tick={AXIS_TICK}
          axisLine={AXIS_LINE}
          tickLine={false}
          label={{ value: 'Observed fraction', angle: -90, position: 'insideLeft', fill: '#94a3b8', fontSize: 11 }}
        />
        <Tooltip
          contentStyle={TOOLTIP_STYLE}
          labelStyle={TOOLTIP_LABEL_STYLE}
          itemStyle={TOOLTIP_ITEM_STYLE}
          labelFormatter={(label) => `Predicted ${Number(label).toFixed(3)}`}
          formatter={(value, _name, item) => {
            const n = item && item.payload ? (item.payload as { n?: number | null }).n : null;
            return [`${Number(value).toFixed(3)}${isNum(n) ? ` (n=${fmtInt(n)})` : ''}`, 'Observed'];
          }}
        />
        <ReferenceLine segment={[{ x: 0, y: 0 }, { x: 1, y: 1 }]} stroke="#475569" strokeDasharray="4 4" />
        <Line type="monotone" dataKey="observed" stroke={COLORS.teal} strokeWidth={2} dot={{ r: 3, fill: COLORS.teal }} isAnimationActive={false} />
      </LineChart>
    </ResponsiveContainer>
  );
}

function HorizontalBars({
  data,
  dataKey,
  color,
  label,
}: {
  data: Record<string, unknown>[];
  dataKey: string;
  color: string;
  label: string;
}) {
  if (data.length === 0) {
    return <EmptyState compact title={`No ${label.toLowerCase()}`} message="The API returned no entries." />;
  }
  return (
    <ResponsiveContainer width="100%" height={Math.max(180, data.length * 24 + 40)}>
      <BarChart data={data} layout="vertical" margin={{ top: 4, right: 16, bottom: 4, left: 4 }}>
        <CartesianGrid stroke={GRID_STROKE} horizontal={false} />
        <XAxis type="number" tick={AXIS_TICK} axisLine={AXIS_LINE} tickLine={false} tickFormatter={(v) => Number(v).toFixed(3)} />
        <YAxis type="category" dataKey="feature" width={170} tick={AXIS_TICK} axisLine={AXIS_LINE} tickLine={false} />
        <Tooltip
          cursor={CURSOR_FILL}
          contentStyle={TOOLTIP_STYLE}
          labelStyle={TOOLTIP_LABEL_STYLE}
          itemStyle={TOOLTIP_ITEM_STYLE}
          formatter={(value) => [Number(value).toFixed(4), label]}
        />
        <Bar dataKey={dataKey} fill={color} isAnimationActive={false} radius={[0, 2, 2, 0]} />
      </BarChart>
    </ResponsiveContainer>
  );
}

const geoColumns: Column<GeographicPerformanceEntry>[] = [
  { key: 'region', header: 'Region' },
  { key: 'n', header: 'n', align: 'right', render: (r) => fmtInt(r.n) },
  { key: 'prevalence', header: 'Prevalence', align: 'right', value: (r) => r.prevalence ?? null, render: (r) => fmtFracPct(r.prevalence) },
  { key: 'roc_auc', header: 'ROC-AUC', align: 'right', value: (r) => r.roc_auc ?? null, render: (r) => fmtNum(r.roc_auc, 3) },
  { key: 'pr_auc', header: 'PR-AUC', align: 'right', value: (r) => r.pr_auc ?? null, render: (r) => fmtNum(r.pr_auc, 3) },
];

export function ModelAnalytics() {
  const [version, setVersion] = useState<string>('');
  const versionsQ = useModelVersions();
  const q = useModelsMetrics(version || undefined);
  const [inspectName, setInspectName] = useState<string>('');
  const versions = versionsQ.data ?? [];
  const versionLabel = (v: (typeof versions)[number]) =>
    `${v.version}${v.is_latest ? ' (live)' : ''} · ${v.target === 'within_1km' ? 'within 1 km' : 'inside perimeter'}${
      v.excluded_features.length ? ' · no lag cols' : ''
    } · ${v.selected_model ?? '—'}`;

  const models = q.data?.models ?? [];
  const inspected =
    models.find((m) => seriesName(m, models) === inspectName) ??
    models.find((m) => m.is_selected) ??
    models.find((m) => m.name === q.data?.selected_model) ??
    models[0];

  const metricCols = useMemo(() => {
    const present = METRIC_LABELS.filter((m) => models.some((mod) => metricValue(mod.metrics, m.key, m.alt) !== null));
    const extras = extraMetricKeys(models).map((k) => ({ key: k, label: humanize(k), alt: [] as string[] }));
    return [...present, ...extras];
  }, [models]);

  const comparisonColumns = useMemo<Column<ModelMetricsOut>[]>(
    () => [
      {
        key: 'name',
        header: 'Model',
        render: (m) => (
          <div className="flex items-center gap-2">
            <span className="text-slate-100">{m.name}</span>
            {m.is_selected && <Badge tone="teal">selected</Badge>}
          </div>
        ),
      },
      { key: 'version', header: 'Version', className: 'font-mono text-xs text-slate-400' },
      { key: 'algorithm', header: 'Algorithm', className: 'text-xs' },
      { key: 'trained_at', header: 'Trained', value: (m) => m.trained_at, render: (m) => fmtDateTime(m.trained_at), className: 'text-xs' },
      { key: 'n_train', header: 'n train', align: 'right', render: (m) => fmtInt(m.n_train) },
      { key: 'n_test', header: 'n test', align: 'right', render: (m) => fmtInt(m.n_test) },
      { key: 'prevalence_test', header: 'Prev. test', align: 'right', render: (m) => fmtFracPct(m.prevalence_test) },
      ...metricCols.map<Column<ModelMetricsOut>>((mc) => ({
        key: `metric:${mc.key}`,
        header: mc.label,
        align: 'right',
        value: (m) => metricValue(m.metrics, mc.key, mc.alt),
        render: (m) => fmtNum(metricValue(m.metrics, mc.key, mc.alt), 3),
      })),
      { key: 'threshold', header: 'Threshold', align: 'right', render: (m) => fmtNum(m.threshold, 3) },
    ],
    [metricCols],
  );

  if (q.isLoading) {
    return (
      <PageBody>
        <PageHeader title="Model analytics" />
        <LoadingState label="Loading model metrics…" />
      </PageBody>
    );
  }
  if (q.isError) {
    return (
      <PageBody>
        <PageHeader title="Model analytics" />
        <ErrorState error={q.error} onRetry={() => void q.refetch()} />
      </PageBody>
    );
  }
  const data = q.data;
  if (!data) return null;

  const statusOk = data.status === 'available';

  return (
    <PageBody>
      <PageHeader
        title="Model analytics"
        description="Wildfire exposure classifier: temporal and spatial holdout performance, calibration, explainability and known limitations."
        meta={
          data.selected_model
            ? `Selected model: ${data.selected_model} · ${models.length} model${models.length === 1 ? '' : 's'} evaluated`
            : `${models.length} model${models.length === 1 ? '' : 's'} evaluated`
        }
        actions={
          <div className="flex flex-wrap items-end gap-3">
            {versions.length > 1 && (
              <Field label="Training run">
                <Select
                  value={version || (versions.find((v) => v.is_latest)?.version ?? '')}
                  onChange={setVersion}
                  options={versions.map((v) => ({ value: v.version, label: versionLabel(v) }))}
                  className="w-80"
                />
              </Field>
            )}
            {models.length > 1 && (
              <Field label="Inspect model">
                <Select
                  value={inspected ? seriesName(inspected, models) : ''}
                  onChange={setInspectName}
                  options={models.map((m) => ({ value: seriesName(m, models), label: seriesName(m, models) }))}
                  className="w-56"
                />
              </Field>
            )}
          </div>
        }
      />

      {data.is_latest === false && (
        <CaveatBanner tone="amber" title="Viewing a recorded training run that is not the live model" className="mb-4">
          Run {data.version}: target "{data.target === 'within_1km' ? 'perimeter within 1 km' : 'inside perimeter'}"
          {data.excluded_features?.length ? `, excluding ${data.excluded_features.join(', ')}` : ''}. The map, property
          pages and portfolio scoring use the live run.
        </CaveatBanner>
      )}

      {!statusOk && (
        <CaveatBanner
          tone={data.status === 'not_trained' ? 'red' : 'amber'}
          title={data.status === 'not_trained' ? 'Model not trained' : 'Insufficient data to train a reliable model'}
          className="mb-4"
        >
          {data.status_detail ??
            (data.status === 'not_trained'
              ? 'No model artefact is registered. Run the training pipeline to produce metrics.'
              : 'The available labelled data does not meet the minimum requirements for training and evaluation.')}
        </CaveatBanner>
      )}

      {models.length === 0 ? (
        <EmptyState
          title="No model metrics"
          message="The API returned no evaluated models. Train a model with the backend pipeline to populate this page."
        />
      ) : (
        <>
          {inspected && (
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
              <StatTile label="Training period" value={<span className="text-base">{inspected.train_period}</span>} hint={`n = ${fmtInt(inspected.n_train)} · prevalence ${fmtFracPct(inspected.prevalence_train)}`} />
              <StatTile label="Validation period" value={<span className="text-base">{inspected.validation_period}</span>} />
              <StatTile label="Test period" value={<span className="text-base">{inspected.test_period}</span>} hint={`n = ${fmtInt(inspected.n_test)} · prevalence ${fmtFracPct(inspected.prevalence_test)}`} />
              <StatTile
                label="Spatial holdout"
                value={<span className="text-base">{inspected.spatial_holdout ?? 'None'}</span>}
                hint={inspected.spatial_holdout ? 'Held-out region excluded from training' : 'No spatial holdout reported'}
                accent={inspected.spatial_holdout ? 'default' : 'amber'}
              />
            </div>
          )}

          <Card title="Model comparison" subtitle="All evaluated models; the selected model is highlighted" className="mt-4" flush>
            <DataTable
              columns={comparisonColumns}
              rows={models}
              rowKey={(m) => `${m.name}@${m.version}`}
              rowClassName={(m) => (m.is_selected ? 'bg-teal-500/5' : undefined)}
              dense
            />
          </Card>

          <div className="mt-4 grid grid-cols-1 gap-4 xl:grid-cols-2">
            <Card title="ROC curve" subtitle="All models; dashed diagonal is random">
              <CurveChart models={models} kind="roc" inspected={inspected} />
            </Card>
            <Card title="Precision-recall curve" subtitle="Dashed line marks test prevalence (random baseline)">
              <CurveChart models={models} kind="pr" inspected={inspected} />
            </Card>
          </div>

          {inspected && (
            <>
              <div className="mt-4 grid grid-cols-1 gap-4 xl:grid-cols-2">
                <Card title="Calibration (reliability)" subtitle={`${seriesName(inspected, models)} · dashed diagonal is perfect calibration`}>
                  <CalibrationChart model={inspected} />
                </Card>
                <Card
                  title="Confusion matrix at a chosen operating point"
                  subtitle={`${seriesName(inspected, models)} · thresholds chosen on validation, counts on the held-out split`}
                >
                  <OperatingPointPanel model={inspected} />
                </Card>
              </div>

              <div className="mt-4 grid grid-cols-1 gap-4 xl:grid-cols-2">
                <Card title="Feature importance" subtitle="Model-native importance (top 20)">
                  <HorizontalBars data={featureImportance(inspected).slice(0, 20)} dataKey="importance" color={COLORS.blue} label="Importance" />
                </Card>
                <Card title="SHAP summary" subtitle="Mean |SHAP| over the test period (top 20)">
                  <HorizontalBars data={shapSummary(inspected).slice(0, 20)} dataKey="mean_abs_shap" color={COLORS.teal} label="Mean |SHAP|" />
                </Card>
              </div>

              <Card title="Geographic performance" subtitle="Per-region discrimination; small n regions are unreliable" className="mt-4" flush>
                <DataTable
                  columns={geoColumns}
                  rows={geographicPerformance(inspected)}
                  rowKey={(r) => r.region}
                  initialSort={{ key: 'n', dir: 'desc' }}
                  dense
                  emptyMessage="No geographic breakdown returned."
                />
              </Card>
            </>
          )}
        </>
      )}

      <div className="mt-4 grid grid-cols-1 gap-4 xl:grid-cols-2">
        <Card title="Limitations" subtitle="As declared by the model registry">
          {data.limitations.length === 0 ? (
            <div className="text-sm text-slate-500">No limitations were returned.</div>
          ) : (
            <ul className="list-disc space-y-1.5 pl-5 text-sm text-slate-300">
              {data.limitations.map((l, i) => (
                <li key={i}>{l}</li>
              ))}
            </ul>
          )}
        </Card>
        <Card title="Feature list" subtitle={`${data.feature_list.length} input features`}>
          {data.feature_list.length === 0 ? (
            <div className="text-sm text-slate-500">No feature list was returned.</div>
          ) : (
            <div className="flex flex-wrap gap-1.5">
              {data.feature_list.map((f) => (
                <Badge key={f} className="font-mono">
                  {f}
                </Badge>
              ))}
            </div>
          )}
          {inspected && (
            <p className="mt-3 text-xs text-slate-500">
              Target: {inspected.target_definition}
            </p>
          )}
        </Card>
      </div>
    </PageBody>
  );
}
