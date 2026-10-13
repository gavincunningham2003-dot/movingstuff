import type {
  CountyCountOut,
  DataQualityOut,
  DataSourceOut,
  HazardCatalogueOut,
  HazardLayerFeatureCollection,
  HealthResponse,
  ModelVersionSummary,
  ModelsMetricsResponse,
  PagedProperties,
  PointAssessmentOut,
  PortfolioAnalysisOut,
  PortfolioStatusOut,
  PortfolioSummaryOut,
  PortfolioUploadResponse,
  PropertyDetailOut,
  PropertyFeatureCollection,
} from './types';

/** Base URL for the API; empty string means same-origin (dev proxy / nginx). */
export const API_BASE_URL: string = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/+$/, '');

export const API_PREFIX = '/api/v1';

export class ApiError extends Error {
  readonly status: number;
  readonly url: string;
  readonly body: unknown;

  constructor(message: string, status: number, url: string, body?: unknown) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.url = url;
    this.body = body;
  }
}

export type QueryParams = Record<string, string | number | boolean | null | undefined>;

export function buildUrl(path: string, params?: QueryParams): string {
  const url = `${API_BASE_URL}${API_PREFIX}${path}`;
  if (!params) return url;
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === '') continue;
    qs.set(k, String(v));
  }
  const s = qs.toString();
  return s ? `${url}?${s}` : url;
}

function extractMessage(body: unknown, fallback: string): string {
  if (body && typeof body === 'object') {
    const b = body as { detail?: unknown; message?: unknown; error?: unknown };
    const d = b.detail ?? b.message ?? b.error;
    if (typeof d === 'string') return d;
    if (Array.isArray(d)) {
      // FastAPI validation errors: [{loc, msg, type}]
      const msgs = d
        .map((e) => (e && typeof e === 'object' && 'msg' in e ? String((e as { msg: unknown }).msg) : null))
        .filter((m): m is string => !!m);
      if (msgs.length) return msgs.join('; ');
    }
  }
  return fallback;
}

async function request<T>(path: string, init?: RequestInit, params?: QueryParams): Promise<T> {
  const url = buildUrl(path, params);
  let res: Response;
  try {
    res = await fetch(url, {
      ...init,
      headers: {
        Accept: 'application/json',
        ...(init?.headers ?? {}),
      },
    });
  } catch (err) {
    const msg = err instanceof Error ? err.message : 'Network error';
    throw new ApiError(`Could not reach the API (${msg})`, 0, url);
  }

  const contentType = res.headers.get('content-type') ?? '';
  let body: unknown = null;
  if (res.status !== 204) {
    if (contentType.includes('application/json') || contentType.includes('geo+json')) {
      try {
        body = await res.json();
      } catch {
        body = null;
      }
    } else {
      try {
        body = await res.text();
      } catch {
        body = null;
      }
    }
  }

  if (!res.ok) {
    const fallback = `${res.status} ${res.statusText || 'Request failed'}`;
    throw new ApiError(extractMessage(body, fallback), res.status, url, body);
  }
  return body as T;
}

function get<T>(path: string, params?: QueryParams, signal?: AbortSignal): Promise<T> {
  return request<T>(path, { method: 'GET', signal }, params);
}

// --------------------------------------------------------------------------- endpoints

export const api = {
  health: (signal?: AbortSignal) => get<HealthResponse>('/health', undefined, signal),

  portfolioSummary: (signal?: AbortSignal) =>
    get<PortfolioSummaryOut>('/portfolio/summary', undefined, signal),

  modelsMetrics: (version?: string, signal?: AbortSignal) =>
    get<ModelsMetricsResponse>('/models/metrics', version ? { version } : undefined, signal),
  modelVersions: (signal?: AbortSignal) => get<ModelVersionSummary[]>('/models/versions', undefined, signal),

  hazardsCatalogue: (signal?: AbortSignal) => get<HazardCatalogueOut[]>('/hazards', undefined, signal),

  pointAssessment: (lon: number, lat: number, signal?: AbortSignal) =>
    get<PointAssessmentOut>('/hazards/point', { lon: lon.toFixed(6), lat: lat.toFixed(6) }, signal),
  hazardGeojson: (hazardId: string, bbox: string, zoom: number, signal?: AbortSignal) =>
    get<HazardLayerFeatureCollection>(
      `/hazards/${encodeURIComponent(hazardId)}/geojson`,
      { bbox, zoom: Math.round(zoom) },
      signal,
    ),

  properties: (
    params: { q?: string; county?: string; limit?: number; offset?: number },
    signal?: AbortSignal,
  ) => get<PagedProperties>('/properties', params, signal),

  property: (id: number | string, signal?: AbortSignal) =>
    get<PropertyDetailOut>(`/properties/${encodeURIComponent(String(id))}`, undefined, signal),

  propertiesGeojson: (
    params: { bbox: string; limit?: number; county?: string },
    signal?: AbortSignal,
  ) => get<PropertyFeatureCollection>('/properties/geojson', params, signal),

  propertyCounties: (signal?: AbortSignal) => get<CountyCountOut[]>('/properties/counties', undefined, signal),

  portfolios: (signal?: AbortSignal) => get<PortfolioStatusOut[]>('/portfolio', undefined, signal),

  portfolioStatus: (id: string, signal?: AbortSignal) =>
    get<PortfolioStatusOut>(`/portfolio/${encodeURIComponent(id)}`, undefined, signal),

  portfolioAnalysis: (id: string, signal?: AbortSignal) =>
    get<PortfolioAnalysisOut>(`/portfolio/${encodeURIComponent(id)}/analysis`, undefined, signal),

  uploadPortfolio: (file: File, name?: string): Promise<PortfolioUploadResponse> => {
    const form = new FormData();
    form.append('file', file, file.name);
    if (name && name.trim()) form.append('name', name.trim());
    return request<PortfolioUploadResponse>('/portfolio/upload', { method: 'POST', body: form });
  },

  dataSources: (signal?: AbortSignal) => get<DataSourceOut[]>('/data-sources', undefined, signal),

  dataQuality: (signal?: AbortSignal) => get<DataQualityOut>('/data-quality', undefined, signal),
};

/** Absolute URLs for links that open in a new tab / trigger downloads. */
export const apiLinks = {
  portfolioExport: (id: string, format: 'csv' | 'json') =>
    buildUrl(`/portfolio/${encodeURIComponent(id)}/export`, { format }),
  exampleCsv: () => buildUrl('/portfolio/example.csv'),
  docs: () => `${API_BASE_URL}/docs`,
};

export function isApiError(err: unknown): err is ApiError {
  return err instanceof ApiError;
}

export function errorMessage(err: unknown): string {
  if (isApiError(err)) return err.status ? `${err.status}: ${err.message}` : err.message;
  if (err instanceof Error) return err.message;
  return 'Unknown error';
}
