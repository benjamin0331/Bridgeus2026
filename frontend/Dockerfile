FROM node:22-alpine AS build

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .
RUN npm run build

FROM nginx:1.27-alpine

ENV BACKEND_HOST=host.docker.internal
ENV BACKEND_PORT=8005
ENV GODOT_WS_HOST=host.docker.internal
ENV GODOT_WS_PORT=8085

COPY nginx/default.conf.template /etc/nginx/templates/default.conf.template
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80
