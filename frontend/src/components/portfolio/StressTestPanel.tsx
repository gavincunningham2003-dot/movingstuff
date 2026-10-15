import 'maplibre-gl/dist/maplibre-gl.css';

import type { FeatureCollection, Point, Position } from 'geojson';
import { useEffect, useMemo, useState } from 'react';
import MapGL, { Layer, NavigationControl, Popup, Source, type LayerProps, type MapLayerMouseEvent } from 'react-map-gl/maplibre';

import { apiLinks } from '../../api/client';
import { useStressTest, useStressTests } from '../../api/hooks';
import type { StressEventSummaryOut, StressHitOut } from '../../api/types';
import { fmtCoord, fmtDate, fmtInt, fmtNum, fmtPct, fmtScore, fmtUsd, fmtUsdCompact, humanize } from '../../lib/format';
import { DARK_BASEMAP } from '../map/basemap';
import { Badge } from '../ui/Badge';
import { Card } from '../ui/Card';
import { CaveatBanner } from '../ui/CaveatBanner';
import { DataTable, type Column } from '../ui/DataTable';
import { EmptyState } from '../ui/EmptyState';
import { ErrorState } from '../ui/ErrorState';
import { Field, Select, TextInput } from '../ui/Inputs';
import { LoadingState } from '../ui/LoadingState';
import { StatTile } from '../ui/StatTile';

const BUFFER_OPTIONS = ['0', '500', '1000', '2500', '5000'].map((v) => ({ value: v, label: v === '0' ? 'Inside perimeter only' : `Within ${fmtInt(Number(v))} m` }));

const perimeterFill: LayerProps = {
  id: 'st-perimeter-fill',
  type: 'fill',
  paint: { 'fill-color': '#f97316', 'fill-opacity': 0.22 },
};
const perimeterLine: LayerProps = {
  id: 'st-perimeter-line',
  type: 'line',
  paint: { 'line-color': '#fb923c', 'line-width': 1.5 },
};
const hitLayer: LayerProps = {
  id: 'st-hits',
  type: 'circle',
  paint: {
    'circle-color': ['match', ['get', 'hit_kind'], 'inside', '#f87171', '#facc15'],
    'circle-radius': ['match', ['get', 'hit_kind'], 'inside', 7, 5],
    'circle-stroke-color': '#020617',
    'circle-stroke-width': 1.2,
  },
};

function flattenPositions(geom: unknown, out: Position[] = []): Position[] {
  if (Array.isArray(geom)) {
    if (geom.length >= 2 && typeof geom[0] === 'number' && typeof geom[1] === 'number') out.push(geom as Position);
    else geom.forEach((g) => flattenPositions(g, out));
  } else if (geom && typeof geom === 'object' && 'coordinates' in geom) {
    flattenPositions((geom as { coordinates: unknown }).coordinates, out);
  }
  return out;
}

function boundsOf(positions: Position[]): [[number, number], [number, number]] | null {
  if (positions.length === 0) return null;
  let w = Infinity;
  let s = Infinity;
  let e = -Infinity;
  let n = -Infinity;
  for (const [x, y] of positions) {
    if (x < w) w = x;
    if (x > e) e = x;
    if (y < s) s = y;
    if (y > n) n = y;
  }
  return [
    [w, s],
    [e, n],
  ];
}

