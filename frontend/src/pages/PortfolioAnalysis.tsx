import 'maplibre-gl/dist/maplibre-gl.css';

import type { FeatureCollection, Point } from 'geojson';
import { parse, type ParseResult } from 'papaparse';
import { useMemo, useRef, useState, type ChangeEvent, type DragEvent } from 'react';
import MapGL, { Layer, NavigationControl, Popup, Source, type LayerProps, type MapLayerMouseEvent } from 'react-map-gl/maplibre';

import { apiLinks } from '../api/client';
import { useCsvContract, usePortfolioAnalysis, usePortfolios, usePortfolioStatus, useUploadPortfolio } from '../api/hooks';
import type {
  ConcentrationCellOut,
  CsvColumnContractOut,
  LocationAccumulationOut,
  PortfolioAnalysisOut,
  PortfolioFinancial,
  PortfolioScenarioTotal,
  PortfolioStatusOut,
  TopExposureOut,
  ValidationIssue,
} from '../api/types';
import { HazardDistributionPanel } from '../components/charts/HazardDistributionPanel';
import { StressTestPanel } from '../components/portfolio/StressTestPanel';
import { PageBody } from '../components/layout/PageBody';
import { DARK_BASEMAP } from '../components/map/basemap';
import { expr, pointColorExpression, pointLegend } from '../components/map/expressions';
import { countyColumns, countyRowKey } from '../components/tables/countyColumns';
import { Badge, JobStatusBadge } from '../components/ui/Badge';
import { Card } from '../components/ui/Card';
import { CaveatBanner, ScreeningIndexCaveat } from '../components/ui/CaveatBanner';
import { DataTable, type Column } from '../components/ui/DataTable';
import { EmptyState } from '../components/ui/EmptyState';
import { ErrorState } from '../components/ui/ErrorState';
import { Field, TextInput } from '../components/ui/Inputs';
import { LegendBox } from '../components/ui/LegendBox';
import { LoadingState } from '../components/ui/LoadingState';
import { PageHeader } from '../components/ui/PageHeader';
import { ProgressBar } from '../components/ui/ProgressBar';
import { StatTile } from '../components/ui/StatTile';
import { fmtAny, fmtCoord, fmtDateTime, fmtInt, fmtNum, fmtPct, fmtScore, fmtUsd, fmtUsdCompact, humanize, isNum } from '../lib/format';
import { COLORS } from '../lib/hazardColors';
import { PORTFOLIO_SUMMARY_LABELS } from '../lib/summaryStats';

// --------------------------------------------------------------------------- CSV preview helpers

type ColumnHint = CsvColumnContractOut;

/**
 * Fallback mirror of the server contract (GET /portfolio/csv-contract), used only until
 * the live contract loads. The server's table is authoritative: the parser and this
 * preview read the same aliases.
 */
const FALLBACK_COLUMN_HINTS: ColumnHint[] = [
  { name: 'property_id', required: false, description: 'Your identifier for the exposure; generated as row-<n> when missing.', aliases: ['property_id', 'propertyid', 'prop_id', 'id', 'location_id', 'loc_id', 'ref'] },
  { name: 'latitude', required: true, description: 'Decimal degrees, WGS84.', aliases: ['latitude', 'lat', 'y'] },
  { name: 'longitude', required: true, description: 'Decimal degrees, WGS84.', aliases: ['longitude', 'lon', 'lng', 'long', 'x'] },
  { name: 'address', required: false, description: 'Street address, used for display only.', aliases: ['address', 'street_address', 'street', 'addr'] },
  { name: 'property_value', required: false, description: 'Building replacement value, USD (basis of gross physical loss).', aliases: ['property_value', 'building_value', 'replacement_value', 'replacement_cost', 'rcv', 'value'] },
  { name: 'contents_value', required: false, description: 'Contents replacement value, USD (added to the building value).', aliases: ['contents_value', 'contents', 'contents_insured_value'] },
  { name: 'insured_value', required: false, description: 'Sum insured / total insured value, USD (caps net insured loss when no limit is given).', aliases: ['insured_value', 'tiv', 'total_insured_value', 'sum_insured', 'insured', 'si'] },
  { name: 'limit', required: false, description: 'Policy limit, USD (defaults to the sum insured).', aliases: ['limit', 'policy_limit', 'coverage_limit'] },
  { name: 'deductible', required: false, description: 'Policy deductible, USD.', aliases: ['deductible', 'ded', 'deductible_amount'] },
  { name: 'policy_id', required: false, description: 'Policy reference.', aliases: ['policy_id', 'policy', 'policy_number', 'policy_no', 'account_id'] },
];

/** Same normalisation as the server: "Policy Limit", "policy-limit" and "POLICY_LIMIT" all match `policy_limit`. */
function normaliseName(s: string): string {
  return s
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '');
}

