/**
 * Frontend de la pantalla de Movimientos (venta, ingreso, estropeo, devolución).
 * Consume la API REST del servicio "movimientos" (services/movimientos).
 *
 * Autenticación: reutiliza apiFetch() de app.js, que agrega automáticamente
 * el header Authorization: Bearer <access_token> y maneja el refresco de la
 * sesión.
 *
 * Dropdowns custom (producto y categoría): no se usa <datalist> ni <select>
 * nativos porque queremos consistencia visual, control total del estilo, y
 * poder mostrar nombres largos en varias líneas. El valor seleccionado se
 * guarda en variables, no se parsea del texto del input.
 */
const MOVIMIENTOS_API_BASE = `${(window.APP_CONFIG && window.APP_CONFIG.MOVIMIENTOS_API_URL) || "http://localhost:8002"}/api/v1`;

const formMovimiento = document.getElementById("form-movimiento");

// Dropdown de productos
const wrapperProducto = document.getElementById("mov-producto-dropdown");
const inputProducto = document.getElementById("mov_producto_input");
const listaProductos = document.getElementById("mov-productos-lista");

// Dropdown de categorías
const wrapperCategoria = document.getElementById("mov-categoria-dropdown");
const inputCategoria = document.getElementById("mov_categoria_input");
const listaCategorias = document.getElementById("mov-categorias-lista");

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

const CATEGORIAS_MOVIMIENTO = [
  { valor: "venta", texto: "Venta (descuenta)" },
  { valor: "ingreso", texto: "Ingreso (incrementa)" },
  { valor: "estropeo", texto: "Estropeo (descuenta)" },
  { valor: "devolucion", texto: "Devolución (incrementa)" },
];

const CATEGORIA_POR_DEFECTO = "venta";

// Estado de los dropdowns
let productosDisponibles = [];
let productoSeleccionadoId = null;
let categoriaSeleccionada = CATEGORIA_POR_DEFECTO;

function formatearFecha(iso) {
  try {
    return new Date(iso).toLocaleString("es-AR");
  } catch (error) {
    return iso;
  }
}

// ---------------------------------------------------------------------------
// Carga de productos desde la API
// ---------------------------------------------------------------------------

function textoOpcionProducto(producto) {
  return `#${producto.id_producto} — ${producto.nombre}`;
}

async function cargarOpcionesDeProductos() {
  try {
    const resp = await apiFetch(API_BASE_URL);
    if (!resp.ok) throw new Error("No se pudo obtener el listado de productos.");
    productosDisponibles = await resp.json();
  } catch (error) {
    if (error.sesionExpirada) return;
    productosDisponibles = [];
  }
}

// ---------------------------------------------------------------------------
// Dropdown de productos
// ---------------------------------------------------------------------------

function filtrarProductos(filtro) {
  const texto = (filtro || "").trim().toLowerCase();
  if (!texto) return productosDisponibles;
  return productosDisponibles.filter((p) =>
    textoOpcionProducto(p).toLowerCase().includes(texto)
  );
}

function renderizarListaProductos(filtro) {
  const filtrados = filtrarProductos(filtro);
  if (!filtrados.length) {
    listaProductos.innerHTML = `<div class="dropdown-opcion sin-resultados">Sin coincidencias</div>`;
    return;
  }
  listaProductos.innerHTML = filtrados
    .map(
      (p) =>
        `<div class="dropdown-opcion" data-id="${p.id_producto}">${escaparHtml(textoOpcionProducto(p))}</div>`
    )
    .join("");
}

function mostrarListaProductos(filtro = "") {
  renderizarListaProductos(filtro);
  listaProductos.hidden = false;
}

function ocultarListaProductos() {
  listaProductos.hidden = true;
}

function seleccionarProducto(idProducto) {
  const producto = productosDisponibles.find((p) => p.id_producto === idProducto);
  if (!producto) return;
  productoSeleccionadoId = producto.id_producto;
  inputProducto.value = textoOpcionProducto(producto);
  ocultarListaProductos();
}

function limpiarSeleccionProducto() {
  productoSeleccionadoId = null;
  inputProducto.value = "";
}

inputProducto.addEventListener("input", () => {
  productoSeleccionadoId = null;
  mostrarListaProductos(inputProducto.value);
});

inputProducto.addEventListener("focus", () => {
  mostrarListaProductos(inputProducto.value);
});

