# CATRIX frontend

React 18 + TypeScript + Vite single-page app for the CATRIX California catastrophe
risk intelligence API (`/api/v1`). Dark-mode insurance analytics UI with MapLibre maps,
Recharts charts and TanStack Query data fetching.

## Pages

| Route | Page |
| --- | --- |
| `/` | Executive overview (portfolio summary + model performance) |
| `/map` | Hazard map (clustered property points, hazard overlays) |
| `/properties`, `/properties/:id` | Property explorer and detail |
| `/models` | Model analytics (ROC / PR / calibration, SHAP, geography) |
| `/portfolio` | Portfolio CSV upload and analysis |
| `/data-quality` | Dataset provenance and quality checks |

## Develop

```bash
npm install
cp .env.example .env      # optional; empty VITE_API_BASE_URL uses the dev proxy
npm run dev               # http://localhost:5173, /api proxied to http://localhost:8000
```

## Build

```bash
npm run build             # tsc -b && vite build  -> dist/
npm run preview
```

## Docker

The multi-stage `Dockerfile` builds the bundle with `node:20-alpine` and serves it
with `nginx:alpine`. `nginx.conf` proxies `/api/` to the `api` service
(`http://api:8000`) and falls back to `index.html` for client-side routes.
Build with an empty `VITE_API_BASE_URL` so the app talks to the same origin and
nginx does the proxying.

## Notes

- All data comes from the API; the UI renders explicit empty states when the ingestion
  pipeline has not run.
- The composite score is a 0-100 screening index, never a probability or premium, and
  the UI labels it that way everywhere.
- Nothing shown is real-time: every dataset carries its ingestion timestamp.