function matchColumn(hint: ColumnHint, fields: string[]): string | null {
  const targets = [hint.name, ...hint.aliases].map(normaliseName);
  return fields.find((f) => targets.includes(normaliseName(f))) ?? null;
}

interface CsvPreview {
  fields: string[];
  rows: Record<string, string>[];
  errors: string[];
}

// --------------------------------------------------------------------------- summary tiles

function formatSummaryValue(key: string, v: unknown): string {
  if (!isNum(v)) return fmtAny(v);
  if (/pct|percent|share/.test(key)) return fmtPct(v);
  if (/value|tiv|loss|premium/.test(key)) return fmtUsdCompact(v);
  if (/score/.test(key)) return fmtScore(v);
  if (Number.isInteger(v)) return fmtInt(v);
  return fmtNum(v, 2);
}

function summaryTiles(summary: Record<string, unknown>): { key: string; label: string; value: string }[] {
  const scalar = Object.entries(summary).filter(
    ([, v]) => v === null || typeof v === 'number' || typeof v === 'string' || typeof v === 'boolean',
  );
  const known = Object.keys(PORTFOLIO_SUMMARY_LABELS)
    .filter((k) => scalar.some(([key]) => key === k))
    .map((k): [string, unknown] => [k, summary[k]]);
  const rest = scalar.filter(([k]) => !(k in PORTFOLIO_SUMMARY_LABELS));
  return [...known, ...rest].map(([k, v]) => ({
    key: k,
    label: PORTFOLIO_SUMMARY_LABELS[k] ?? humanize(k),
    value: formatSummaryValue(k, v),
  }));
}

// --------------------------------------------------------------------------- map

interface ExposureProps {
  kind: 'exposure';
  property_id: string;
  county: string | null;
  insured_value: number | null;
  composite_score: number | null;
}

interface CellProps {
  kind: 'cell';
  cell_id: string;
  n_properties: number;
  total_insured_value: number | null;
  share_of_tiv_pct: number | null;
  dominant_hazard: string | null;
}

type PortfolioFeatureProps = ExposureProps | CellProps;

interface MapSelection {
  lon: number;
  lat: number;
  props: PortfolioFeatureProps;
}

const exposureLayer: LayerProps = {
  id: 'pf-exposures',
  type: 'circle',
  paint: {
    'circle-color': pointColorExpression('composite_score'),
    'circle-radius': 5,
    'circle-stroke-color': '#020617',
    'circle-stroke-width': 1,
  },
};

function cellLayer(maxN: number): LayerProps {
  return {
    id: 'pf-cells',
    type: 'circle',
    paint: {
      'circle-color': COLORS.amber,
      'circle-opacity': 0.22,
      'circle-stroke-color': COLORS.amber,
      'circle-stroke-width': 1.5,
      'circle-radius': maxN > 1 ? expr('interpolate', expr('linear'), expr('get', 'n_properties'), 1, 8, maxN, 32) : 10,
    },
  };
}

