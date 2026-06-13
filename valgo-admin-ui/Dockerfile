# Multi-stage build: compile React app, serve with nginx
FROM node:20-alpine AS builder
WORKDIR /app
COPY package*.json ./
# Use `npm install` until a package-lock.json is committed (then switch to `npm ci`).
RUN npm install --no-audit --no-fund --loglevel=error
COPY . .
RUN npm run build

FROM nginx:alpine
COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
