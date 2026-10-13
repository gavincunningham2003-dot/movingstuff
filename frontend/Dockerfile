# ---- build stage -----------------------------------------------------------
FROM node:20-alpine AS build
WORKDIR /app

# VITE_API_BASE_URL is baked into the bundle at build time. Leave it empty
# (the default) so the app uses same-origin /api requests, which nginx proxies
# to the API container. docker-compose passes http://localhost:8000 explicitly.
ARG VITE_API_BASE_URL=
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}

COPY package.json package-lock.json* ./
RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi

COPY . .
RUN npm run build

# ---- serve stage -----------------------------------------------------------
FROM nginx:alpine AS serve
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
HEALTHCHECK --interval=30s --timeout=3s CMD wget -qO- http://localhost/ >/dev/null || exit 1
CMD ["nginx", "-g", "daemon off;"]