function PortfolioMap({ analysis }: { analysis: PortfolioAnalysisOut }) {
  const [selected, setSelected] = useState<MapSelection | null>(null);

  const exposures = useMemo<FeatureCollection<Point, ExposureProps>>(
    () => ({
      type: 'FeatureCollection',
      features: analysis.top_exposures
        .filter((e) => Number.isFinite(e.lon) && Number.isFinite(e.lat))
        .map((e) => ({
          type: 'Feature',
          geometry: { type: 'Point', coordinates: [e.lon, e.lat] },
          properties: {
            kind: 'exposure',
            property_id: e.property_id,
            county: e.county,
            insured_value: e.insured_value,
            composite_score: e.composite_score,
          },
        })),
    }),
    [analysis.top_exposures],
  );

  const cells = useMemo<FeatureCollection<Point, CellProps>>(
    () => ({
      type: 'FeatureCollection',
      features: analysis.concentration
        .filter((c) => Number.isFinite(c.centroid_lon) && Number.isFinite(c.centroid_lat))
        .map((c) => ({
          type: 'Feature',
          geometry: { type: 'Point', coordinates: [c.centroid_lon, c.centroid_lat] },
          properties: {
            kind: 'cell',
            cell_id: c.cell_id,
            n_properties: c.n_properties,
            total_insured_value: c.total_insured_value,
            share_of_tiv_pct: c.share_of_tiv_pct,
            dominant_hazard: c.dominant_hazard,
          },
        })),
    }),
    [analysis.concentration],
  );

  const maxN = Math.max(1, ...analysis.concentration.map((c) => c.n_properties));

  const bounds = useMemo<[[number, number], [number, number]] | null>(() => {
    const xs: number[] = [];
    const ys: number[] = [];
    for (const f of [...exposures.features, ...cells.features]) {
      xs.push(f.geometry.coordinates[0]);
      ys.push(f.geometry.coordinates[1]);
    }
    if (xs.length === 0) return null;
    let w = Math.min(...xs);
    let e = Math.max(...xs);
    let s = Math.min(...ys);
    let n = Math.max(...ys);
    if (e - w < 0.05) {
      w -= 0.05;
      e += 0.05;
    }
    if (n - s < 0.05) {
      s -= 0.05;
      n += 0.05;
    }
    return [
      [w, s],
      [e, n],
    ];
  }, [exposures, cells]);

  if (!bounds) {
    return (
      <EmptyState
        compact
        title="Nothing to map"
        message="The analysis returned no geocoded top exposures or concentration cells."
      />
    );
  }

  const onClick = (ev: MapLayerMouseEvent) => {
    const f = ev.features?.[0];
    if (!f) {
      setSelected(null);
      return;
    }
    const coords = (f.geometry as Point).coordinates;
    setSelected({ lon: coords[0], lat: coords[1], props: f.properties as PortfolioFeatureProps });
  };

  return (
    <div className="h-[420px] w-full">
      <MapGL
        initialViewState={{ bounds, fitBoundsOptions: { padding: 40, maxZoom: 12 } }}
        mapStyle={DARK_BASEMAP}
        style={{ width: '100%', height: '100%' }}
        interactiveLayerIds={['pf-exposures', 'pf-cells']}
        onClick={onClick}
      >
        <NavigationControl position="top-left" showCompass={false} />
        <Source id="pf-cells" type="geojson" data={cells}>
          <Layer {...cellLayer(maxN)} />
        </Source>
        <Source id="pf-exposures" type="geojson" data={exposures}>
          <Layer {...exposureLayer} />
        </Source>
        {selected && (
          <Popup longitude={selected.lon} latitude={selected.lat} anchor="bottom" onClose={() => setSelected(null)} closeOnClick={false}>
            <div className="min-w-[200px] p-3 text-xs text-slate-200">
              {selected.props.kind === 'exposure' ? (
                <>
                  <div className="text-sm font-semibold">Property {selected.props.property_id}</div>
                  <div className="text-slate-400">{selected.props.county ?? 'Unknown county'}</div>
                  <div className="mt-1">Insured value {fmtUsd(selected.props.insured_value)}</div>
                  <div>Screening index {fmtScore(selected.props.composite_score)}</div>
                </>
              ) : (
                <>
                  <div className="text-sm font-semibold">Concentration cell {selected.props.cell_id}</div>
                  <div className="mt-1">{fmtInt(selected.props.n_properties)} properties</div>
                  <div>TIV {fmtUsd(selected.props.total_insured_value)}</div>
                  <div>Share of TIV {fmtPct(selected.props.share_of_tiv_pct)}</div>
                  <div>Dominant hazard {selected.props.dominant_hazard ?? '—'}</div>
                </>
              )}
            </div>
          </Popup>
        )}
      </MapGL>
    </div>
  );
}

// --------------------------------------------------------------------------- tables

const accumulationColumns: Column<LocationAccumulationOut>[] = [
  {
    key: 'location',
    header: 'Location',
    sortable: false,
    render: (r) => (
      <span className="font-mono text-xs">
        {fmtCoord(r.lat)}, {fmtCoord(r.lon)}
      </span>
    ),
  },
  { key: 'n_exposures', header: 'Exposures', align: 'right', value: (r) => r.n_exposures, render: (r) => fmtInt(r.n_exposures) },
  {
    key: 'ids',
    header: 'Property ids · policies',
    sortable: false,
    render: (r) => (
      <span className="text-xs">
        <span className="font-mono text-slate-200">{r.property_ids.join(', ')}</span>
        {r.policy_ids.length > 0 && <span className="text-slate-500"> · {r.policy_ids.join(', ')}</span>}
      </span>
    ),
  },
  { key: 'total_insured_value', header: 'Sum insured', align: 'right', value: (r) => r.total_insured_value, render: (r) => fmtUsd(r.total_insured_value) },
  { key: 'total_replacement_value', header: 'Replacement value', align: 'right', value: (r) => r.total_replacement_value, render: (r) => fmtUsd(r.total_replacement_value) },
  { key: 'share_of_tiv_pct', header: '% of TIV', align: 'right', value: (r) => r.share_of_tiv_pct, render: (r) => fmtPct(r.share_of_tiv_pct) },
  { key: 'composite_score', header: 'Screening index', align: 'right', value: (r) => r.composite_score, render: (r) => fmtScore(r.composite_score) },
];

