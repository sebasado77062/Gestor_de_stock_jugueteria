# syntax=docker/dockerfile:1
FROM nginx:1.27-alpine

# Config del servidor y del sitio estático
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY index.html /usr/share/nginx/html/index.html
COPY app.js /usr/share/nginx/html/app.js
COPY config.js.template /usr/share/nginx/html/config.js.template

# Entrypoint que genera config.js con la URL de la API en runtime
COPY docker-entrypoint.sh /docker-entrypoint.d/40-generate-config.sh
RUN chmod +x /docker-entrypoint.d/40-generate-config.sh

EXPOSE 80

HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=5 \
    CMD wget -qO- http://localhost/healthz || exit 1
