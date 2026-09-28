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
: "${AUTH_API_URL:=http://localhost:8001}"
: "${HABILITAR_USUARIOS_PRUEBA:=false}"

# Si el acceso rápido no está habilitado, las credenciales de prueba NO se
# escriben en config.js (así no quedan expuestas en un entorno real).
if [ "$HABILITAR_USUARIOS_PRUEBA" != "true" ]; then
  HABILITAR_USUARIOS_PRUEBA=false
  PRUEBA_ADMIN_EMAIL=""
  PRUEBA_ADMIN_PASSWORD=""
  PRUEBA_EMPLEADO_EMAIL=""
  PRUEBA_EMPLEADO_PASSWORD=""
fi
export PRODUCTOS_API_URL AUTH_API_URL HABILITAR_USUARIOS_PRUEBA \
       PRUEBA_ADMIN_EMAIL PRUEBA_ADMIN_PASSWORD \
       PRUEBA_EMPLEADO_EMAIL PRUEBA_EMPLEADO_PASSWORD

envsubst '${PRODUCTOS_API_URL} ${AUTH_API_URL} ${HABILITAR_USUARIOS_PRUEBA} ${PRUEBA_ADMIN_EMAIL} ${PRUEBA_ADMIN_PASSWORD} ${PRUEBA_EMPLEADO_EMAIL} ${PRUEBA_EMPLEADO_PASSWORD}' \
  < /usr/share/nginx/html/config.js.template \
  > /usr/share/nginx/html/config.js
