#!/bin/sh
# Este script se ejecuta automáticamente al iniciar el contenedor porque la
# imagen oficial de nginx corre todo lo que hay en /docker-entrypoint.d/
# antes de arrancar el servidor (no requiere ser el CMD ni recibir args).
#
# Genera /usr/share/nginx/html/config.js a partir del template, sustituyendo
# variables de entorno. Esto permite cambiar la URL de la API sin reconstruir
# la imagen (por ejemplo, entre entornos de desarrollo y producción).
set -e

: "${PRODUCTOS_API_URL:=http://localhost:8000}"

envsubst '${PRODUCTOS_API_URL}' \
  < /usr/share/nginx/html/config.js.template \
  > /usr/share/nginx/html/config.js
