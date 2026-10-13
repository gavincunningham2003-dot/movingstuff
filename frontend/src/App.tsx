import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useState } from 'react';
import { BrowserRouter, Link, Route, Routes } from 'react-router-dom';

import { AppShell } from './components/layout/AppShell';
import { PageBody } from './components/layout/PageBody';
import { DataQuality } from './pages/DataQuality';
import { ExecutiveOverview } from './pages/ExecutiveOverview';
import { HazardMap } from './pages/HazardMap';
import { ModelAnalytics } from './pages/ModelAnalytics';
import { PortfolioAnalysis } from './pages/PortfolioAnalysis';
import { PropertyDetail } from './pages/PropertyDetail';
import { PropertyExplorer } from './pages/PropertyExplorer';

function NotFound() {
  return (
    <PageBody>
      <h1 className="text-xl font-semibold">Page not found</h1>
      <p className="mt-2 text-sm text-slate-400">
        Nothing is routed here.{' '}
        <Link to="/" className="text-teal-300 hover:underline">
          Back to the executive overview
        </Link>
        .
      </p>
    </PageBody>
  );
}

function makeQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: 1,
        refetchOnWindowFocus: false,
        staleTime: 30_000,
      },
    },
  });
}

export function App() {
  const [queryClient] = useState(makeQueryClient);

  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route element={<AppShell />}>
            <Route index element={<ExecutiveOverview />} />
            <Route path="/map" element={<HazardMap />} />
            <Route path="/properties" element={<PropertyExplorer />} />
            <Route path="/properties/:id" element={<PropertyDetail />} />
            <Route path="/models" element={<ModelAnalytics />} />
            <Route path="/portfolio" element={<PortfolioAnalysis />} />
            <Route path="/data-quality" element={<DataQuality />} />
            <Route path="*" element={<NotFound />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
