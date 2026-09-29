"""
Regenera el archivo openapi.json a partir del contrato actual de la API.

Uso:
    python export_openapi.py

No levanta un servidor: construye la app de FastAPI en memoria y vuelca su
especificación OpenAPI a JSON. Se recomienda correr esto después de
cualquier cambio en los schemas o en las rutas, para que el contrato
publicado en este archivo no quede desactualizado.
"""
import json
import os

# La app importa app.core.jwt_auth, que exige AUTH_SECRET_KEY al cargarse.
# Para generar el contrato OpenAPI no hace falta una clave real: se usa un
# placeholder solo si no está definida (en Docker ya viene del entorno).
os.environ.setdefault("AUTH_SECRET_KEY", "placeholder-solo-para-generar-openapi")

from app.main import app  # noqa: E402

if __name__ == "__main__":
    with open("openapi.json", "w", encoding="utf-8") as f:
        json.dump(app.openapi(), f, indent=2, ensure_ascii=False)
    print("openapi.json actualizado.")