listaProductos.addEventListener("click", (evento) => {
  const opcion = evento.target.closest(".dropdown-opcion[data-id]");
  if (!opcion) return;
  seleccionarProducto(parseInt(opcion.dataset.id, 10));
  inputProducto.focus();
});

inputProducto.addEventListener("keydown", (evento) => {
  if (evento.key === "Escape") {
    ocultarListaProductos();
    return;
  }
  if (evento.key === "Enter" && !listaProductos.hidden) {
    const primera = listaProductos.querySelector(".dropdown-opcion[data-id]");
    if (primera) {
      evento.preventDefault();
      seleccionarProducto(parseInt(primera.dataset.id, 10));
    }
  }
});

// ---------------------------------------------------------------------------
// Dropdown de categorías
// ---------------------------------------------------------------------------

function renderizarListaCategorias() {
  listaCategorias.innerHTML = CATEGORIAS_MOVIMIENTO
    .map(
      (c) =>
        `<div class="dropdown-opcion" data-valor="${c.valor}">${escaparHtml(c.texto)}</div>`
    )
    .join("");
}

function mostrarListaCategorias() {
  renderizarListaCategorias();
  listaCategorias.hidden = false;
}

function ocultarListaCategorias() {
  listaCategorias.hidden = true;
}

function seleccionarCategoria(valor) {
  const categoria = CATEGORIAS_MOVIMIENTO.find((c) => c.valor === valor);
  if (!categoria) return;
  categoriaSeleccionada = categoria.valor;
  inputCategoria.value = categoria.texto;
  ocultarListaCategorias();
}

function reiniciarCategoria() {
  seleccionarCategoria(CATEGORIA_POR_DEFECTO);
}

inputCategoria.addEventListener("focus", () => {
  mostrarListaCategorias();
});

inputCategoria.addEventListener("click", () => {
  // Si el usuario hace click y por algún motivo estaba cerrada, la abre.
  if (listaCategorias.hidden) mostrarListaCategorias();
});

listaCategorias.addEventListener("click", (evento) => {
  const opcion = evento.target.closest(".dropdown-opcion[data-valor]");
  if (!opcion) return;
  seleccionarCategoria(opcion.dataset.valor);
  inputCategoria.blur();
});

inputCategoria.addEventListener("keydown", (evento) => {
  if (evento.key === "Escape") {
    ocultarListaCategorias();
  }
});

// ---------------------------------------------------------------------------
// Cierre de dropdowns al hacer click afuera
// ---------------------------------------------------------------------------

document.addEventListener("click", (evento) => {
  if (!wrapperProducto.contains(evento.target)) {
    ocultarListaProductos();
  }
  if (!wrapperCategoria.contains(evento.target)) {
    ocultarListaCategorias();
  }
});

// ---------------------------------------------------------------------------
// Listado e historial de movimientos
// ---------------------------------------------------------------------------

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

  tablaMovimientos.innerHTML = movimientos
    .map((mov) => {
      const etiqueta =
        ETIQUETAS_CATEGORIA[mov.categoria_movimiento] || { texto: mov.categoria_movimiento, clase: "" };
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
    })
    .join("");
}

// ---------------------------------------------------------------------------
// Envío del formulario
// ---------------------------------------------------------------------------

formMovimiento.addEventListener("submit", async (evento) => {
  evento.preventDefault();

  // Si el usuario escribió el texto exacto de una opción sin hacer clic,
  // se resuelve el id a partir del texto.
  if (!productoSeleccionadoId) {
    const texto = inputProducto.value.trim();
    const exacto = productosDisponibles.find((p) => textoOpcionProducto(p) === texto);
    if (exacto) productoSeleccionadoId = exacto.id_producto;
  }

  if (!productoSeleccionadoId) {
    mostrarEstado("Elegí un producto del listado de sugerencias.", "error");
    return;
  }

  const payload = {
    categoria_movimiento: categoriaSeleccionada,
    cantidad_movida: parseInt(campoCantidad.value, 10),
    id_producto: productoSeleccionadoId,
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
      limpiarSeleccionProducto();
      reiniciarCategoria();
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

// ---------------------------------------------------------------------------
// Inicialización
// ---------------------------------------------------------------------------

// Aplicar restricciones de entrada al formulario de movimientos.
// Las funciones están definidas en app.js (mismo scope global).
restringirInputNumerico(campoCantidad);
restringirInputDni(campoDniCliente);
restringirInputDni(campoDniEmpleado);

// Categoría por defecto: Venta.
reiniciarCategoria();