const hitColumns: Column<StressHitOut>[] = [
  { key: 'property_id', header: 'Property', className: 'font-mono text-xs' },
  { key: 'policy_id', header: 'Policy', className: 'font-mono text-xs', value: (r) => r.policy_id ?? null },
  {
    key: 'hit_kind',
    header: 'Hit',
    value: (r) => r.hit_kind,
    render: (r) => <Badge tone={r.hit_kind === 'inside' ? 'red' : 'amber'}>{r.hit_kind === 'inside' ? 'inside' : `${fmtInt(Math.round(r.distance_m))} m`}</Badge>,
  },
  { key: 'insured_value', header: 'Sum insured', align: 'right', value: (r) => r.insured_value, render: (r) => fmtUsd(r.insured_value) },
  {
    key: 'exposed_value',
    header: 'Exposed value',
    align: 'right',
    value: (r) => r.exposed_value,
    render: (r) => (
      <span>
        {fmtUsd(r.exposed_value)}
        {r.exposed_value_basis !== 'missing' && <span className="block text-[11px] text-slate-500">{humanize(r.exposed_value_basis)}</span>}
      </span>
    ),
  },
  { key: 'gross_loss', header: 'Gross loss', align: 'right', value: (r) => r.gross_loss, render: (r) => <span className="font-semibold text-amber-200">{fmtUsd(r.gross_loss)}</span> },
  { key: 'net_insured_loss', header: 'Net insured loss', align: 'right', value: (r) => r.net_insured_loss, render: (r) => <span className="font-semibold text-teal-200">{fmtUsd(r.net_insured_loss)}</span> },
  { key: 'composite_score', header: 'Screening index', align: 'right', value: (r) => r.composite_score, render: (r) => fmtScore(r.composite_score) },
];

function eventLabel(e: { fire_name: string | null; year: number | null; gis_acres: number | null }): string {
  const acres = e.gis_acres != null ? ` · ${fmtInt(Math.round(e.gis_acres))} ac` : '';
  return `${e.fire_name ?? 'Unnamed fire'} (${e.year ?? '?'})${acres}`;
}

function StressMap({ perimeter, hits }: { perimeter: Record<string, unknown> | null; hits: StressHitOut[] }) {
  const [selected, setSelected] = useState<StressHitOut | null>(null);
  const perimeterFc = useMemo<FeatureCollection>(
    () => ({ type: 'FeatureCollection', features: perimeter ? [{ type: 'Feature', geometry: perimeter as never, properties: {} }] : [] }),
    [perimeter],
  );
  const hitsFc = useMemo<FeatureCollection<Point>>(
    () => ({
      type: 'FeatureCollection',
      features: hits.map((h) => ({ type: 'Feature', geometry: { type: 'Point', coordinates: [h.lon, h.lat] }, properties: { ...h } })),
    }),
    [hits],
  );
  const bounds = useMemo(() => boundsOf([...flattenPositions(perimeter), ...hits.map((h): Position => [h.lon, h.lat])]), [perimeter, hits]);
  if (!bounds) return null;
  const onClick = (ev: MapLayerMouseEvent) => {
    const f = ev.features?.[0];
    setSelected(f ? (f.properties as StressHitOut) : null);
  };
  return (
    <div className="h-[380px] w-full">
      <MapGL
        key={`${bounds[0][0]},${bounds[1][1]}`}
        initialViewState={{ bounds, fitBoundsOptions: { padding: 40, maxZoom: 12 } }}
        mapStyle={DARK_BASEMAP}
        style={{ width: '100%', height: '100%' }}
        interactiveLayerIds={['st-hits']}
        onClick={onClick}
      >
        <NavigationControl position="top-left" showCompass={false} />
        <Source id="st-perimeter" type="geojson" data={perimeterFc}>
          <Layer {...perimeterFill} />
          <Layer {...perimeterLine} />
        </Source>
        <Source id="st-hits" type="geojson" data={hitsFc}>
          <Layer {...hitLayer} />
        </Source>
        {selected && (
          <Popup longitude={selected.lon} latitude={selected.lat} anchor="bottom" onClose={() => setSelected(null)} closeOnClick={false}>
            <div className="min-w-[200px] p-3 text-xs text-slate-200">
              <div className="text-sm font-semibold">Property {selected.property_id}</div>
              <div className="text-slate-400">
                {selected.hit_kind === 'inside' ? 'Inside perimeter' : `${fmtInt(Math.round(selected.distance_m))} m from perimeter`} · {fmtCoord(selected.lat)}, {fmtCoord(selected.lon)}
              </div>
              <div className="mt-1">Sum insured {fmtUsd(selected.insured_value)}</div>
              <div>Gross loss {fmtUsd(selected.gross_loss)}</div>
              <div>Net insured loss {fmtUsd(selected.net_insured_loss)}</div>
            </div>
          </Popup>
        )}
      </MapGL>
    </div>
  );
}