const scenarioTotalColumns: Column<PortfolioScenarioTotal>[] = [
  { key: 'scenario_id', header: 'Scenario', className: 'font-mono text-xs' },
  { key: 'hazard_id', header: 'Hazard', render: (r) => <Badge>{r.hazard_id}</Badge> },
  { key: 'description', header: 'Description', className: 'max-w-xs text-slate-300', sortable: false },
  { key: 'n_properties', header: 'Exposures', align: 'right', value: (r) => r.n_properties, render: (r) => fmtInt(r.n_properties) },
  { key: 'exposed_value', header: 'Exposed value', align: 'right', value: (r) => r.exposed_value, render: (r) => fmtUsd(r.exposed_value) },
  { key: 'gross_loss', header: 'Gross loss', align: 'right', value: (r) => r.gross_loss, render: (r) => <span className="font-semibold text-amber-200">{fmtUsd(r.gross_loss)}</span> },
  {
    key: 'net_insured_loss',
    header: 'Net insured loss',
    align: 'right',
    value: (r) => r.net_insured_loss,
    render: (r) => (
      <span>
        <span className="font-semibold text-teal-200">{fmtUsd(r.net_insured_loss)}</span>
        <span className="block text-xs text-slate-500">
          {fmtInt(r.n_with_terms)} with terms{r.n_without_terms ? ` · ${fmtInt(r.n_without_terms)} without` : ''}
        </span>
      </span>
    ),
  },
  {
    key: 'sensitivity',
    header: 'Ratio ×0.5 / ×1.5 (gross)',
    align: 'right',
    sortable: false,
    render: (r) => {
      const by = new Map((r.sensitivity ?? []).map((x) => [x.multiplier, x]));
      const lo = by.get(0.5);
      const hi = by.get(1.5);
      if (!lo && !hi) return <span className="text-slate-500">—</span>;
      return (
        <span className="tabular text-xs text-slate-300">
          {fmtUsdCompact(lo?.gross_loss ?? null)} / {fmtUsdCompact(hi?.gross_loss ?? null)}
        </span>
      );
    },
  },
];

function FinancialTotals({ fin }: { fin: PortfolioFinancial }) {
  const tiles: { label: string; value: string; hint?: string }[] = [
    {
      label: 'Sum insured (TIV)',
      value: fmtUsdCompact(fin.total_insured_value ?? null),
      hint: `${fmtInt(fin.n_with_insured_value ?? 0)} of ${fmtInt(fin.n_exposures ?? 0)} exposures`,
    },
    {
      label: 'Replacement value',
      value: fmtUsdCompact(fin.total_replacement_value ?? null),
      hint: `building ${fmtUsdCompact(fin.total_property_value ?? null)} · contents ${fmtUsdCompact(fin.total_contents_value ?? null)}`,
    },
    {
      label: 'Policy limits',
      value: fmtUsdCompact(fin.total_policy_limit ?? null),
      hint: `${fmtInt(fin.n_with_limit ?? 0)} explicit · others use the sum insured`,
    },
    {
      label: 'Deductibles',
      value: fmtUsdCompact(fin.total_deductible ?? null),
      hint: `${fmtInt(fin.n_with_deductible ?? 0)} exposures`,
    },
  ];
  const basis = fin.exposed_value_basis_counts ?? {};
  return (
    <div className="space-y-3 p-4">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {tiles.map((t) => (
          <StatTile key={t.label} label={t.label} value={t.value} hint={t.hint} />
        ))}
      </div>
      {Object.keys(basis).length > 0 && (
        <div className="text-xs text-slate-400">
          Gross loss basis:{' '}
          {Object.entries(basis)
            .map(([k, v]) => `${humanize(k)} (${fmtInt(v)})`)
            .join(' · ')}
        </div>
      )}
    </div>
  );
}

function issueTone(sev: ValidationIssue['severity']): 'red' | 'amber' | 'neutral' {
  if (sev === 'error') return 'red';
  if (sev === 'warning') return 'amber';
  return 'neutral';
}

const exposureColumns: Column<TopExposureOut>[] = [
  { key: 'property_id', header: 'Property', className: 'font-mono text-xs' },
  { key: 'county', header: 'County', value: (r) => r.county ?? null },
  { key: 'insured_value', header: 'Insured value', align: 'right', value: (r) => r.insured_value, render: (r) => fmtUsd(r.insured_value) },
  { key: 'composite_score', header: 'Screening index', align: 'right', value: (r) => r.composite_score, render: (r) => fmtScore(r.composite_score) },
  {
    key: 'drivers',
    header: 'Drivers',
    sortable: false,
    render: (r) =>
      r.drivers.length === 0 ? (
        <span className="text-slate-500">—</span>
      ) : (
        <div className="flex max-w-md flex-wrap gap-1">
          {r.drivers.map((d, i) => (
            <Badge key={`${d}-${i}`} tone="amber">
              {d}
            </Badge>
          ))}
        </div>
      ),
  },
  { key: 'location', header: 'Lat / Lon', sortable: false, render: (r) => <span className="font-mono text-xs text-slate-400">{fmtCoord(r.lat)}, {fmtCoord(r.lon)}</span> },
];

