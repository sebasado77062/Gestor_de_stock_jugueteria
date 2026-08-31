/**
 * Frontend simple (vanilla JS) para probar el CRUD de Producto.
 * Consume la API RESTful del backend FastAPI.
 *
 * Si el backend corre en otro host/puerto, cambiar API_BASE_URL.
 */
const API_BASE_URL = "http://127.0.0.1:8000/api/v1/productos";

const form = document.getElementById("form-producto");
const tabla = document.getElementById("tabla-productos");
const estadoGlobal = document.getElementById("estado-global");
const indicadorConexion = document.getElementById("indicador-conexion");
const tituloForm = document.getElementById("titulo-form");
const btnGuardar = document.getElementById("btn-guardar");
const btnCancelar = document.getElementById("btn-cancelar");
const mensajeCancelar = document.getElementById("mensaje-cancelar");

const campoId = document.getElementById("id_producto");
const campoNombre = document.getElementById("nombre");
const campoMarca = document.getElementById("marca");
const campoCategoria = document.getElementById("categoria");
const campoPrecio = document.getElementById("precio_venta");
const campoStockActual = document.getElementById("stock_actual");
const campoStockMinimo = document.getElementById("stock_minimo");

function mostrarEstado(mensaje, tipo = "ok") {
  estadoGlobal.textContent = mensaje;
  estadoGlobal.className = `estado ${tipo}`;
  setTimeout(() => {
    estadoGlobal.className = "estado";
  }, 4000);
}

function formatearPrecio(valor) {
  return valor.toLocaleString("es-AR", { style: "currency", currency: "ARS" });
}

function limpiarFormulario() {
  form.reset();
  campoId.value = "";
  tituloForm.textContent = "➕ Nuevo producto";
  btnGuardar.textContent = "Guardar producto";
  btnCancelar.style.display = "none";
  mensajeCancelar.textContent = "";
}

async function cargarProductos() {
  try {
    const resp = await fetch(API_BASE_URL);
    if (!resp.ok) throw new Error("No se pudo obtener el listado de productos.");
    const productos = await resp.json();
    indicadorConexion.textContent = "API conectada";
    indicadorConexion.className = "etiqueta stock-ok";
    renderizarTabla(productos);
  } catch (error) {
    indicadorConexion.textContent = "API no disponible";
    indicadorConexion.className = "etiqueta stock-bajo";
    tabla.innerHTML = `<tr><td colspan="7" class="vacio">No se pudo conectar con la API. Verificá que el backend esté corriendo en ${API_BASE_URL}.</td></tr>`;
  }
}

function renderizarTabla(productos) {
  if (!productos.length) {
    tabla.innerHTML = `<tr><td colspan="7" class="vacio">Todavía no hay productos cargados.</td></tr>`;
    return;
  }

  tabla.innerHTML = productos.map((prod) => {
    const stockBajo = prod.stock_actual <= prod.stock_minimo;
    const etiquetaStock = stockBajo
      ? `<span class="etiqueta stock-bajo">${prod.stock_actual} (mín. ${prod.stock_minimo})</span>`
      : `<span class="etiqueta stock-ok">${prod.stock_actual}</span>`;

    return `
      <tr>
        <td>${prod.id_producto}</td>
        <td>${escaparHtml(prod.nombre)}</td>
        <td>${escaparHtml(prod.marca || "—")}</td>
        <td>${escaparHtml(prod.categoria || "—")}</td>
        <td>${formatearPrecio(prod.precio_venta)}</td>
        <td>${etiquetaStock}</td>
        <td class="col-acciones">
          <button class="btn-secundario btn-chico" onclick="editarProducto(${prod.id_producto})">Editar</button>
          <button class="btn-peligro btn-chico" onclick="eliminarProducto(${prod.id_producto})">Eliminar</button>
        </td>
      </tr>
    `;
  }).join("");
}

function escaparHtml(texto) {
  const div = document.createElement("div");
  div.textContent = texto;
  return div.innerHTML;
}

async function editarProducto(id) {
  try {
    const resp = await fetch(`${API_BASE_URL}/${id}`);
    if (!resp.ok) throw new Error("Producto no encontrado.");
    const prod = await resp.json();

    campoId.value = prod.id_producto;
    campoNombre.value = prod.nombre;
    campoMarca.value = prod.marca || "";
    campoCategoria.value = prod.categoria || "";
    campoPrecio.value = prod.precio_venta;
    campoStockActual.value = prod.stock_actual;
    campoStockMinimo.value = prod.stock_minimo;

    tituloForm.textContent = `✏️ Editando: ${prod.nombre}`;
    btnGuardar.textContent = "Guardar cambios";
    btnCancelar.style.display = "inline-block";
    mensajeCancelar.textContent = `Editando producto #${prod.id_producto}`;
    window.scrollTo({ top: 0, behavior: "smooth" });
  } catch (error) {
    mostrarEstado("No se pudo cargar el producto para editar.", "error");
  }
}

async function eliminarProducto(id) {
  if (!confirm("¿Eliminar este producto del inventario? Esta acción no se puede deshacer.")) return;

  try {
    const resp = await fetch(`${API_BASE_URL}/${id}`, { method: "DELETE" });
    if (resp.status === 204) {
      mostrarEstado("Producto eliminado correctamente.", "ok");
      cargarProductos();
    } else {
      const data = await resp.json();
      mostrarEstado(data.mensaje || "No se pudo eliminar el producto.", "error");
    }
  } catch (error) {
    mostrarEstado("Error de conexión al eliminar el producto.", "error");
  }
}

form.addEventListener("submit", async (evento) => {
  evento.preventDefault();

  const payload = {
    nombre: campoNombre.value.trim(),
    marca: campoMarca.value.trim() || null,
    categoria: campoCategoria.value.trim() || null,
    precio_venta: parseFloat(campoPrecio.value),
    stock_actual: parseInt(campoStockActual.value, 10),
    stock_minimo: parseInt(campoStockMinimo.value, 10),
  };

  const idExistente = campoId.value;
  const esEdicion = Boolean(idExistente);
  const url = esEdicion ? `${API_BASE_URL}/${idExistente}` : API_BASE_URL;
  const metodo = esEdicion ? "PUT" : "POST";

  try {
    const resp = await fetch(url, {
      method: metodo,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    const data = await resp.json();

    if (resp.ok) {
      mostrarEstado(esEdicion ? "Producto actualizado correctamente." : "Producto creado correctamente.", "ok");
      limpiarFormulario();
      cargarProductos();
    } else {
      mostrarEstado(data.mensaje || "Ocurrió un error al guardar el producto.", "error");
    }
  } catch (error) {
    mostrarEstado("Error de conexión con la API.", "error");
  }
});

btnCancelar.addEventListener("click", limpiarFormulario);

cargarProductos();
