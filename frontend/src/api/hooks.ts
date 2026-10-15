import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { api } from './client';
import type { PortfolioStatusOut, PortfolioUploadResponse } from './types';

export const queryKeys = {
  health: ['health'] as const,
  portfolioSummary: ['portfolio', 'summary'] as const,
  modelsMetrics: ['models', 'metrics'] as const,
  hazardsCatalogue: ['hazards', 'catalogue'] as const,
  hazardGeojson: (hazardId: string, bbox: string, zoom: number) =>
    ['hazards', hazardId, 'geojson', bbox, zoom] as const,
  properties: (q: string, county: string, limit: number, offset: number) =>
    ['properties', 'list', q, county, limit, offset] as const,
  property: (id: string) => ['properties', 'detail', id] as const,
  propertiesGeojson: (bbox: string, county: string, limit: number) =>
    ['properties', 'geojson', bbox, county, limit] as const,
  propertyCounties: ['properties', 'counties'] as const,
  portfolios: ['portfolio', 'list'] as const,
  csvContract: ['portfolio', 'csv-contract'] as const,
  stressTests: (id: string, bufferM: number) => ['portfolio', 'stress-tests', id, bufferM] as const,
  stressTest: (id: string, eventId: number, ratio: number, bufferM: number) =>
    ['portfolio', 'stress-test', id, eventId, ratio, bufferM] as const,
  portfolioStatus: (id: string) => ['portfolio', 'status', id] as const,
  portfolioAnalysis: (id: string) => ['portfolio', 'analysis', id] as const,
  dataSources: ['data-sources'] as const,
  dataQuality: ['data-quality'] as const,
};

export function useHealth() {
  return useQuery({
    queryKey: queryKeys.health,
    queryFn: ({ signal }) => api.health(signal),
    refetchInterval: 30_000,
    retry: false,
    staleTime: 10_000,
  });
}

export function usePortfolioSummary() {
  return useQuery({
    queryKey: queryKeys.portfolioSummary,
    queryFn: ({ signal }) => api.portfolioSummary(signal),
  });
}

export function useModelsMetrics(version?: string) {
  return useQuery({
    queryKey: [...queryKeys.modelsMetrics, version ?? 'latest'] as const,
    queryFn: ({ signal }) => api.modelsMetrics(version, signal),
  });
}

export function useModelVersions() {
  return useQuery({
    queryKey: ['models', 'versions'] as const,
    queryFn: ({ signal }) => api.modelVersions(signal),
    staleTime: 60 * 1000,
  });
}

export function useHazardsCatalogue() {
  return useQuery({
    queryKey: queryKeys.hazardsCatalogue,
    queryFn: ({ signal }) => api.hazardsCatalogue(signal),
    staleTime: 10 * 60_000,
  });
}

export function useHazardGeojson(hazardId: string, bbox: string | null, zoom: number, enabled: boolean) {
  const z = Math.round(zoom);
  return useQuery({
    queryKey: queryKeys.hazardGeojson(hazardId, bbox ?? '', z),
    queryFn: ({ signal }) => api.hazardGeojson(hazardId, bbox ?? '', z, signal),
    enabled: enabled && !!bbox,
    placeholderData: keepPreviousData,
    staleTime: 5 * 60_000,
  });
}

export function useProperties(q: string, county: string, limit = 25, offset = 0, enabled = true) {
  return useQuery({
    queryKey: queryKeys.properties(q, county, limit, offset),
    queryFn: ({ signal }) =>
      api.properties({ q: q || undefined, county: county || undefined, limit, offset }, signal),
    enabled,
    placeholderData: keepPreviousData,
  });
}

export function useProperty(id: string | undefined) {
  return useQuery({
    queryKey: queryKeys.property(id ?? ''),
    queryFn: ({ signal }) => api.property(id ?? '', signal),
    enabled: !!id,
  });
}

