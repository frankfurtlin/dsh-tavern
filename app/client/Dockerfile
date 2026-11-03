FROM node:22.21.1-alpine3.22 AS build
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . ./
ARG VITE_PUBLIC_ORIGIN=
ENV VITE_PUBLIC_ORIGIN=$VITE_PUBLIC_ORIGIN
ARG VITE_RELEASE=
ENV VITE_RELEASE=$VITE_RELEASE
RUN npm run typecheck && npm run build
FROM nginx:1.27.5-alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
HEALTHCHECK --interval=5s --timeout=3s CMD wget -q -O /dev/null http://127.0.0.1/health/ready || exit 1
