/**
 * Frontend de la pantalla de Movimientos (venta, ingreso, estropeo, devolución).
 * Consume la API REST del servicio "movimientos" (services/movimientos).
 *
 * Autenticación: reutiliza apiFetch() de app.js, que agrega automáticamente
 * el header Authorization: Bearer <access_token> y maneja el refresco de la
 * sesión.
 *
 * Selección de producto: input con <datalist> nativo. El usuario escribe
 * y el navegador sugiere; además se puede ver el listado completo
 * enfocando el campo (datalist muestra todas las opciones al hacer foco).
 */
const MOVIMIENTOS_API_BASE = `${(window.APP_CONFIG && window.APP_CONFIG.MOVIMIENTOS_API_URL) || "http://localhost:8002"}/api/v1`;

const formMovimiento = document.getElementById("form-movimiento");
const inputProducto = document.getElementById("mov_producto_input");
const datalistProductos = document.getElementById("mov-productos-list");
const selectCategoria = document.getElementById("mov_categoria");
const campoCantidad = document.getElementById("mov_cantidad");
const campoDniCliente = document.getElementById("mov_dni_cliente");
const campoDniEmpleado = document.getElementById("mov_dni_empleado");
const tablaMovimientos = document.getElementById("tabla-movimientos");
const btnGuardarMovimiento = document.getElementById("btn-guardar-movimiento");

const ETIQUETAS_CATEGORIA = {
  venta: { texto: "Venta", clase: "badge-venta" },
  ingreso: { texto: "Ingreso", clase: "badge-ingreso" },
  estropeo: { texto: "Estropeo", clase: "badge-estropeo" },
  devolucion: { texto: "Devolución", clase: "badge-devolucion" },
};

// Caché local del listado de productos, para poder resolver el ID desde el
// texto que el usuario escribió en el input.
let productosDisponibles = [];

function formatearFecha(iso) {
  try {
    return new Date(iso).toLocaleString("es-AR");
  } catch (error) {
    return iso;
  }
}

function formatearOpcionProducto(producto) {
  // Texto que ve el usuario y que queda en el value del input. Incluye el
  // ID con "#" para poder parsearlo al enviar el formulario.
  return `#${producto.id_producto} — ${producto.nombre} (stock: ${producto.stock_actual})`;
}

async function cargarOpcionesDeProductos() {
  try {
    const resp = await apiFetch(API_BASE_URL);
    if (!resp.ok) throw new Error("No se pudo obtener el listado de productos.");
    productosDisponibles = await resp.json();

    datalistProductos.innerHTML = productosDisponibles
      .map((p) => `<option value="${formatearOpcionProducto(p)}"></option>`)
      .join("");
  } catch (error) {
    if (error.sesionExpirada) return;
    datalistProductos.innerHTML = "";
  }
}

/**
 * Resuelve el id_producto a partir de lo que el usuario escribió.
 * Acepta:
 *  - El texto completo "#5 — Muñeca Bebota (stock: 10)"
 *  - Solo "#5" o "5"
 *  - El nombre exacto "Muñeca Bebota"
 * Devuelve null si no puede resolverlo.
 */
function obtenerIdProductoSeleccionado() {
  const valor = inputProducto.value.trim();
  if (!valor) return null;

  const matchId = valor.match(/^#?(\d+)/);
  if (matchId) {
    const id = parseInt(matchId[1], 10);
    if (productosDisponibles.some((p) => p.id_producto === id)) return id;
  }

  const porNombre = productosDisponibles.find((p) => p.nombre === valor);
  if (porNombre) return porNombre.id_producto;

  return null;
}

async function cargarMovimientos() {
  tablaMovimientos.innerHTML = `
    <tr>
      <td colspan="7">
        <div class="estado-carga">
          <span class="spinner" aria-hidden="true"></span>
          <span>Cargando movimientos...</span>
        </div>
      </td>
    </tr>
  `;

  try {
    const resp = await apiFetch(`${MOVIMIENTOS_API_BASE}/movimientos`);
    if (!resp.ok) throw new Error("No se pudo obtener el historial de movimientos.");
    const movimientos = await resp.json();
    renderizarTablaMovimientos(movimientos);
  } catch (error) {
    if (error.sesionExpirada) return;
    tablaMovimientos.innerHTML = `<tr><td colspan="7" class="vacio">No se pudo conectar con el servicio de Movimientos (${MOVIMIENTOS_API_BASE}).</td></tr>`;
  }
}

function renderizarTablaMovimientos(movimientos) {
  if (!movimientos.length) {
    tablaMovimientos.innerHTML = `<tr><td colspan="7" class="vacio">Todavía no hay movimientos registrados.</td></tr>`;
    return;
  }

  tablaMovimientos.innerHTML = movimientos.map((mov) => {
    const etiqueta = ETIQUETAS_CATEGORIA[mov.categoria_movimiento] || { texto: mov.categoria_movimiento, clase: "" };
    return `
      <tr>
        <td>${mov.id_movimiento}</td>
        <td>${formatearFecha(mov.fecha_hora)}</td>
        <td><span class="badge-categoria ${etiqueta.clase}">${etiqueta.texto}</span></td>
        <td>#${mov.id_producto}</td>
        <td>${mov.cantidad_movida}</td>
        <td>${escaparHtml(mov.dni_cliente || "—")}</td>
        <td>${escaparHtml(mov.dni_empleado || "—")}</td>
      </tr>
    `;
  }).join("");
}

formMovimiento.addEventListener("submit", async (evento) => {
  evento.preventDefault();

  const idProducto = obtenerIdProductoSeleccionado();
  if (!idProducto) {
    mostrarEstado("Elegí un producto del listado de sugerencias.", "error");
    return;
  }

  const payload = {
    categoria_movimiento: selectCategoria.value,
    cantidad_movida: parseInt(campoCantidad.value, 10),
    id_producto: idProducto,
    dni_cliente: campoDniCliente.value.trim() || null,
    dni_empleado: campoDniEmpleado.value.trim() || null,
  };

  btnGuardarMovimiento.disabled = true;
  try {
    const resp = await apiFetch(`${MOVIMIENTOS_API_BASE}/movimientos`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    const data = await resp.json();

    if (resp.ok) {
      mostrarEstado("Movimiento registrado correctamente. El stock del producto fue actualizado.", "ok");
      formMovimiento.reset();
      cargarMovimientos();
      cargarOpcionesDeProductos();
      if (typeof cargarProductos === "function") cargarProductos();
    } else {
      mostrarEstado(data.mensaje || data.detail || "Ocurrió un error al registrar el movimiento.", "error");
    }
  } catch (error) {
    if (error.sesionExpirada) return;
    mostrarEstado("Error de conexión con el servicio de Movimientos.", "error");
  } finally {
    btnGuardarMovimiento.disabled = false;
  }
});

// Aplicar restricciones de entrada al formulario de movimientos.
// Las funciones están definidas en app.js (mismo scope global).
restringirInputNumerico(campoCantidad);
restringirInputDni(campoDniCliente);
restringirInputDni(campoDniEmpleado);