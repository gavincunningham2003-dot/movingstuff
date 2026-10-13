import type { StyleSpecification } from 'maplibre-gl';

/**
 * Free CARTO dark raster basemap (no API key).
 * MapLibre uses `{ratio}` (expands to "@2x" on high-DPI screens) rather than
 * Leaflet's `{r}` placeholder. `glyphs` is required for symbol layers with
 * text (cluster count labels); the MapLibre demo glyph server is public.
 */
export const DARK_BASEMAP: StyleSpecification = {
  version: 8,
  name: 'CARTO Dark Matter',
  glyphs: 'https://demotiles.maplibre.org/font/{fontstack}/{range}.pbf',
  sources: {
    carto: {
      type: 'raster',
      tiles: ['https://basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{ratio}.png'],
      tileSize: 256,
      attribution: '© OpenStreetMap contributors © CARTO',
      maxzoom: 19,
    },
  },
  layers: [
    {
      id: 'carto-dark',
      type: 'raster',
      source: 'carto',
      minzoom: 0,
      maxzoom: 22,
    },
  ],
};

export const LABEL_FONT = ['Open Sans Semibold'];

export const CALIFORNIA_VIEW = { longitude: -119.5, latitude: 37.3, zoom: 5.5 };

/** Serialise bounds to the API bbox string "minLon,minLat,maxLon,maxLat". */
export function bboxString(west: number, south: number, east: number, north: number): string {
  const f = (n: number) => n.toFixed(5);
  return `${f(west)},${f(south)},${f(east)},${f(north)}`;
}