export function StressTestPanel({ portfolioId }: { portfolioId: string }) {
  const [bufferM, setBufferM] = useState('0');
  const [eventId, setEventId] = useState<string>('');
  const [ratioText, setRatioText] = useState('0.5');
  const ratio = Math.min(1, Math.max(0.01, Number(ratioText) || 0.5));
  const listQ = useStressTests(portfolioId, Number(bufferM));
  const list = listQ.data;

  // pick the event that hits the most sum insured once the list arrives
  useEffect(() => {
    if (!eventId && list && list.events.length > 0) setEventId(String(list.events[0].event_id));
  }, [list, eventId]);

  const detailQ = useStressTest(portfolioId, eventId ? Number(eventId) : null, ratio, Number(bufferM));
  const detail = detailQ.data;

  const hitOptions = (list?.events ?? []).map((e) => ({ value: String(e.event_id), label: `${eventLabel(e)} — ${fmtInt(e.n_exposures_inside)} inside` }));
  const hitIds = new Set((list?.events ?? []).map((e) => e.event_id));
  const catalogueOptions = (list?.catalogue ?? []).filter((e) => !hitIds.has(e.event_id)).map((e) => ({ value: String(e.event_id), label: `${eventLabel(e)} — misses` }));

  const eventColumns: Column<StressEventSummaryOut>[] = [
    { key: 'fire_name', header: 'Fire', value: (r) => r.fire_name ?? 'Unnamed', className: 'font-medium' },
    { key: 'year', header: 'Year', align: 'right', value: (r) => r.year },
    { key: 'gis_acres', header: 'Acres', align: 'right', value: (r) => r.gis_acres, render: (r) => fmtInt(r.gis_acres == null ? null : Math.round(r.gis_acres)) },
    { key: 'n_exposures_inside', header: 'Inside', align: 'right', value: (r) => r.n_exposures_inside, render: (r) => fmtInt(r.n_exposures_inside) },
    ...(Number(bufferM) > 0
      ? [{ key: 'n_exposures_within_buffer', header: 'Near miss', align: 'right' as const, value: (r: StressEventSummaryOut) => r.n_exposures_within_buffer, render: (r: StressEventSummaryOut) => fmtInt(r.n_exposures_within_buffer) }]
      : []),
    { key: 'total_insured_value_inside', header: 'Sum insured inside', align: 'right', value: (r) => r.total_insured_value_inside, render: (r) => fmtUsd(r.total_insured_value_inside) },
    { key: 'share_of_tiv_pct', header: '% of TIV', align: 'right', value: (r) => r.share_of_tiv_pct, render: (r) => fmtPct(r.share_of_tiv_pct) },
  ];

  const totals = detail?.totals;
  const num = (k: string): number | null => {
    const v = totals?.[k];
    return typeof v === 'number' ? v : null;
  };

  return (
    <Card
      title="Historical stress tests"
      subtitle="Replay an ingested CAL FIRE perimeter on this portfolio: which exposures it reaches, how much of the book that is, and the loss under an assumed damage ratio"
      className="mt-4"
    >
      {listQ.isLoading ? (
        <LoadingState compact label="Overlaying perimeters…" />
      ) : listQ.isError ? (
        <ErrorState compact error={listQ.error} onRetry={() => void listQ.refetch()} />
      ) : list ? (
        <div className="space-y-4">
          <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
            <Field label="Event" hint={`${fmtInt(list.events.length)} perimeters touch this portfolio; the catalogue lists large fires that miss it`}>
              <Select
                value={eventId}
                onChange={setEventId}
                placeholder="Choose a fire"
                options={[...hitOptions, ...catalogueOptions]}
              />
            </Field>
            <Field label="Near-miss buffer" hint="Exposures within the buffer are listed for context, not totalled">
              <Select value={bufferM} onChange={setBufferM} options={BUFFER_OPTIONS} />
            </Field>
            <Field label="Assumed damage ratio (0-1)" hint="Share of exposed value lost inside the perimeter; an assumption, not a measurement">
              <TextInput value={ratioText} onChange={setRatioText} inputMode="decimal" />
            </Field>
          </div>

          <DataTable
            columns={eventColumns}
            rows={list.events}
            rowKey={(r) => r.event_id}
            onRowClick={(r) => setEventId(String(r.event_id))}
            rowClassName={(r) => (String(r.event_id) === eventId ? 'bg-teal-500/10' : undefined)}
            initialSort={{ key: 'total_insured_value_inside', dir: 'desc' }}
            dense
            maxHeight="280px"
            emptyMessage="No ingested perimeter touches any exposure in this portfolio. Pick a fire from the catalogue to confirm it misses, or widen the buffer."
          />

          {eventId &&
            (detailQ.isLoading ? (
              <LoadingState compact label="Running stress test…" />
            ) : detailQ.isError ? (
              <ErrorState compact error={detailQ.error} onRetry={() => void detailQ.refetch()} />
            ) : detail && totals ? (
              <div className="space-y-4">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="text-sm">
                    <span className="font-semibold text-slate-100">{eventLabel(detail.event as never)}</span>
                    <span className="text-slate-400">
                      {' '}
                      · alarm {fmtDate(typeof detail.event.alarm_date === 'string' ? detail.event.alarm_date : null)} · damage ratio {fmtNum(detail.damage_ratio, 2)}
                      {detail.buffer_m > 0 && ` · buffer ${fmtInt(detail.buffer_m)} m`}
                    </span>
                  </div>
                  <a href={apiLinks.stressExport(portfolioId, Number(eventId), ratio, Number(bufferM))} className="btn-secondary text-xs" target="_blank" rel="noreferrer">
                    Export hits (CSV)
                  </a>
                </div>
                <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
                  <StatTile label="Exposures inside" value={fmtInt(num('n_exposures_inside'))} hint={`of ${fmtInt(num('n_exposures_portfolio'))}`} accent={(num('n_exposures_inside') ?? 0) > 0 ? 'red' : 'default'} />
                  <StatTile label="Sum insured inside" value={fmtUsdCompact(num('total_insured_value_inside'))} hint={`${fmtPct(num('share_of_tiv_pct'))} of TIV`} />
                  <StatTile label="Replacement value inside" value={fmtUsdCompact(num('total_replacement_value_inside'))} />
                  <StatTile label="Gross loss" value={fmtUsdCompact(num('gross_loss'))} hint={`${fmtInt(num('n_with_value'))} exposures with a value`} accent="amber" />
                  <StatTile label="Net insured loss" value={fmtUsdCompact(num('net_insured_loss'))} hint={`${fmtInt(num('n_with_terms'))} with terms`} accent="teal" />
                </div>
                {Array.isArray(totals.sensitivity) && (
                  <div className="text-xs text-slate-400">
                    Gross at other ratios:{' '}
                    {(totals.sensitivity as { damage_ratio: number; gross_loss: number | null }[]).map((s) => `${fmtNum(s.damage_ratio, 2)} → ${fmtUsdCompact(s.gross_loss)}`).join(' · ')}
                  </div>
                )}
                <StressMap perimeter={detail.perimeter} hits={detail.hits} />
                <DataTable
                  columns={hitColumns}
                  rows={detail.hits}
                  rowKey={(r) => `${r.row_number}`}
                  initialSort={{ key: 'gross_loss', dir: 'desc' }}
                  dense
                  maxHeight="360px"
                  emptyMessage="This perimeter reaches none of the uploaded exposures."
                />
                <CaveatBanner title="What this replay is and is not">
                  <ul className="list-disc space-y-1 pl-4">
                    {[...detail.assumptions, ...detail.caveats].map((a, i) => (
                      <li key={i}>{a}</li>
                    ))}
                  </ul>
                </CaveatBanner>
              </div>
            ) : null)}
          {!eventId && <EmptyState compact title="Choose an event" message="Select a fire above to replay its perimeter on this portfolio." />}
        </div>
      ) : null}
    </Card>
  );
}