export function usePropertiesGeojson(bbox: string | null, county: string, limit = 5000, enabled = true) {
  return useQuery({
    queryKey: queryKeys.propertiesGeojson(bbox ?? '', county, limit),
    queryFn: ({ signal }) =>
      api.propertiesGeojson({ bbox: bbox ?? '', limit, county: county || undefined }, signal),
    enabled: enabled && !!bbox,
    placeholderData: keepPreviousData,
    staleTime: 60_000,
  });
}

export function usePropertyCounties() {
  return useQuery({
    queryKey: queryKeys.propertyCounties,
    queryFn: ({ signal }) => api.propertyCounties(signal),
    staleTime: 10 * 60_000,
  });
}

export function useStressTests(portfolioId: string | null, bufferM: number) {
  return useQuery({
    queryKey: queryKeys.stressTests(portfolioId ?? '', bufferM),
    queryFn: ({ signal }) => api.stressTests(portfolioId as string, bufferM, signal),
    enabled: !!portfolioId,
    staleTime: 5 * 60_000,
  });
}

export function useStressTest(portfolioId: string | null, eventId: number | null, damageRatio: number, bufferM: number) {
  return useQuery({
    queryKey: queryKeys.stressTest(portfolioId ?? '', eventId ?? 0, damageRatio, bufferM),
    queryFn: ({ signal }) => api.stressTest(portfolioId as string, eventId as number, damageRatio, bufferM, signal),
    enabled: !!portfolioId && eventId != null,
    placeholderData: keepPreviousData,
    staleTime: 5 * 60_000,
  });
}

export function useCsvContract() {
  return useQuery({
    queryKey: queryKeys.csvContract,
    queryFn: ({ signal }) => api.csvContract(signal),
    staleTime: Infinity,
  });
}

export function usePortfolios() {
  return useQuery({
    queryKey: queryKeys.portfolios,
    queryFn: ({ signal }) => api.portfolios(signal),
  });
}

function isTerminal(status: PortfolioStatusOut['status'] | undefined): boolean {
  return status === 'completed' || status === 'failed';
}

/** Polls every 2 s until the portfolio job reaches completed/failed. */
export function usePortfolioStatus(id: string | null) {
  return useQuery({
    queryKey: queryKeys.portfolioStatus(id ?? ''),
    queryFn: ({ signal }) => api.portfolioStatus(id ?? '', signal),
    enabled: !!id,
    refetchInterval: (query) => (isTerminal(query.state.data?.status) ? false : 2000),
    refetchIntervalInBackground: true,
  });
}

export function usePortfolioAnalysis(id: string | null, enabled: boolean) {
  return useQuery({
    queryKey: queryKeys.portfolioAnalysis(id ?? ''),
    queryFn: ({ signal }) => api.portfolioAnalysis(id ?? '', signal),
    enabled: enabled && !!id,
    staleTime: Infinity,
  });
}

export function useUploadPortfolio() {
  const qc = useQueryClient();
  return useMutation<PortfolioUploadResponse, Error, { file: File; name?: string }>({
    mutationFn: ({ file, name }) => api.uploadPortfolio(file, name),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: queryKeys.portfolios });
    },
  });
}

export function useDataSources() {
  return useQuery({
    queryKey: queryKeys.dataSources,
    queryFn: ({ signal }) => api.dataSources(signal),
  });
}

export function useDataQuality() {
  return useQuery({
    queryKey: queryKeys.dataQuality,
    queryFn: ({ signal }) => api.dataQuality(signal),
  });
}

/** On-demand assessment of a clicked coordinate (not persisted server-side). */
export function usePointAssessment(point: { lon: number; lat: number } | null) {
  return useQuery({
    queryKey: ['hazards', 'point', point?.lon ?? null, point?.lat ?? null] as const,
    queryFn: ({ signal }) => api.pointAssessment(point!.lon, point!.lat, signal),
    enabled: point !== null,
    staleTime: 5 * 60 * 1000,
  });
}