const concentrationColumns: Column<ConcentrationCellOut>[] = [
  { key: 'cell_id', header: 'Cell', className: 'font-mono text-xs' },
  { key: 'n_properties', header: 'Properties', align: 'right', render: (r) => fmtInt(r.n_properties) },
  { key: 'total_insured_value', header: 'TIV', align: 'right', value: (r) => r.total_insured_value, render: (r) => fmtUsdCompact(r.total_insured_value) },
  { key: 'share_of_tiv_pct', header: 'Share of TIV', align: 'right', value: (r) => r.share_of_tiv_pct, render: (r) => fmtPct(r.share_of_tiv_pct) },
  { key: 'dominant_hazard', header: 'Dominant hazard', value: (r) => r.dominant_hazard ?? null, render: (r) => (r.dominant_hazard ? <Badge>{r.dominant_hazard}</Badge> : <span className="text-slate-500">—</span>) },
  { key: 'centroid', header: 'Centroid', sortable: false, render: (r) => <span className="font-mono text-xs text-slate-400">{fmtCoord(r.centroid_lat)}, {fmtCoord(r.centroid_lon)}</span> },
];

const portfolioColumns: Column<PortfolioStatusOut>[] = [
  { key: 'name', header: 'Name', value: (r) => r.name ?? null, render: (r) => <span className="text-slate-100">{r.name ?? <span className="text-slate-500">(unnamed)</span>}</span> },
  { key: 'portfolio_id', header: 'ID', className: 'font-mono text-xs text-slate-400' },
  { key: 'status', header: 'Status', render: (r) => <JobStatusBadge status={r.status} /> },
  { key: 'created_at', header: 'Created', render: (r) => fmtDateTime(r.created_at), className: 'text-xs' },
  { key: 'n_rows', header: 'Rows', align: 'right', render: (r) => fmtInt(r.n_rows) },
  { key: 'n_valid', header: 'Valid', align: 'right', render: (r) => fmtInt(r.n_valid) },
  { key: 'n_invalid', header: 'Invalid', align: 'right', render: (r) => <span className={r.n_invalid > 0 ? 'text-amber-300' : undefined}>{fmtInt(r.n_invalid)}</span> },
  { key: 'n_duplicates', header: 'Duplicates', align: 'right', render: (r) => fmtInt(r.n_duplicates) },
];

// --------------------------------------------------------------------------- page

