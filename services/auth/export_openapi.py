"""
Regenera el archivo openapi.json a partir del contrato actual de la API de
auth. Ver services/productos/export_openapi.py para el mismo patrón.

Uso:
    AUTH_SECRET_KEY=cualquier-valor python export_openapi.py
"""
import json
import os

os.environ.setdefault("AUTH_SECRET_KEY", "valor-temporal-solo-para-generar-el-contrato")

from app.main import app

if __name__ == "__main__":
    with open("openapi.json", "w", encoding="utf-8") as f:
        json.dump(app.openapi(), f, indent=2, ensure_ascii=False)
    print("openapi.json actualizado.")
