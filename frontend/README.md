# Cui Frontend

The web UI for browsing logs. It has one page: filters for service, environment and level, a table of log events, and a pager. The design is in [design/ui-design.md](../design/ui-design.md).

Built with React, TypeScript and Vite.

## Run it locally

The page reads from query-api, so start query-api on `localhost:8001` first. Then:

```bash
npm ci
npm run dev
```

Vite proxies every `/api` request to `localhost:8001` (see `vite.config.ts`), so the page and the API look like the same origin, same as behind nginx.

## How it is served in Docker Compose

There is no separate frontend container. The nginx image builds the page in a Node stage (`npm run build` → `dist/`) and copies the output into nginx, which serves it as static files at `/` (see `infra/nginx/Dockerfile`). `docker compose up` from the repo root is all you need.