export function PortfolioAnalysis() {
  const [file, setFile] = useState<File | null>(null);
  const [name, setName] = useState('');
  const [preview, setPreview] = useState<CsvPreview | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const [portfolioId, setPortfolioId] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const uploadM = useUploadPortfolio();
  const portfoliosQ = usePortfolios();
  const statusQ = usePortfolioStatus(portfolioId);
  const status = statusQ.data;
  const analysisQ = usePortfolioAnalysis(portfolioId, status?.status === 'completed');
  const analysis = analysisQ.data;

  function handleFile(f: File) {
    setFile(f);
    setPreview(null);
    setPortfolioId(null);
    uploadM.reset();
    if (!name) setName(f.name.replace(/\.csv$/i, ''));
    parse<Record<string, string>>(f, {
      header: true,
      preview: 10,
      skipEmptyLines: true,
      complete: (results: ParseResult<Record<string, string>>) => {
        setPreview({
          fields: results.meta.fields ?? [],
          rows: results.data,
          errors: results.errors.slice(0, 5).map((e) => `${e.message}${typeof e.row === 'number' ? ` (row ${e.row + 1})` : ''}`),
        });
      },
    });
  }

  function onInputChange(e: ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0];
    if (f) handleFile(f);
  }

  function onDrop(e: DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setDragOver(false);
    const f = e.dataTransfer.files?.[0];
    if (f) handleFile(f);
  }

  function onUpload() {
    if (!file) return;
    uploadM.mutate(
      { file, name: name.trim() || undefined },
      { onSuccess: (res) => setPortfolioId(res.portfolio_id) },
    );
  }

  const contractQ = useCsvContract();
  const columnHints: ColumnHint[] = contractQ.data && contractQ.data.length > 0 ? contractQ.data : FALLBACK_COLUMN_HINTS;
  const mapping = useMemo(
    () => (preview ? columnHints.map((h) => ({ hint: h, matched: matchColumn(h, preview.fields) })) : []),
    [preview, columnHints],
  );
  const issues: ValidationIssue[] = status?.validation_errors ?? [];
  const issueCounts = {
    error: issues.filter((e) => e.severity === 'error' || (!e.severity && e.row != null)).length,
    warning: issues.filter((e) => e.severity === 'warning').length,
    info: issues.filter((e) => e.severity === 'info' || (!e.severity && e.row == null)).length,
  };
  const missingRequired = mapping.filter((m) => m.hint.required && !m.matched).map((m) => m.hint.name);
  const tiles = analysis ? summaryTiles(analysis.summary) : [];
  const financialScenarios: PortfolioScenarioTotal[] =
    analysis?.financial && Array.isArray(analysis.financial.scenarios) ? analysis.financial.scenarios : [];
  const accumulation: LocationAccumulationOut[] = analysis?.location_accumulation ?? [];
  const inProgress = !!portfolioId && (statusQ.isLoading || status?.status === 'pending' || status?.status === 'running');

  return (
    <PageBody>
      <PageHeader
        title="Portfolio analysis"
        description="Upload a CSV of insured locations to score it against the ingested hazard layers. Processing runs server-side; results are a snapshot of the current data."
        actions={
          <a href={apiLinks.exampleCsv()} target="_blank" rel="noreferrer" className="btn-secondary text-xs">
            Download example CSV
          </a>
        }
      />

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <Card title="1. Upload CSV" subtitle="Drag and drop or choose a file; first 10 rows are previewed locally" className="xl:col-span-2">
          <div
            onDragOver={(e) => {
              e.preventDefault();
              setDragOver(true);
            }}
            onDragLeave={() => setDragOver(false)}
            onDrop={onDrop}
            onClick={() => fileInputRef.current?.click()}
            role="button"
            tabIndex={0}
            onKeyDown={(e) => {
              if (e.key === 'Enter' || e.key === ' ') fileInputRef.current?.click();
            }}
            className={
              'flex cursor-pointer flex-col items-center justify-center rounded-lg border-2 border-dashed px-4 py-8 text-center transition-colors ' +
              (dragOver ? 'border-teal-400 bg-teal-500/10' : 'border-slate-700 bg-slate-950/40 hover:border-slate-500')
            }
          >
            <input ref={fileInputRef} type="file" accept=".csv,text/csv" className="hidden" onChange={onInputChange} />
            <div className="text-sm text-slate-200">{file ? file.name : 'Drop a CSV here or click to browse'}</div>
            <div className="mt-1 text-xs text-slate-500">
              {file ? `${fmtInt(Math.round(file.size / 1024))} KB` : 'UTF-8 CSV with a header row; latitude and longitude are required'}
            </div>
          </div>

          <div className="mt-3 grid grid-cols-1 gap-3 md:grid-cols-[1fr_auto] md:items-end">
            <Field label="Portfolio name (optional)">
              <TextInput value={name} onChange={setName} placeholder="e.g. Q3 homeowners book" />
            </Field>
            <button type="button" className="btn-primary" disabled={!file || uploadM.isPending || inProgress} onClick={onUpload}>
              {uploadM.isPending ? 'Uploading…' : 'Upload & analyse'}
            </button>
          </div>

          {uploadM.isError && <ErrorState compact className="mt-3" error={uploadM.error} title="Upload failed" onRetry={onUpload} />}

          {preview && (
            <div className="mt-4">
              <div className="mb-1 flex items-center justify-between">
                <div className="text-xs font-semibold uppercase tracking-wide text-slate-400">Preview (first {preview.rows.length} rows)</div>
                <div className="text-xs text-slate-500">{preview.fields.length} columns</div>
              </div>
              {preview.errors.length > 0 && (
                <CaveatBanner className="mb-2 text-xs" title="Parser warnings">
                  <ul className="list-disc pl-4">
                    {preview.errors.map((e, i) => (
                      <li key={i}>{e}</li>
                    ))}
                  </ul>
                </CaveatBanner>
              )}
              <div className="max-h-64 overflow-auto rounded border border-slate-800">
                <table className="w-full min-w-max border-collapse text-xs">
                  <thead className="sticky top-0 bg-slate-900">
                    <tr>
                      {preview.fields.map((f) => (
                        <th key={f} className="whitespace-nowrap border-b border-slate-800 px-2 py-1.5 text-left font-semibold text-slate-400">
                          {f}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {preview.rows.map((row, i) => (
                      <tr key={i} className="border-b border-slate-800/60">
                        {preview.fields.map((f) => (
                          <td key={f} className="whitespace-nowrap px-2 py-1 text-slate-300">
                            {row[f] ?? ''}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </Card>

        <Card
          title="Column mapping"
          subtitle={contractQ.data ? 'Contract served by the API; the parser uses the same aliases' : 'Expected columns and what was detected in your file'}
        >
          {missingRequired.length > 0 && (
            <CaveatBanner tone="red" className="mb-3 text-xs" title="Required columns missing">
              {missingRequired.join(', ')} — the server will reject rows without coordinates.
            </CaveatBanner>
          )}
          <ul className="space-y-2 text-sm">
            {columnHints.map((h) => {
              const m = mapping.find((x) => x.hint.name === h.name);
              return (
                <li key={h.name} className="rounded border border-slate-800 px-2 py-1.5">
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-mono text-xs text-slate-100">{h.name}</span>
                    <span className="flex items-center gap-1">
                      {h.required ? <Badge tone="amber">required</Badge> : <Badge>optional</Badge>}
                      {preview && (m?.matched ? <Badge tone="teal">{m.matched}</Badge> : <Badge tone={h.required ? 'red' : 'neutral'}>not found</Badge>)}
                    </span>
                  </div>
                  <div className="mt-0.5 text-xs text-slate-500">{h.description}</div>
                  <div className="mt-0.5 truncate text-[11px] text-slate-600" title={h.aliases.join(', ')}>
                    aliases: {h.aliases.join(', ')}
                  </div>
                </li>
              );
            })}
          </ul>
        </Card>
      </div>

      {portfolioId && (
        <Card
          title="2. Processing"
          subtitle={`Portfolio ${portfolioId}`}
          className="mt-4"
          actions={status ? <JobStatusBadge status={status.status} /> : undefined}
        >
          {statusQ.isError ? (
            <ErrorState error={statusQ.error} onRetry={() => void statusQ.refetch()} />
          ) : !status ? (
            <LoadingState compact label="Waiting for job status…" />
          ) : (
            <div className="space-y-3">
              <ProgressBar
                value={status.status === 'completed' ? 1 : status.progress}
                label={status.status === 'running' || status.status === 'pending' ? 'Analysing…' : status.status}
                tone={status.status === 'failed' ? 'red' : 'teal'}
                indeterminate={status.status === 'pending' && !status.progress}
              />
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
                <StatTile label="Rows" value={fmtInt(status.n_rows)} />
                <StatTile label="Valid" value={fmtInt(status.n_valid)} accent="teal" />
                <StatTile label="Invalid" value={fmtInt(status.n_invalid)} accent={status.n_invalid > 0 ? 'amber' : 'default'} hint="rows rejected" />
                <StatTile label="Exact duplicates" value={fmtInt(status.n_duplicates)} hint="same id + location; excluded" />
                <StatTile label="Shared locations" value={fmtInt(status.n_shared_location ?? 0)} hint="kept; see accumulation" />
                <StatTile label="Distinct locations" value={fmtInt(status.n_locations ?? 0)} />
              </div>
              {status.status === 'failed' && (
                <CaveatBanner tone="red" title="Analysis failed">
                  {status.error ?? 'The server reported a failure without details.'}
                </CaveatBanner>
              )}
              {issues.length > 0 && (
                <details>
                  <summary className="cursor-pointer text-xs font-semibold uppercase tracking-wide text-slate-400">
                    Validation issues ({fmtInt(issues.length)}): {fmtInt(issueCounts.error)} row errors · {fmtInt(issueCounts.warning)} field warnings ·{' '}
                    {fmtInt(issueCounts.info)} notes
                  </summary>
                  <ul className="mt-2 max-h-56 space-y-1 overflow-auto text-xs">
                    {issues.slice(0, 80).map((e, i) => (
                      <li key={i} className="flex flex-wrap items-center gap-2 rounded border border-slate-800 px-2 py-1 text-slate-300">
                        <Badge tone={issueTone(e.severity)}>{e.severity ?? (e.row != null ? 'error' : 'info')}</Badge>
                        {e.row != null && <span className="font-mono text-slate-400">row {fmtInt(e.row)}</span>}
                        {e.field && <span className="font-mono text-slate-400">{e.field}</span>}
                        <span>{e.error}</span>
                      </li>
                    ))}
                    {issues.length > 80 && <li className="text-slate-500">… {fmtInt(issues.length - 80)} more</li>}
                  </ul>
                </details>
              )}
            </div>
          )}
        </Card>
      )}

      {portfolioId && status?.status === 'completed' && (
        <>
          {analysisQ.isLoading ? (
            <Card className="mt-4">
              <LoadingState label="Loading analysis…" />
            </Card>
          ) : analysisQ.isError ? (
            <Card className="mt-4">
              <ErrorState error={analysisQ.error} onRetry={() => void analysisQ.refetch()} />
            </Card>
          ) : analysis ? (
            <>
              <div className="mt-6 flex flex-col gap-3 md:flex-row md:items-end md:justify-between">
                <div>
                  <h2 className="text-lg font-semibold">3. Results</h2>
                  <div className="text-xs text-slate-500">Generated {fmtDateTime(analysis.generated_at)}</div>
                </div>
                <div className="flex gap-2">
                  <a href={apiLinks.portfolioExport(analysis.portfolio_id, 'csv')} target="_blank" rel="noreferrer" className="btn-secondary text-xs">
                    Export CSV
                  </a>
                  <a href={apiLinks.portfolioExport(analysis.portfolio_id, 'json')} target="_blank" rel="noreferrer" className="btn-secondary text-xs">
                    Export JSON
                  </a>
                </div>
              </div>

              {analysis.warnings.length > 0 && (
                <CaveatBanner title="Warnings" className="mt-3">
                  <ul className="list-disc space-y-1 pl-4">
                    {analysis.warnings.map((w, i) => (
                      <li key={i}>{w}</li>
                    ))}
                  </ul>
                </CaveatBanner>
              )}

              {tiles.length === 0 ? (
                <EmptyState compact className="mt-3" title="No summary" message="The analysis summary was empty." />
              ) : (
                <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2 md:grid-cols-3 xl:grid-cols-6">
                  {tiles.map((t) => (
                    <StatTile key={t.key} label={t.label} value={t.value} />
                  ))}
                </div>
              )}

              <div className="mt-4 grid grid-cols-1 gap-4 xl:grid-cols-3">
                <Card title="Portfolio map" subtitle="Top exposures (points, coloured by screening index) and concentration cells (amber circles sized by property count)" className="xl:col-span-2" flush>
                  <PortfolioMap analysis={analysis} />
                  <div className="flex flex-wrap items-start gap-6 border-t border-slate-800 p-3">
                    <LegendBox title="Screening index" items={pointLegend('composite_score').map((l) => ({ ...l, shape: 'circle' as const }))} inline />
                    <LegendBox title="Concentration" items={[{ label: 'Cell sized by n_properties', color: COLORS.amber, shape: 'circle' }]} />
                  </div>
                </Card>
                <Card title="Concentration cells" subtitle="Spatial accumulation of insured value" flush>
                  <DataTable columns={concentrationColumns} rows={analysis.concentration} rowKey={(r) => r.cell_id} initialSort={{ key: 'n_properties', dir: 'desc' }} dense maxHeight="420px" emptyMessage="No concentration cells returned." />
                </Card>
              </div>

              <h3 className="mb-2 mt-6 text-sm font-semibold uppercase tracking-wide text-slate-400">Hazard distribution</h3>
              <HazardDistributionPanel distribution={analysis.hazard_distribution} />

              <Card title="By county" className="mt-4" flush>
                <DataTable columns={countyColumns} rows={analysis.by_county} rowKey={countyRowKey} initialSort={{ key: 'n_properties', dir: 'desc' }} dense maxHeight="480px" emptyMessage="No county breakdown returned." />
              </Card>

              <Card title="Top exposures" subtitle="Highest-ranked locations with the hazards driving their rank" className="mt-4" flush>
                <DataTable columns={exposureColumns} rows={analysis.top_exposures} rowKey={(r) => r.property_id} dense maxHeight="480px" emptyMessage="No top exposures returned." />
              </Card>

              <Card
                title="Location accumulation"
                subtitle="Several exposures at one site (within ~5 m): kept in every total, and the first place to look for a single-event pile-up"
                className="mt-4"
                flush
              >
                <DataTable
                  columns={accumulationColumns}
                  rows={accumulation}
                  rowKey={(r) => r.location_key}
                  initialSort={{ key: 'total_insured_value', dir: 'desc' }}
                  dense
                  maxHeight="360px"
                  emptyMessage="No two exposures share a location."
                />
              </Card>

              {portfolioId && <StressTestPanel portfolioId={portfolioId} />}

              <Card
                title="Financial scenarios"
                subtitle={
                  analysis.financial
                    ? 'Each kind of money is totalled separately; gross is physical damage to the replacement value, net applies each exposure\'s deductible and limit'
                    : 'Not available'
                }
                className="mt-4"
                flush
              >
                {analysis.financial ? (
                  <>
                    <FinancialTotals fin={analysis.financial} />
                    <DataTable
                      columns={scenarioTotalColumns}
                      rows={financialScenarios}
                      rowKey={(r) => r.scenario_id}
                      initialSort={{ key: 'gross_loss', dir: 'desc' }}
                      dense
                      emptyMessage="No scenario triggered for any exposure."
                    />
                    {typeof analysis.financial.note === 'string' && analysis.financial.note && (
                      <div className="border-t border-slate-800 p-4">
                        <CaveatBanner title="Scenario note">{analysis.financial.note}</CaveatBanner>
                      </div>
                    )}
                  </>
                ) : (
                  <div className="p-4">
                    <EmptyState compact title="No financial scenarios" message="The upload had no replacement, contents or insured values, so no loss scenarios were computed." />
                  </div>
                )}
              </Card>

              <ScreeningIndexCaveat className="mt-4" />
            </>
          ) : null}
        </>
      )}

      <Card title="Previously analysed portfolios" subtitle="Select a row to load its results" className="mt-6" flush>
        {portfoliosQ.isLoading ? (
          <LoadingState compact label="Loading portfolios…" />
        ) : portfoliosQ.isError ? (
          <div className="p-4">
            <ErrorState compact error={portfoliosQ.error} onRetry={() => void portfoliosQ.refetch()} />
          </div>
        ) : (portfoliosQ.data ?? []).length === 0 ? (
          <div className="p-4">
            <EmptyState compact title="No portfolios yet" message="Upload a CSV above to run the first analysis." />
          </div>
        ) : (
          <DataTable
            columns={portfolioColumns}
            rows={portfoliosQ.data ?? []}
            rowKey={(r) => r.portfolio_id}
            initialSort={{ key: 'created_at', dir: 'desc' }}
            onRowClick={(r) => setPortfolioId(r.portfolio_id)}
            rowClassName={(r) => (r.portfolio_id === portfolioId ? 'bg-teal-500/5' : undefined)}
            dense
            maxHeight="360px"
          />
        )}
      </Card>
    </PageBody>
  );
}
