# ---Stage 1: build frontend files---
FROM node:24.22.2 AS builder
WORKDIR /app
COPY frontend/package*.json /app/
# install dependencies
RUN npm ci
COPY frontend/ /app
# ts compiler type check && vite build to create dist/
RUN npm run build

# ---Stage 2: copy the dist/ to nginx---
FROM nginx:1.29.8-alpine
COPY --from=builder /app/dist /data/www
EXPOSE 80
# remove the default.conf in nginx img && copy the cui.conf
RUN rm /etc/nginx/conf.d/default.conf
COPY infra/nginx/cui.conf /etc/nginx/conf.d/

