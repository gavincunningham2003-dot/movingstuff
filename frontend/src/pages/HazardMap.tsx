import 'maplibre-gl/dist/maplibre-gl.css';

import type { UseQueryResult } from '@tanstack/react-query';
import type { Point } from 'geojson';
import type { GeoJSONSource } from 'maplibre-gl';
import { useCallback, useMemo, useRef, useState } from 'react';
import Map, {
  Layer,
  NavigationControl,
  Popup,
  Source,
  type LayerProps,
  type MapLayerMouseEvent,
  type MapRef,
} from 'react-map-gl/maplibre';
import { Link } from 'react-router-dom';

import { useHazardGeojson, useHazardsCatalogue, usePointAssessment, usePropertiesGeojson, usePropertyCounties } from '../api/hooks';
import type { HazardLayerFeatureCollection, LegendEntry, PropertyFeatureCollection, PropertyFeatureProperties } from '../api/types';
import { bboxString, CALIFORNIA_VIEW, clipToCalifornia, DARK_BASEMAP, LABEL_FONT } from '../components/map/basemap';
import {
  expr,
  legendFillExpression,
  POINT_COLOR_OPTIONS,
  pointColorExpression,
  pointLegend,
  type PointColorBy,
} from '../components/map/expressions';
import { Badge, FloodZoneBadge, WildfireBadge } from '../components/ui/Badge';
import { PointAssessmentCard } from '../components/map/PointAssessmentCard';
import { CaveatBanner } from '../components/ui/CaveatBanner';
import { ErrorState } from '../components/ui/ErrorState';
import { Field, Select, Toggle } from '../components/ui/Inputs';
import { LegendBox } from '../components/ui/LegendBox';
import { fmtInt, fmtNum, fmtScore } from '../lib/format';
import { COLORS } from '../lib/hazardColors';
import { useDebouncedValue } from '../lib/useDebouncedValue';

const POINTS_SOURCE = 'properties';
const POINT_LIMIT = 5000;
const MIN_POINT_ZOOM = 5;
const MIN_OVERLAY_ZOOM = 8;
const EMPTY_FC: PropertyFeatureCollection = { type: 'FeatureCollection', features: [] };

type OverlayId = 'wildfire_fhsz' | 'flood_nfhl' | 'tsunami' | 'fire_perimeters';

interface OverlayDef {
  id: OverlayId;
  label: string;
  fallback: string;
}

const OVERLAYS: OverlayDef[] = [
  { id: 'wildfire_fhsz', label: 'Wildfire Fire Hazard Severity Zones', fallback: COLORS.wildfireHigh },
  { id: 'flood_nfhl', label: 'Flood zones (NFHL)', fallback: COLORS.floodSfha },
  { id: 'tsunami', label: 'Tsunami inundation', fallback: COLORS.tsunami },
  { id: 'fire_perimeters', label: 'Historic fire perimeters', fallback: '#fb923c' },
];

const clusterLayer: LayerProps = {
  id: 'clusters',
  type: 'circle',
  filter: expr('has', 'point_count'),
  paint: {
    'circle-color': expr('step', expr('get', 'point_count'), '#0f766e', 50, '#0d9488', 250, '#14b8a6', 1000, '#2dd4bf'),
    'circle-radius': expr('step', expr('get', 'point_count'), 14, 50, 18, 250, 24, 1000, 30),
    'circle-stroke-width': 1.5,
    'circle-stroke-color': '#020617',
    'circle-opacity': 0.9,
  },
};

const clusterCountLayer: LayerProps = {
  id: 'cluster-count',
  type: 'symbol',
  filter: expr('has', 'point_count'),
  layout: {
    'text-field': expr('get', 'point_count_abbreviated'),
    'text-font': LABEL_FONT,
    'text-size': 12,
    'text-allow-overlap': true,
  },
  paint: { 'text-color': '#f8fafc' },
};

function unclusteredLayer(colorBy: PointColorBy): LayerProps {
  return {
    id: 'unclustered-point',
    type: 'circle',
    filter: expr('!', expr('has', 'point_count')),
    paint: {
      'circle-color': pointColorExpression(colorBy),
      'circle-radius': expr('interpolate', expr('linear'), expr('zoom'), 5, 3, 10, 5, 14, 8),
      'circle-stroke-width': 1,
      'circle-stroke-color': '#020617',
      'circle-opacity': 0.9,
    },
  };
}

function overlayFillLayer(def: OverlayDef, legend: LegendEntry[]): LayerProps {
  return {
    id: `hz-${def.id}-fill`,
    type: 'fill',
    paint: { 'fill-color': legendFillExpression(legend, def.fallback), 'fill-opacity': 0.35 },
    beforeId: 'unclustered-point',
  };
}

