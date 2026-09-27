"""
Regenera el archivo openapi.json a partir del contrato actual de la API.

Uso:
    python export_openapi.py

No levanta un servidor: construye la app de FastAPI en memoria (con la
misma configuración que usa lifespan para no tocar la base de datos real,
salvo la creación de tablas si el motor apunta a un archivo existente) y
vuelca su especificación OpenAPI a JSON. Se recomienda correr esto después
de cualquier cambio en los schemas o en las rutas, para que el contrato
publicado en este archivo no quede desactualizado.
"""
import json

from app.main import app

if __name__ == "__main__":
    with open("openapi.json", "w", encoding="utf-8") as f:
        json.dump(app.openapi(), f, indent=2, ensure_ascii=False)
    print("openapi.json actualizado.")
