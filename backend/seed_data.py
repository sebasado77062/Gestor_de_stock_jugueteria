"""
Script opcional para precargar la base de datos con productos de ejemplo.
Útil para el live testing (mostrar el GET con datos ya cargados).

Ejecutar desde la carpeta backend/:
    python seed_data.py
"""
from app.database.db import Base, engine, SessionLocal
from app.models.producto import Producto

Base.metadata.create_all(bind=engine)

productos_ejemplo = [
    {"nombre": "Muñeca Bebota", "marca": "Playkids", "precio_venta": 15999.90,
     "stock_actual": 12, "stock_minimo": 5, "categoria": "Muñecos"},
    {"nombre": "Set Bloques de Construcción x200", "marca": "BlockMax", "precio_venta": 22500.00,
     "stock_actual": 8, "stock_minimo": 4, "categoria": "Ladrillitos"},
    {"nombre": "Peluche Oso 40cm", "marca": "SoftFriends", "precio_venta": 9800.00,
     "stock_actual": 3, "stock_minimo": 5, "categoria": "Peluches"},
    {"nombre": "Juego de Mesa - Carrera Loca", "marca": "DiverGames", "precio_venta": 13200.50,
     "stock_actual": 20, "stock_minimo": 6, "categoria": "Juegos de mesa"},
    {"nombre": "Auto a Control Remoto 4x4", "marca": "TurboToys", "precio_venta": 34990.00,
     "stock_actual": 6, "stock_minimo": 3, "categoria": "Varios"},
]

db = SessionLocal()
try:
    if db.query(Producto).count() == 0:
        for datos in productos_ejemplo:
            db.add(Producto(**datos))
        db.commit()
        print(f"Se cargaron {len(productos_ejemplo)} productos de ejemplo.")
    else:
        print("La base de datos ya tiene productos cargados. No se modificó nada.")
finally:
    db.close()
