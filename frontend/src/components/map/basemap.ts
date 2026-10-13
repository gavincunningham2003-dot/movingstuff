/**
 * Free OpenFreeMap "Fiord" dark vector basemap (no API key, no usage cap;
 * https://openfreemap.org). CARTO's raster basemap, used previously, now serves
 * "API KEY REQUIRED" placeholder tiles without a key. The style brings its own
 * glyph server, so label layers must use a font it serves (Noto Sans).
 */
export const DARK_BASEMAP = 'https://tiles.openfreemap.org/styles/fiord';

export const LABEL_FONT = ['Noto Sans Regular'];

export const CALIFORNIA_VIEW = { longitude: -119.5, latitude: 37.3, zoom: 5.5 };

/** California extent (minLon, minLat, maxLon, maxLat). All CATRIX data lies inside it. */
export const CALIFORNIA_BOUNDS = { west: -124.6, south: 32.4, east: -114.0, north: 42.1 };

/**
 * Clip map bounds to the California extent. The API rejects very large bounding boxes,
 * and a zoomed-out view can span half a continent, none of which holds data.
 */
export function clipToCalifornia(west: number, south: number, east: number, north: number) {
  return {
    west: Math.max(west, CALIFORNIA_BOUNDS.west),
    south: Math.max(south, CALIFORNIA_BOUNDS.south),
    east: Math.min(east, CALIFORNIA_BOUNDS.east),
    north: Math.min(north, CALIFORNIA_BOUNDS.north),
  };
}

/** Serialise bounds to the API bbox string "minLon,minLat,maxLon,maxLat". */
export function bboxString(west: number, south: number, east: number, north: number): string {
  const f = (n: number) => n.toFixed(5);
  return `${f(west)},${f(south)},${f(east)},${f(north)}`;
}
