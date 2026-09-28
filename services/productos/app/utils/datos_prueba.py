"""
Generador de productos de prueba (datos "fake" pero realistas), usado por
el endpoint de seed masivo (ver app/routers/seed_router.py).

Combina nombres base, marcas y categorías de juguetería de forma aleatoria
para producir un lote de productos variados, con precios y stocks también
aleatorios dentro de rangos razonables. No pretende ser una librería de
datos de prueba genérica: está pensada específicamente para poblar rápido
la base de este proyecto y facilitar probar el listado, el filtro de stock
bajo, la edición y el borrado con muchos registros.
"""
import random

from app.schemas.producto import ProductoCreate

_NOMBRES_BASE = [
    "Muñeca", "Peluche", "Auto a control remoto", "Set de bloques",
    "Juego de mesa", "Rompecabezas", "Pelota", "Cocina de juguete",
    "Espada de espuma", "Set de té", "Robot transformable", "Bicicleta infantil",
    "Patineta", "Set de plastilina", "Kit de pintura", "Carrito de bebé",
    "Dinosaurio articulado", "Set de herramientas de juguete", "Cometa",
    "Yo-yo", "Trompo", "Set de disfraces", "Muñeco de acción", "Pista de autos",
    "Set de té de porcelana", "Casa de muñecas", "Triciclo", "Set de magia",
]

_MARCAS = [
    "Playkids", "BlockMax", "SoftFriends", "DiverGames", "TurboToys",
    "ImaginaMás", "Kidstar", "JuegaFeliz", "ToyLand", "Brillibrilli",
]

_CATEGORIAS = [
    "Muñecos", "Ladrillitos", "Peluches", "Juegos de mesa", "Varios",
    "Aire libre", "Educativos", "Vehículos", "Arte y manualidades",
]


def generar_productos_prueba(cantidad: int) -> list[ProductoCreate]:
    """
    Genera `cantidad` instancias de ProductoCreate con datos variados.

    - precio_venta: entre 1.000 y 50.000, con dos decimales.
    - stock_actual: entre 0 y 40 (incluye casos con stock 0 y con stock
      por debajo de stock_minimo, para poder probar el indicador de "stock
      bajo" del frontend con datos reales).
    - stock_minimo: entre 2 y 10.
    - nombre: combina un nombre base con un sufijo numérico para que no
      se repitan literalmente si se generan muchos productos.
    """
    productos = []
    for i in range(cantidad):
        nombre_base = random.choice(_NOMBRES_BASE)
        productos.append(
            ProductoCreate(
                nombre=f"{nombre_base} #{random.randint(100, 999)}",
                marca=random.choice(_MARCAS),
                precio_venta=round(random.uniform(1000, 50000), 2),
                stock_actual=random.randint(0, 40),
                stock_minimo=random.randint(2, 10),
                categoria=random.choice(_CATEGORIAS),
            )
        )
    return productos