function overlayLineLayer(def: OverlayDef, legend: LegendEntry[]): LayerProps {
  return {
    id: `hz-${def.id}-line`,
    type: 'line',
    paint: { 'line-color': legendFillExpression(legend, def.fallback), 'line-width': 0.7, 'line-opacity': 0.8 },
    beforeId: 'unclustered-point',
  };
}

interface SelectedProperty extends PropertyFeatureProperties {
  lon: number;
  lat: number;
}

type OverlayQuery = UseQueryResult<HazardLayerFeatureCollection, Error>;

export function HazardMap() {
  const mapRef = useRef<MapRef>(null);
  const [bbox, setBbox] = useState<string | null>(null);
  const [zoom, setZoom] = useState<number>(CALIFORNIA_VIEW.zoom);
  const [county, setCounty] = useState('');
  const [colorBy, setColorBy] = useState<PointColorBy>('composite_score');
  const [overlays, setOverlays] = useState<Record<OverlayId, boolean>>({
    wildfire_fhsz: false,
    flood_nfhl: false,
    tsunami: false,
    fire_perimeters: false,
  });
  const [selected, setSelected] = useState<SelectedProperty | null>(null);
  const [clicked, setClicked] = useState<{ lon: number; lat: number } | null>(null);
  const pointQ = usePointAssessment(clicked);
  const [hovering, setHovering] = useState(false);
  const [panelOpen, setPanelOpen] = useState(true);

  const debouncedBbox = useDebouncedValue(bbox, 400);
  const debouncedZoom = useDebouncedValue(zoom, 400);
  const pointsEnabled = zoom >= MIN_POINT_ZOOM;
  const overlaysEnabled = zoom >= MIN_OVERLAY_ZOOM;

  const countiesQ = usePropertyCounties();
  const catalogueQ = useHazardsCatalogue();
  const pointsQ = usePropertiesGeojson(debouncedBbox, county, POINT_LIMIT, pointsEnabled);

  const wildfireQ = useHazardGeojson('wildfire_fhsz', debouncedBbox, debouncedZoom, overlays.wildfire_fhsz && overlaysEnabled);
  const floodQ = useHazardGeojson('flood_nfhl', debouncedBbox, debouncedZoom, overlays.flood_nfhl && overlaysEnabled);
  const tsunamiQ = useHazardGeojson('tsunami', debouncedBbox, debouncedZoom, overlays.tsunami && overlaysEnabled);
  const perimetersQ = useHazardGeojson('fire_perimeters', debouncedBbox, debouncedZoom, overlays.fire_perimeters && overlaysEnabled);

  const overlayQueries: Record<OverlayId, OverlayQuery> = {
    wildfire_fhsz: wildfireQ,
    flood_nfhl: floodQ,
    tsunami: tsunamiQ,
    fire_perimeters: perimetersQ,
  };

  const syncView = useCallback(() => {
    const map = mapRef.current?.getMap();
    if (!map) return;
    const b = map.getBounds();
    const c = clipToCalifornia(b.getWest(), b.getSouth(), b.getEast(), b.getNorth());
    setBbox(bboxString(c.west, c.south, c.east, c.north));
    setZoom(map.getZoom());
  }, []);

  const onClick = useCallback((e: MapLayerMouseEvent) => {
    const f = e.features?.[0];
    if (!f) {
      // Empty map: assess the clicked coordinate on demand.
      setSelected(null);
      setClicked({ lon: e.lngLat.lng, lat: e.lngLat.lat });
      return;
    }
    setClicked(null);
    const map = mapRef.current?.getMap();
    const coords = (f.geometry as Point).coordinates;
    if (f.layer.id === 'clusters') {
      const clusterId = f.properties.cluster_id as number | undefined;
      const src = map?.getSource(POINTS_SOURCE) as GeoJSONSource | undefined;
      if (!map || !src || clusterId === undefined) return;
      void src.getClusterExpansionZoom(clusterId).then((z) => {
        map.easeTo({ center: [coords[0], coords[1]], zoom: Math.min(z, 16), duration: 500 });
      });
      return;
    }
    const p = f.properties as Partial<PropertyFeatureProperties>;
    if (p.id === undefined || p.id === null) return;
    setSelected({
      id: Number(p.id),
      address: p.address ?? null,
      county: p.county ?? null,
      composite_score: p.composite_score ?? null,
      wildfire_fhsz: p.wildfire_fhsz ?? null,
      flood_zone: p.flood_zone ?? null,
      pga_2pc50_g: p.pga_2pc50_g ?? null,
      lon: coords[0],
      lat: coords[1],
    });
  }, []);

  const pointLayer = useMemo(() => unclusteredLayer(colorBy), [colorBy]);
  const pointsData = pointsEnabled ? pointsQ.data ?? EMPTY_FC : EMPTY_FC;
  const nPoints = pointsData.features.length;

  const countyOptions = useMemo(
    () =>
      (countiesQ.data ?? [])
        .filter((c) => !!c.county)
        .sort((a, b) => (a.county ?? '').localeCompare(b.county ?? ''))
        .map((c) => ({ value: c.county ?? '', label: `${c.county} (${fmtInt(c.n)})` })),
    [countiesQ.data],
  );

  return (
    <div className="flex h-full flex-col lg:flex-row">
      <div className="relative min-h-[420px] flex-1">
        <Map
          ref={mapRef}
          initialViewState={CALIFORNIA_VIEW}
          mapStyle={DARK_BASEMAP}
          style={{ width: '100%', height: '100%' }}
          minZoom={4}
          maxZoom={18}
          interactiveLayerIds={['clusters', 'unclustered-point']}
          cursor={hovering ? 'pointer' : 'grab'}
          onLoad={syncView}
          onMoveEnd={syncView}
          onClick={onClick}
          onMouseEnter={() => setHovering(true)}
          onMouseLeave={() => setHovering(false)}
        >
          <NavigationControl position="top-left" />

          {OVERLAYS.map((def) => {
            const q = overlayQueries[def.id];
            if (!overlays[def.id] || !q.data) return null;
            const fc = { type: 'FeatureCollection' as const, features: q.data.features };
            return (
              <Source key={def.id} id={`hz-${def.id}`} type="geojson" data={fc}>
                <Layer {...overlayFillLayer(def, q.data.legend)} />
                <Layer {...overlayLineLayer(def, q.data.legend)} />
              </Source>
            );
          })}

          <Source
            id={POINTS_SOURCE}
            type="geojson"
            data={pointsData}
            cluster={true}
            clusterMaxZoom={11}
            clusterRadius={40}
          >
            <Layer {...pointLayer} />
            <Layer {...clusterLayer} />
            <Layer {...clusterCountLayer} />
          </Source>

          {clicked && (
            <Popup
              longitude={clicked.lon}
              latitude={clicked.lat}
              onClose={() => setClicked(null)}
              closeOnClick={false}
              maxWidth="380px"
            >
              <PointAssessmentCard
                lon={clicked.lon}
                lat={clicked.lat}
                data={pointQ.data}
                isLoading={pointQ.isLoading}
                error={pointQ.error}
              />
            </Popup>
          )}

          {selected && (
            <Popup
              longitude={selected.lon}
              latitude={selected.lat}
              anchor="bottom"
              onClose={() => setSelected(null)}
              closeOnClick={false}
              maxWidth="300px"
            >
              <div className="min-w-[220px] p-3 text-xs text-slate-200">
                <div className="text-sm font-semibold text-slate-100">{selected.address ?? `Property #${selected.id}`}</div>
                <div className="text-slate-400">
                  {selected.county ?? 'Unknown county'} · #{selected.id}
                </div>
                <dl className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1">
                  <dt className="text-slate-400">Screening index</dt>
                  <dd className="tabular text-right">{fmtScore(selected.composite_score)}</dd>
                  <dt className="text-slate-400">Wildfire FHSZ</dt>
                  <dd className="text-right">
                    <WildfireBadge value={selected.wildfire_fhsz} />
                  </dd>
                  <dt className="text-slate-400">Flood zone</dt>
                  <dd className="text-right">
                    <FloodZoneBadge value={selected.flood_zone} />
                  </dd>
                  <dt className="text-slate-400">PGA 2%/50yr</dt>
                  <dd className="tabular text-right">{fmtNum(selected.pga_2pc50_g, 3)} g</dd>
                </dl>
                <Link to={`/properties/${selected.id}`} className="mt-2 inline-block text-teal-300 hover:underline">
                  Open property detail →
                </Link>
              </div>
            </Popup>
          )}
        </Map>

        {/* Status overlay */}
        <div className="pointer-events-none absolute bottom-6 left-2 flex flex-col gap-1 text-xs">
          <div className="pointer-events-auto rounded border border-slate-700 bg-slate-900/90 px-2 py-1 text-slate-300">
            Zoom {zoom.toFixed(1)} · {pointsEnabled ? `${fmtInt(nPoints)} points loaded` : `zoom to ${MIN_POINT_ZOOM}+ to load points`} · click anywhere to assess that spot
            {pointsQ.isFetching && pointsEnabled && <span className="ml-2 text-teal-300">loading…</span>}
          </div>
          {nPoints >= POINT_LIMIT && (
            <div className="pointer-events-auto rounded border border-amber-500/40 bg-amber-500/10 px-2 py-1 text-amber-200">
              Showing the first {fmtInt(POINT_LIMIT)} points in view; zoom in to see all.
            </div>
          )}
        </div>

        <button
          type="button"
          onClick={() => setPanelOpen((o) => !o)}
          className="btn-secondary absolute right-2 top-2 text-xs lg:hidden"
        >
          {panelOpen ? 'Hide panel' : 'Show panel'}
        </button>
      </div>

      <aside
        className={
          (panelOpen ? 'flex' : 'hidden lg:flex') +
          ' max-h-[45vh] w-full shrink-0 flex-col gap-4 overflow-y-auto border-t border-slate-800 bg-slate-900 p-4 lg:max-h-none lg:w-80 lg:border-l lg:border-t-0'
        }
      >
        <div>
          <h1 className="text-base font-semibold">Hazard map</h1>
          <p className="mt-1 text-xs text-slate-400">
            Property points cluster by zoom level. Hazard overlays are fetched for the current view and only at zoom{' '}
            {MIN_OVERLAY_ZOOM}+.
          </p>
        </div>

        {pointsQ.isError && <ErrorState compact error={pointsQ.error} onRetry={() => void pointsQ.refetch()} />}

        <Field label="County filter" hint={countiesQ.isError ? 'County list unavailable' : undefined}>
          <Select
            value={county}
            onChange={setCounty}
            options={countyOptions}
            placeholder={countiesQ.isLoading ? 'Loading counties…' : 'All counties'}
            disabled={countiesQ.isLoading}
          />
        </Field>

        <Field label="Colour points by">
          <Select value={colorBy} onChange={(v) => setColorBy(v as PointColorBy)} options={POINT_COLOR_OPTIONS} />
        </Field>

        <LegendBox
          title="Point legend"
          items={pointLegend(colorBy).map((l) => ({ ...l, shape: 'circle' as const }))}
          note={colorBy === 'composite_score' ? 'Screening index (0-100), not a probability.' : undefined}
        />

        <div className="space-y-3">
          <div className="text-xs font-semibold uppercase tracking-wide text-slate-400">Hazard overlays</div>
          {!overlaysEnabled && (
            <div className="rounded border border-slate-700 bg-slate-950/60 px-2 py-1.5 text-xs text-slate-400">
              Zoom in to level {MIN_OVERLAY_ZOOM}+ to load hazard polygons (current {zoom.toFixed(1)}).
            </div>
          )}
          {OVERLAYS.map((def) => {
            const q = overlayQueries[def.id];
            const cat = catalogueQ.data?.find((h) => h.hazard_id === def.id);
            const on = overlays[def.id];
            return (
              <div key={def.id} className="rounded-md border border-slate-800 bg-slate-950/40 p-2">
                <Toggle
                  checked={on}
                  onChange={(v) => setOverlays((o) => ({ ...o, [def.id]: v }))}
                  label={def.label}
                  color={def.fallback}
                />
                {cat && (
                  <div className="mt-1 text-[11px] text-slate-500">
                    {cat.units ? `Units: ${cat.units}` : 'Categorical'}
                    {cat.probability_horizon ? ` · ${cat.probability_horizon}` : ''}
                  </div>
                )}
                {on && overlaysEnabled && q.isFetching && <div className="mt-1 text-[11px] text-teal-300">Loading…</div>}
                {on && q.isError && <ErrorState compact className="mt-2" error={q.error} onRetry={() => void q.refetch()} />}
                {on && q.data && (
                  <div className="mt-2 space-y-2">
                    {q.data.legend.length > 0 ? (
                      <LegendBox
                        items={q.data.legend.map((l) => ({ label: l.label, color: l.color || def.fallback }))}
                        note={`Source: ${q.data.source_id} · ${fmtInt(q.data.features.length)} features`}
                      />
                    ) : (
                      <div className="text-[11px] text-slate-500">
                        {fmtInt(q.data.features.length)} features · source {q.data.source_id}
                      </div>
                    )}
                    {q.data.truncated && (
                      <CaveatBanner className="!px-2 !py-1.5 text-xs">
                        Layer truncated by the API for this view; zoom in for complete coverage.
                      </CaveatBanner>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>

        <div className="mt-auto space-y-1 text-[11px] text-slate-500">
          <div>
            <Badge tone="amber">Not real-time</Badge>
          </div>
          <div>Layers reflect the last ingestion run. Points limited to {fmtInt(POINT_LIMIT)} per request.</div>
        </div>
      </aside>
    </div>
  );
}
