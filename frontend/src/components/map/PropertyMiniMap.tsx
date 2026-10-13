import 'maplibre-gl/dist/maplibre-gl.css';

import Map, { Marker, NavigationControl } from 'react-map-gl/maplibre';

import { DARK_BASEMAP } from './basemap';

interface PropertyMiniMapProps {
  lat: number;
  lon: number;
  color?: string;
  zoom?: number;
  className?: string;
}

export function PropertyMiniMap({ lat, lon, color = '#2dd4bf', zoom = 12, className }: PropertyMiniMapProps) {
  return (
    <div className={className ?? 'h-64 w-full'}>
      <Map
        initialViewState={{ longitude: lon, latitude: lat, zoom }}
        mapStyle={DARK_BASEMAP}
        style={{ width: '100%', height: '100%' }}
      >
        <NavigationControl position="top-right" showCompass={false} />
        <Marker longitude={lon} latitude={lat} anchor="center">
          <span
            className="block h-4 w-4 rounded-full border-2 border-white shadow"
            style={{ backgroundColor: color }}
          />
        </Marker>
      </Map>
    </div>
  );
}
