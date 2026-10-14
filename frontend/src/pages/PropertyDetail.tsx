import { Bar, BarChart, CartesianGrid, Cell, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { Link, useParams } from 'react-router-dom';

import { useProperty } from '../api/hooks';
import type { EnvironmentOut, HazardAssessmentOut, PropertyDetailOut, ShapEntry } from '../api/types';
import { AXIS_LINE, AXIS_TICK, CURSOR_FILL, GRID_STROKE, TOOLTIP_ITEM_STYLE, TOOLTIP_LABEL_STYLE, TOOLTIP_STYLE } from '../components/charts/chartTheme';
import { FinancialScenariosTable } from '../components/finance/FinancialScenariosTable';
import { PageBody } from '../components/layout/PageBody';
import { PropertyMiniMap } from '../components/map/PropertyMiniMap';
import { AssumptionKindBadge, Badge, ConfidenceBadge, CoverageBadge, HazardClassBadge, ScoreBadge } from '../components/ui/Badge';
import { Card } from '../components/ui/Card';
import { CaveatBanner, CaveatList, ScreeningIndexCaveat } from '../components/ui/CaveatBanner';
import { DataTable, type Column } from '../components/ui/DataTable';
import { EmptyState } from '../components/ui/EmptyState';
import { ErrorState } from '../components/ui/ErrorState';
import { KeyValueList, type KeyValue } from '../components/ui/KeyValueList';
import { LoadingState } from '../components/ui/LoadingState';
import { MiniBar } from '../components/ui/ProgressBar';
import { PageHeader } from '../components/ui/PageHeader';
import { fmtAny, fmtCoord, fmtDateTime, fmtFracPct, fmtInt, fmtNum, fmtScore, fmtUsd, humanize, isNum } from '../lib/format';
import { COLORS, scoreColor } from '../lib/hazardColors';

const hazardColumns: Column<HazardAssessmentOut>[] = [
  {
    key: 'hazard_name',
    header: 'Hazard',
    render: (h) => (
      <div>
        <div className="text-slate-100">{h.hazard_name}</div>
        <div className="font-mono text-[11px] text-slate-500">{h.hazard_id}</div>
      </div>
    ),
  },
  { key: 'classification', header: 'Class', value: (h) => h.classification ?? null, render: (h) => <HazardClassBadge hazardId={h.hazard_id} value={h.classification} /> },
  {
    key: 'value',
    header: 'Value',
    align: 'right',
    value: (h) => h.value,
    render: (h) => (isNum(h.value) ? `${fmtNum(h.value, 3)}${h.units ? ` ${h.units}` : ''}` : '—'),
  },
  { key: 'probability_horizon', header: 'Probability / horizon', value: (h) => h.probability_horizon ?? null },
  { key: 'coverage_status', header: 'Coverage', render: (h) => <CoverageBadge status={h.coverage_status} /> },
  { key: 'confidence', header: 'Confidence', value: (h) => h.confidence ?? null, render: (h) => <ConfidenceBadge value={h.confidence} /> },
  { key: 'method', header: 'Method', value: (h) => h.method ?? null, className: 'text-xs text-slate-400' },
  {
    key: 'source_id',
    header: 'Source',
    render: (h) => (
      <div className="text-xs">
        <div className="font-mono text-slate-300">{h.source_id}</div>
        {h.source_version && <div className="text-slate-500">{h.source_version}</div>}
        {h.assessed_at && <div className="text-slate-500">{fmtDateTime(h.assessed_at)}</div>}
      </div>
    ),
  },
];

function ShapWaterfall({ shap }: { shap: ShapEntry[] }) {
  const rows = [...shap]
    .filter((s) => isNum(s.contribution))
    .sort((a, b) => Math.abs(b.contribution) - Math.abs(a.contribution))
    .slice(0, 15)
    .map((s) => ({ feature: s.feature, contribution: s.contribution, value: s.value ?? null }));

  if (rows.length === 0) {
    return <EmptyState compact title="No SHAP contributions" message="The prediction did not include per-feature contributions." />;
  }

  return (
    <ResponsiveContainer width="100%" height={Math.max(180, rows.length * 26 + 40)}>
      <BarChart data={rows} layout="vertical" margin={{ top: 4, right: 16, bottom: 4, left: 4 }}>
        <CartesianGrid stroke={GRID_STROKE} horizontal={false} />
        <XAxis type="number" tick={AXIS_TICK} axisLine={AXIS_LINE} tickLine={false} tickFormatter={(v) => Number(v).toFixed(2)} />
        <YAxis type="category" dataKey="feature" width={170} tick={AXIS_TICK} axisLine={AXIS_LINE} tickLine={false} />
        <ReferenceLine x={0} stroke="#64748b" />
        <Tooltip
          cursor={CURSOR_FILL}
          contentStyle={TOOLTIP_STYLE}
          labelStyle={TOOLTIP_LABEL_STYLE}
          itemStyle={TOOLTIP_ITEM_STYLE}
          formatter={(value, _name, item) => {
            const featureValue = item && item.payload ? fmtAny((item.payload as { value?: unknown }).value) : '—';
            return [`${Number(value).toFixed(4)} (feature value ${featureValue})`, 'SHAP contribution'];
          }}
        />
        <Bar dataKey="contribution" isAnimationActive={false} radius={2}>
          {rows.map((r) => (
            <Cell key={r.feature} fill={r.contribution >= 0 ? COLORS.red : COLORS.teal} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}

function environmentItems(env: EnvironmentOut): KeyValue[] {
  const items: KeyValue[] = [];
  const push = (key: string, label: string, v: number | string | null | undefined, fmt: (x: number) => string) => {
    if (v === null || v === undefined) return;
    items.push({ key, label, value: typeof v === 'number' ? fmt(v) : v });
  };
  push('elevation_m', 'Elevation', env.elevation_m, (x) => `${fmtNum(x, 1)} m`);
  push('slope_deg', 'Slope', env.slope_deg, (x) => `${fmtNum(x, 1)}°`);
  push('aspect_deg', 'Aspect', env.aspect_deg, (x) => `${fmtNum(x, 0)}°`);
  push('land_cover_class', 'Land cover', env.land_cover_class, String);
  push('fuel_model', 'Fuel model', env.fuel_model, String);
  push('distance_to_fault_m', 'Distance to fault', env.distance_to_fault_m, (x) => `${fmtInt(x)} m`);
  push('distance_to_watercourse_m', 'Distance to watercourse', env.distance_to_watercourse_m, (x) => `${fmtInt(x)} m`);
  push('fires_within_5km_since_1990', 'Fires within 5 km since 1990', env.fires_within_5km_since_1990, (x) => fmtInt(x));
  push('years_since_last_fire_within_1km', 'Years since last fire (1 km)', env.years_since_last_fire_within_1km, (x) => fmtNum(x, 1));
  return items;
}

function CompositeScoreCard({ detail }: { detail: PropertyDetailOut }) {
  const score = detail.score;
  if (!score) {
    return (
      <Card title="Composite screening index" subtitle="0-100 relative index, not a probability">
        <EmptyState compact title="No composite score" message="No score was computed for this property (hazard assessments may be missing)." />
      </Card>
    );
  }
  const contributions = score.contributions
    .map((c) => {
      const name = String(c.hazard_id ?? c.hazard ?? c['name'] ?? 'unknown');
      // The composite returns weighted_points / family_weight; older shapes used
      // contribution / weight.
      const weight = isNum(c.weight) ? c.weight : isNum(c.family_weight) ? c.family_weight : null;
      const value = isNum(c.contribution)
        ? c.contribution
        : isNum(c.weighted_points)
          ? c.weighted_points
          : weight !== null && isNum(c.normalised)
            ? weight * c.normalised
            : null;
      return { name, value, weight };
    })
    .sort((a, b) => (b.value ?? -Infinity) - (a.value ?? -Infinity));
  const maxAbs = Math.max(1e-9, ...contributions.map((c) => Math.abs(c.value ?? 0)));

  return (
    <Card title="Composite screening index" subtitle={`Method ${score.method_version} · 0-100 relative index, not a probability`}>
      <div className="flex items-end gap-4">
        <div className="tabular text-5xl font-semibold" style={{ color: scoreColor(score.score) }}>
          {fmtScore(score.score)}
        </div>
        <div className="pb-1">
          <ScoreBadge score={score.score} label={score.label} />
          <div className="mt-1 text-xs text-slate-500">Screening index (0-100)</div>
        </div>
      </div>

      <div className="mt-4 space-y-2">
        <div className="text-xs font-semibold uppercase tracking-wide text-slate-400">Contributions</div>
        {contributions.length === 0 ? (
          <div className="text-xs text-slate-500">No contribution breakdown returned.</div>
        ) : (
          contributions.map((c) => (
            <div key={c.name}>
              <div className="flex items-center justify-between text-xs">
                <span className="text-slate-300">{c.name}</span>
                <span className="tabular text-slate-400">
                  {c.value === null ? '—' : fmtNum(c.value, 2)}
                  {c.weight !== null ? ` · w ${fmtNum(c.weight, 2)}` : ''}
                </span>
              </div>
              <MiniBar fraction={(c.value ?? 0) / maxAbs} color={COLORS.teal} className="mt-1" />
            </div>
          ))
        )}
      </div>

      {score.missing_hazards.length > 0 && (
        <div className="mt-4">
          <div className="text-xs font-semibold uppercase tracking-wide text-slate-400">Missing hazards</div>
          <div className="mt-1 flex flex-wrap gap-1">
            {score.missing_hazards.map((h) => (
              <Badge key={h} tone="amber">
                {h}
              </Badge>
            ))}
          </div>
        </div>
      )}

      <p className="mt-4 text-xs text-slate-400">{score.weights_note}</p>
      <ScreeningIndexCaveat className="mt-3" />
    </Card>
  );
}

function PredictionCard({ detail }: { detail: PropertyDetailOut }) {
  const p = detail.prediction;
  if (!p) {
    return (
      <Card title="Wildfire prediction" subtitle="ML model output">
        <EmptyState
          compact
          title="No prediction available"
          message="The wildfire model has not been trained or this property lacks the required features."
        />
      </Card>
    );
  }
  const above = isNum(p.probability) && isNum(p.threshold) ? p.probability >= p.threshold : p.predicted_class ?? null;
  const featureItems: KeyValue[] = Object.entries(p.features).map(([k, v]) => ({
    key: k,
    label: humanize(k),
    value: isNum(v) ? fmtNum(v, Number.isInteger(v) ? 0 : 3) : '—',
  }));

  return (
    <Card title="Wildfire prediction" subtitle={`${p.model_version}${p.prediction_year ? ` · prediction year ${p.prediction_year}` : ''}`}>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <div>
          <div className="text-[11px] uppercase tracking-wide text-slate-500">Probability</div>
          <div className="tabular text-2xl font-semibold text-slate-100">{fmtFracPct(p.probability)}</div>
        </div>
        <div>
          <div className="text-[11px] uppercase tracking-wide text-slate-500">Threshold</div>
          <div className="tabular text-2xl font-semibold text-slate-300">{fmtFracPct(p.threshold)}</div>
        </div>
        <div>
          <div className="text-[11px] uppercase tracking-wide text-slate-500">Class</div>
          <div className="mt-1">
            {above === null ? <Badge>Unknown</Badge> : above ? <Badge tone="red">Above threshold</Badge> : <Badge tone="teal">Below threshold</Badge>}
          </div>
        </div>
        <div>
          <div className="text-[11px] uppercase tracking-wide text-slate-500">Base value</div>
          <div className="tabular text-2xl font-semibold text-slate-300">{isNum(p.base_value) ? fmtNum(p.base_value, 3) : '—'}</div>
        </div>
      </div>
      <p className="mt-3 text-xs text-slate-400">Target: {p.target_definition}</p>

      <div className="mt-4">
        <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-400">SHAP contributions (top 15)</div>
        <ShapWaterfall shap={p.shap} />
        <div className="mt-1 text-[11px] text-slate-500">Red pushes the probability up, teal pushes it down, relative to the base value.</div>
      </div>

      {featureItems.length > 0 && (
        <details className="mt-4">
          <summary className="cursor-pointer text-xs font-semibold uppercase tracking-wide text-slate-400">
            Model features ({featureItems.length})
          </summary>
          <KeyValueList items={featureItems} columns={2} className="mt-2" />
        </details>
      )}

      <PredictionQuality p={p} />

      {p.caveats.length > 0 && (
        <CaveatBanner title="Model caveats" className="mt-4">
          <CaveatList items={p.caveats} />
        </CaveatBanner>
      )}
    </Card>
  );
}

function PredictionQuality({ p }: { p: NonNullable<PropertyDetailOut['prediction']> }) {
  const completeness = p.feature_completeness ?? null;
  const tone = p.quality === 'complete' ? 'teal' : p.quality === 'partial' ? 'amber' : p.quality === 'sparse' ? 'red' : 'neutral';
  const fresh = p.data_freshness ?? {};
  const freshItems: KeyValue[] = [
    { key: 'fy', label: 'Fire history through', value: fresh.fire_history_through ?? '—' },
    { key: 'cy', label: 'Climate year', value: fresh.climate_year ?? '—' },
    { key: 'lc', label: 'Land cover epoch', value: fresh.land_cover_epoch ?? '—' },
    { key: 'fu', label: 'Fuel edition', value: fresh.fuel_edition_year ?? '—' },
  ];
  return (
    <div className="mt-4 rounded-md border border-slate-700 bg-slate-800/40 p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="text-xs font-semibold uppercase tracking-wide text-slate-400">Prediction inputs</div>
        <div className="flex items-center gap-2 text-xs">
          <span className="text-slate-400">Feature completeness</span>
          <span className="tabular text-slate-100">{completeness === null ? '—' : fmtFracPct(completeness)}</span>
          <Badge tone={tone}>{p.quality ?? 'unknown'}</Badge>
        </div>
      </div>
      <div className="mt-2 grid grid-cols-1 gap-3 md:grid-cols-2">
        <div>
          <div className="text-[11px] uppercase tracking-wide text-slate-500">Data freshness</div>
          <KeyValueList items={freshItems} columns={2} className="mt-1" />
        </div>
        <div>
          <div className="text-[11px] uppercase tracking-wide text-slate-500">Top contributing factors</div>
          {p.top_factors && p.top_factors.length > 0 ? (
            <ul className="mt-1 space-y-0.5 text-xs">
              {p.top_factors.map((f) => (
                <li key={f.feature} className="flex justify-between gap-2">
                  <span className="text-slate-300">{humanize(f.feature)}</span>
                  <span className={`tabular ${f.contribution > 0 ? 'text-red-300' : 'text-teal-300'}`}>
                    {f.contribution > 0 ? '+' : ''}
                    {fmtNum(f.contribution, 3)}
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <div className="mt-1 text-xs text-slate-500">No per-feature contributions for this model.</div>
          )}
        </div>
      </div>
      {p.coverage_warnings && p.coverage_warnings.length > 0 && (
        <ul className="mt-2 list-disc space-y-0.5 pl-4 text-xs text-amber-300">
          {p.coverage_warnings.map((w) => (
            <li key={w}>{w}</li>
          ))}
        </ul>
      )}
      {p.missing_features && p.missing_features.length > 0 && (
        <div className="mt-1 text-[11px] text-slate-500">Missing: {p.missing_features.map(humanize).join(', ')}</div>
      )}
      {p.explanation && <p className="mt-2 text-[11px] leading-snug text-slate-500">{p.explanation}</p>}
    </div>
  );
}

export function PropertyDetail() {
  const { id } = useParams<{ id: string }>();
  const q = useProperty(id);

  if (q.isLoading) {
    return (
      <PageBody>
        <LoadingState label={`Loading property #${id ?? ''}…`} />
      </PageBody>
    );
  }
  if (q.isError) {
    return (
      <PageBody>
        <PageHeader title={`Property #${id ?? ''}`} />
        <ErrorState error={q.error} onRetry={() => void q.refetch()} />
        <Link to="/properties" className="mt-4 inline-block text-sm text-teal-300 hover:underline">
          ← Back to explorer
        </Link>
      </PageBody>
    );
  }
  const d = q.data;
  if (!d) return null;

  const title = d.address ?? `Property #${d.id}`;
  const locality = [d.city, d.postcode, d.county ? `${d.county} County` : null].filter(Boolean).join(' · ');
  const hazardCaveats = d.hazards.flatMap((h) => h.caveats.map((c) => `${h.hazard_name}: ${c}`));

  const locationItems: KeyValue[] = [
    { key: 'lat', label: 'Latitude', value: fmtCoord(d.lat) },
    { key: 'lon', label: 'Longitude', value: fmtCoord(d.lon) },
    { key: 'county', label: 'County', value: d.county ?? '—' },
    { key: 'fips', label: 'County FIPS', value: d.county_fips ?? '—' },
    { key: 'source', label: 'Source', value: d.source_id },
    { key: 'external_id', label: 'External id', value: d.external_id ?? '—' },
    { key: 'acquired', label: 'Acquired', value: fmtDateTime(d.acquired_at) },
  ];
  const buildingItems: KeyValue[] = d.building
    ? [
        { key: 'building_id', label: 'Building id', value: d.building.building_id ?? '—' },
        { key: 'footprint', label: 'Footprint area', value: isNum(d.building.footprint_area_m2) ? `${fmtNum(d.building.footprint_area_m2, 0)} m²` : '—' },
        { key: 'match_method', label: 'Match method', value: d.building.match_method ?? '—' },
        { key: 'match_distance', label: 'Match distance', value: isNum(d.building.match_distance_m) ? `${fmtNum(d.building.match_distance_m, 1)} m` : '—' },
        { key: 'within_100m', label: 'Buildings within 100 m', value: fmtInt(d.building.buildings_within_100m) },
        { key: 'uncertainty', label: 'Uncertainty', value: d.building.uncertainty ?? '—' },
      ]
    : [];
  const st = d.building?.structure ?? null;
  const structureItems: KeyValue[] = st
    ? [
        { key: 'hazus_type', label: 'Hazus building type', value: st.hazus_type ?? '—' },
        { key: 'bldgtype', label: 'Material (NSI)', value: st.bldgtype ?? '—' },
        { key: 'occtype', label: 'Occupancy', value: st.occtype ?? '—' },
        { key: 'storeys', label: 'Storeys', value: fmtInt(st.num_story) },
        { key: 'year_built', label: 'Year built (median)', value: st.year_built ?? '—' },
        { key: 'found', label: 'Foundation / height', value: `${st.found_type ?? '—'}${isNum(st.found_ht_ft) ? ` / ${fmtNum(st.found_ht_ft, 1)} ft` : ''}` },
        { key: 'sqft', label: 'Floor area', value: isNum(st.sqft) ? `${fmtNum(st.sqft, 0)} sq ft` : '—' },
        { key: 'val_struct', label: 'Structure value (NSI, modelled)', value: fmtUsd(st.val_struct_usd) },
        { key: 'st_match', label: 'Match distance / uncertainty', value: `${isNum(st.match_distance_m) ? `${fmtNum(st.match_distance_m, 1)} m` : '—'} / ${st.uncertainty ?? '—'}` },
      ]
    : [];
  const envItems = d.environment ? environmentItems(d.environment) : [];
  const coverageEntries = Object.entries(d.coverage);

  return (
    <PageBody>
      <PageHeader
        title={title}
        description={locality || undefined}
        meta={`Property #${d.id}`}
        actions={
          <Link to="/properties" className="btn-secondary text-xs">
            ← Explorer
          </Link>
        }
      />

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <Card title="Location" subtitle="Geocoded point and building match" flush className="xl:col-span-1">
          <PropertyMiniMap lat={d.lat} lon={d.lon} color={scoreColor(d.composite_score)} className="h-56 w-full" />
          <div className="p-4">
            <KeyValueList items={locationItems} />
            {buildingItems.length > 0 && (
              <>
                <div className="mb-1 mt-4 text-xs font-semibold uppercase tracking-wide text-slate-400">Building match</div>
                <KeyValueList items={buildingItems} />
              </>
            )}
            {structureItems.length > 0 && (
              <>
                <div className="mb-1 mt-4 text-xs font-semibold uppercase tracking-wide text-slate-400">Structure attributes (USACE NSI)</div>
                <KeyValueList items={structureItems} />
                <p className="mt-2 text-xs text-slate-500">Modelled national-inventory attributes, not a survey. They drive the Hazus damage functions in the financial scenarios below.</p>
              </>
            )}
          </div>
        </Card>

        <div className="xl:col-span-2">
          <CompositeScoreCard detail={d} />
        </div>
      </div>

      <Card title="Hazard exposure profile" subtitle="One row per hazard assessment" className="mt-4" flush>
        {d.hazards.length === 0 ? (
          <div className="p-4">
            <EmptyState compact title="No hazard assessments" message="No hazards have been assessed for this property yet." />
          </div>
        ) : (
          <DataTable columns={hazardColumns} rows={d.hazards} rowKey={(h) => h.hazard_id} dense />
        )}
        {hazardCaveats.length > 0 && (
          <div className="border-t border-slate-800 p-4">
            <CaveatList items={hazardCaveats} />
          </div>
        )}
      </Card>

      <div className="mt-4 grid grid-cols-1 gap-4 xl:grid-cols-3">
        <div className="xl:col-span-2">
          <PredictionCard detail={d} />
        </div>
        <div className="space-y-4">
          <Card title="Environment features" subtitle="Terrain, land cover and history">
            {envItems.length === 0 ? (
              <EmptyState compact title="No environment features" message="No environmental attributes were sampled for this property." />
            ) : (
              <>
                <KeyValueList items={envItems} />
                {d.environment && Object.keys(d.environment.sources).length > 0 && (
                  <div className="mt-3 text-[11px] text-slate-500">
                    Sources:{' '}
                    {Object.entries(d.environment.sources)
                      .map(([k, v]) => `${k} → ${v}`)
                      .join('; ')}
                  </div>
                )}
              </>
            )}
          </Card>
          <Card title="Coverage" subtitle="Per hazard data coverage at this location">
            {coverageEntries.length === 0 ? (
              <div className="text-xs text-slate-500">No coverage information.</div>
            ) : (
              <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                {coverageEntries.map(([hazard, status]) => (
                  <div key={hazard} className="flex items-center justify-between gap-2 rounded border border-slate-800 px-2 py-1.5 text-xs">
                    <span className="truncate font-mono text-slate-300">{hazard}</span>
                    <CoverageBadge status={status} />
                  </div>
                ))}
              </div>
            )}
          </Card>
        </div>
      </div>

      {d.financial && (
        <Card
          title="Financial scenarios"
          subtitle={
            <span>
              Exposed value {fmtUsd(d.financial.exposed_value)} <AssumptionKindBadge kind={d.financial.exposed_value_kind} />
              {d.financial.expected_annual_loss_available ? ' · expected annual loss available' : ' · no expected annual loss (scenario losses only)'}
            </span>
          }
          className="mt-4"
          flush
        >
          <FinancialScenariosTable scenarios={d.financial.scenarios} />
          <div className="border-t border-slate-800 p-4">
            <CaveatBanner title="Scenario note">{d.financial.note}</CaveatBanner>
          </div>
        </Card>
      )}
    </PageBody>
  );
}
