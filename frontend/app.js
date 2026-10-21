/**
 * Frontend simple (vanilla JS) para probar el CRUD de Producto.
 * Consume la API RESTful del backend FastAPI.
 *
 * La URL base de la API se lee de window.APP_CONFIG, generado en runtime
 * por el contenedor de nginx a partir de la variable de entorno
 * PRODUCTOS_API_URL (ver frontend/docker-entrypoint.sh y config.js).
 * Si no existe (por ejemplo, abriendo el HTML directo sin Docker), cae
 * a localhost:8000 como valor por defecto para desarrollo local.
 */
const RAIZ_API_URL = (window.APP_CONFIG && window.APP_CONFIG.PRODUCTOS_API_URL) || "http://localhost:8000";
const AUTH_URL = `${(window.APP_CONFIG && window.APP_CONFIG.AUTH_API_URL) || "http://localhost:8001"}/api/v1/auth`;
const USUARIOS_PRUEBA = (window.APP_CONFIG && window.APP_CONFIG.USUARIOS_PRUEBA) || { habilitado: false };
const API_BASE_URL = `${RAIZ_API_URL}/api/v1/productos`;
const SEED_URL = `${RAIZ_API_URL}/api/v1/dev/seed-productos`;

const form = document.getElementById("form-producto");
const tabla = document.getElementById("tabla-productos");
const estadoGlobal = document.getElementById("estado-global");
const indicadorConexion = document.getElementById("indicador-conexion");
const tituloForm = document.getElementById("titulo-form");
const btnGuardar = document.getElementById("btn-guardar");
const btnCancelar = document.getElementById("btn-cancelar");
const mensajeCancelar = document.getElementById("mensaje-cancelar");
const btnSeed = document.getElementById("btn-seed");
const campoCantidadSeed = document.getElementById("cantidad-seed");

const campoId = document.getElementById("id_producto");
const campoNombre = document.getElementById("nombre");
const campoMarca = document.getElementById("marca");
const campoCategoria = document.getElementById("categoria");
const campoPrecio = document.getElementById("precio_venta");
const campoStockActual = document.getElementById("stock_actual");
const campoStockMinimo = document.getElementById("stock_minimo");
const notaStockActual = document.getElementById("nota-stock-actual");

// --- Elementos de login / sesión ---
const pantallaLogin = document.getElementById("pantalla-login");
const appPrincipal = document.getElementById("app-principal");
const formLogin = document.getElementById("form-login");
const loginEmail = document.getElementById("login-email");
const loginPassword = document.getElementById("login-password");
const loginError = document.getElementById("login-error");
const btnLogin = document.getElementById("btn-login");
const btnLogout = document.getElementById("btn-logout");
const infoUsuario = document.getElementById("info-usuario");
const usuarioNombre = document.getElementById("usuario-nombre");
const usuarioRol = document.getElementById("usuario-rol");
const bloqueAccesoRapido = document.getElementById("acceso-rapido");

// ---------------------------------------------------------------------------
// Sesión (JWT). Access token de vida corta + refresh token de un solo uso:
// cada refresh devuelve un refresh token nuevo (header X-New-Refresh-Token).
// ---------------------------------------------------------------------------
const CLAVE_SESION = "jugueteria.sesion";
let sesion = null;          // { access, refresh }
let usuarioActual = null;   // { id_usuario, email, nombre, rol, activo }
let refrescoEnCurso = null; // promesa compartida: evita gastar dos veces el mismo refresh token

function cargarSesionGuardada() {
  try {
    sesion = JSON.parse(localStorage.getItem(CLAVE_SESION)) || null;
  } catch (e) {
    sesion = null;
  }
}

function guardarSesion(nueva) {
  sesion = nueva;
  try {
    if (nueva) localStorage.setItem(CLAVE_SESION, JSON.stringify(nueva));
    else localStorage.removeItem(CLAVE_SESION);
  } catch (e) { /* almacenamiento no disponible: la sesión vive solo en memoria */ }
}

function esAdmin() {
  return usuarioActual && usuarioActual.rol === "admin";
}

async function refrescarSesion() {
  if (!sesion || !sesion.refresh) return false;
  if (refrescoEnCurso) return refrescoEnCurso;

  refrescoEnCurso = (async () => {
    try {
      const resp = await fetch(`${AUTH_URL}/refresh`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: sesion.refresh }),
      });
      if (!resp.ok) return false;
      const data = await resp.json();
      const nuevoRefresh = resp.headers.get("X-New-Refresh-Token");
      if (!nuevoRefresh) return false;
      guardarSesion({ access: data.access_token, refresh: nuevoRefresh });
      return true;
    } catch (e) {
      return false;
    } finally {
      refrescoEnCurso = null;
    }
  })();
  return refrescoEnCurso;
}

/**
 * fetch con el access token en el header Authorization. Si la API responde
 * 401, intenta renovar la sesión una vez y reintenta; si tampoco se puede,
 * cierra la sesión local y vuelve a la pantalla de login.
 */
async function apiFetch(url, opciones = {}) {
  const armar = () => ({
    ...opciones,
    headers: {
      ...(opciones.headers || {}),
      ...(sesion ? { Authorization: `Bearer ${sesion.access}` } : {}),
    },
  });

  let resp = await fetch(url, armar());
  if (resp.status === 401 && (await refrescarSesion())) {
    resp = await fetch(url, armar());
  }
  if (resp.status === 401) {
    mostrarLogin("Tu sesión expiró. Iniciá sesión de nuevo.");
    const err = new Error("Sesión expirada");
    err.sesionExpirada = true;
    throw err;
  }
  return resp;
}

function mostrarLogin(mensaje = "") {
  guardarSesion(null);
  usuarioActual = null;
  appPrincipal.style.display = "none";
  infoUsuario.style.display = "none";
  btnLogout.style.display = "none";
  pantallaLogin.style.display = "block";
  formLogin.reset();
  if (mensaje) {
    loginError.textContent = mensaje;
    loginError.className = "estado error";
  } else {
    loginError.className = "estado";
  }
}

function mostrarApp() {
  pantallaLogin.style.display = "none";
  appPrincipal.style.display = "block";
  usuarioNombre.textContent = usuarioActual.nombre;
  usuarioRol.textContent = usuarioActual.rol;
  usuarioRol.className = `etiqueta rol-${usuarioActual.rol}`;
  infoUsuario.style.display = "inline";
  btnLogout.style.display = "inline-block";
  limpiarFormulario();
  cargarProductos();
}

async function iniciarSesion(email, password) {
  btnLogin.disabled = true;
  loginError.className = "estado";
  try {
    const resp = await fetch(`${AUTH_URL}/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });
    const data = await resp.json();
    if (!resp.ok) {
      loginError.textContent = data.mensaje || "No se pudo iniciar sesión.";
      loginError.className = "estado error";
      return;
    }

    guardarSesion({ access: data.access_token, refresh: data.refresh_token });
    const resMe = await apiFetch(`${AUTH_URL}/me`);
    if (!resMe.ok) throw new Error("No se pudo obtener el usuario.");
    usuarioActual = await resMe.json();
    mostrarApp();
  } catch (error) {
    if (!error.sesionExpirada) {
      loginError.textContent = "No se pudo conectar con el servicio de autenticación.";
      loginError.className = "estado error";
    }
  } finally {
    btnLogin.disabled = false;
  }
}

async function cerrarSesion() {
  const actual = sesion;
  if (actual) {
    // Revoca access + refresh en el servidor; si falla igual se cierra localmente.
    try {
      await fetch(`${AUTH_URL}/logout`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${actual.access}` },
        body: JSON.stringify({ refresh_token: actual.refresh }),
      });
    } catch (e) { /* sin conexión: no bloquea el cierre local */ }
  }
  tabla.innerHTML = "";
  mostrarLogin();
}

async function restaurarSesion() {
  cargarSesionGuardada();
  if (!sesion) return mostrarLogin();
  try {
    const resMe = await apiFetch(`${AUTH_URL}/me`);
    if (!resMe.ok) return mostrarLogin();
    usuarioActual = await resMe.json();
    mostrarApp();
  } catch (error) {
    if (!error.sesionExpirada) mostrarLogin();
  }
}

formLogin.addEventListener("submit", (evento) => {
  evento.preventDefault();
  iniciarSesion(loginEmail.value.trim(), loginPassword.value);
});

btnLogout.addEventListener("click", cerrarSesion);

// Acceso rápido por rol (solo si config.js lo habilita: entorno de pruebas)
if (USUARIOS_PRUEBA.habilitado) {
  bloqueAccesoRapido.style.display = "block";
  ["admin", "empleado"].forEach((rol) => {
    const cred = USUARIOS_PRUEBA[rol];
    document.getElementById(`cred-${rol}`).textContent = `${cred.email} · ${cred.password}`;
    document.getElementById(`btn-rapido-${rol}`).addEventListener("click", () => {
      iniciarSesion(cred.email, cred.password);
    });
  });
}


function mostrarEstado(mensaje, tipo = "ok") {
  estadoGlobal.textContent = mensaje;
  estadoGlobal.className = `estado ${tipo}`;
  setTimeout(() => {
    estadoGlobal.className = "estado";
  }, 4000);
}

// ---------------------------------------------------------------------------
// Errores de validación (400) por campo, envío seguro y fallas de red.
// Estas funciones también las usa movimientos.js (mismo scope global).
// ---------------------------------------------------------------------------

// Tiempo máximo de espera de una petición. Si el servidor tarda más (latencia
// alta, servicio caído), se corta y se informa al usuario en vez de dejar la
// pantalla "colgada" con el botón bloqueado.
const TIMEOUT_PETICION_MS = 15000;

// Campo del schema del backend -> id del <input> en el formulario de productos.
const MAPA_CAMPOS_PRODUCTO = {
  nombre: "nombre",
  marca: "marca",
  categoria: "categoria",
  precio_venta: "precio_venta",
  stock_actual: "stock_actual",
  stock_minimo: "stock_minimo",
};

/** Traduce un error de Pydantic (campo "detalles" del 400) a un mensaje claro en español. */
function traducirErrorValidacion(err) {
  const ctx = err.ctx || {};
  switch (err.type) {
    case "missing":
      return "Este campo es obligatorio.";
    case "string_too_short":
      return ctx.min_length === 1
        ? "Este campo no puede estar vacío."
        : `Debe tener al menos ${ctx.min_length} caracteres.`;
    case "string_too_long":
      return `Puede tener como máximo ${ctx.max_length} caracteres.`;
    case "greater_than_equal":
      return ctx.ge === 0 ? "No puede ser negativo." : `Debe ser mayor o igual a ${ctx.ge}.`;
    case "greater_than":
      return ctx.gt === 0 ? "Debe ser mayor que 0." : `Debe ser mayor que ${ctx.gt}.`;
    case "less_than_equal":
      return `No puede ser mayor que ${ctx.le}.`;
    case "less_than":
      return `Debe ser menor que ${ctx.lt}.`;
    case "int_parsing":
    case "int_from_float":
    case "int_type":
      return "Ingresá un número entero, sin decimales.";
    case "float_parsing":
    case "float_type":
      return "Ingresá un número válido.";
    case "string_type":
      return "Ingresá un texto válido.";
    case "literal_error":
      return "Elegí una de las opciones disponibles.";
    case "string_pattern_mismatch":
      return "Contiene caracteres no permitidos (por ejemplo < > ; = { }).";
    default:
      return err.msg || "El valor ingresado no es válido.";
  }
}

/** Quita el mensaje de error de un campo (si lo tiene) y restaura su estado normal. */
function limpiarErrorDeCampo(input) {
  if (!input || !input.id) return;
  const aviso = document.getElementById(`error-${input.id}`);
  if (aviso) aviso.remove();
  input.classList.remove("campo-invalido");
  input.removeAttribute("aria-invalid");
  if (input.dataset.describedbyOriginal !== undefined) {
    if (input.dataset.describedbyOriginal) input.setAttribute("aria-describedby", input.dataset.describedbyOriginal);
    else input.removeAttribute("aria-describedby");
    delete input.dataset.describedbyOriginal;
  }
}

function limpiarErroresDeCampo(contenedor) {
  contenedor.querySelectorAll(".campo-invalido").forEach(limpiarErrorDeCampo);
  contenedor.querySelectorAll(".error-campo").forEach((el) => el.remove());
}

/**
 * Marca un campo como inválido: mensaje en rojo debajo del campo, borde rojo y
 * aria-invalid. Devuelve true si lo marcó (false si ya tenía un error).
 */
function marcarCampoInvalido(input, mensaje) {
  if (!input || input.classList.contains("campo-invalido")) return false;

  const aviso = document.createElement("small");
  aviso.className = "error-campo";
  aviso.id = `error-${input.id}`;
  aviso.setAttribute("role", "alert");
  aviso.textContent = mensaje;
  (input.closest(".dropdown") || input).insertAdjacentElement("afterend", aviso);

  input.dataset.describedbyOriginal = input.getAttribute("aria-describedby") || "";
  input.setAttribute("aria-describedby", `${aviso.id} ${input.dataset.describedbyOriginal}`.trim());
  input.setAttribute("aria-invalid", "true");
  input.classList.add("campo-invalido");
  return true;
}

/**
 * Muestra cada error del 400 junto a su campo y enfoca el primero. Devuelve
 * los mensajes que no se pudieron asociar a ningún campo, para mostrarlos en
 * el aviso general. "traducciones" permite reemplazar el texto de un campo
 * concreto: { nombre_del_campo: (err) => "mensaje" }.
 */
function mostrarErroresDeCampo(detalles, mapaCampos, traducciones = {}) {
  const sinCampo = [];
  let primerInvalido = null;

  (Array.isArray(detalles) ? detalles : []).forEach((err) => {
    const nombreCampo = Array.isArray(err.loc) ? err.loc[err.loc.length - 1] : null;
    const idInput = mapaCampos[nombreCampo];
    const input = idInput ? document.getElementById(idInput) : null;
    const personalizado = traducciones[nombreCampo] ? traducciones[nombreCampo](err) : null;
    const mensaje = personalizado || traducirErrorValidacion(err);

    if (!input) {
      sinCampo.push(mensaje);
      return;
    }
    if (marcarCampoInvalido(input, mensaje) && !primerInvalido) primerInvalido = input;
  });

  if (primerInvalido) primerInvalido.focus();
  return sinCampo;
}

/** Deshabilita el botón y muestra un spinner con un texto de progreso. */
function bloquearBoton(boton, textoCargando) {
  boton.disabled = true;
  boton.setAttribute("aria-busy", "true");
  boton.innerHTML = `<span class="spinner spinner-btn" aria-hidden="true"></span>${textoCargando}`;
}

function liberarBoton(boton, texto) {
  boton.disabled = false;
  boton.removeAttribute("aria-busy");
  boton.textContent = texto;
}

/** apiFetch con tiempo máximo de espera (AbortController). */
async function apiFetchConTimeout(url, opciones = {}, ms = TIMEOUT_PETICION_MS) {
  const controlador = new AbortController();
  const temporizador = setTimeout(() => controlador.abort(), ms);
  try {
    return await apiFetch(url, { ...opciones, signal: controlador.signal });
  } finally {
    clearTimeout(temporizador);
  }
}

/** Lee el JSON de la respuesta; si no es JSON (p. ej. un 502 de un proxy), devuelve {}. */
async function leerJson(resp) {
  try {
    return await resp.json();
  } catch (e) {
    return {};
  }
}

/** Mensaje para fallas de red, timeout o servidor caído, con el consejo que corresponda. */
function describirFallaDeRed(error, consejo) {
  let causa;
  if (!navigator.onLine) causa = "Parece que no hay conexión a internet.";
  else if (error && error.name === "AbortError") causa = `El servidor tardó más de ${TIMEOUT_PETICION_MS / 1000} segundos en responder.`;
  else causa = "No se pudo conectar con el servidor.";
  return `${causa} ${consejo}`;
}

function formatearPrecio(valor) {
  return valor.toLocaleString("es-AR", { style: "currency", currency: "ARS" });
}

function limpiarFormulario() {
  form.reset();
  limpiarErroresDeCampo(form);
  campoId.value = "";
  tituloForm.textContent = "➕ Nuevo producto";
  btnGuardar.textContent = "Guardar producto";
  btnCancelar.style.display = "none";
  mensajeCancelar.textContent = "";
  // Al crear un producto nuevo, el stock actual sí es editable (es el
  // stock inicial). Al editar uno existente, se bloquea (ver editarProducto).
  campoStockActual.disabled = false;
  campoStockActual.required = true;
  notaStockActual.style.display = "none";
}

function mostrarCargando() {
  tabla.innerHTML = `
    <tr>
      <td colspan="7">
        <div class="estado-carga">
          <span class="spinner" aria-hidden="true"></span>
          <span>Cargando registros del servidor...</span>
        </div>
      </td>
    </tr>
  `;
}

async function cargarProductos() {
  mostrarCargando();
  try {
    const resp = await apiFetch(API_BASE_URL);
    if (!resp.ok) throw new Error("No se pudo obtener el listado de productos.");
    const productos = await resp.json();
    indicadorConexion.textContent = "API conectada";
    indicadorConexion.className = "etiqueta stock-ok";
    renderizarTabla(productos);
  } catch (error) {
    if (error.sesionExpirada) return;
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
          ${esAdmin() ? `<button class="btn-peligro btn-chico" onclick="eliminarProducto(${prod.id_producto})">Eliminar</button>` : ""}
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
    const resp = await apiFetch(`${API_BASE_URL}/${id}`);
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
    // El stock actual no se edita desde la ficha del producto: se gestiona
    // mediante movimientos de inventario. Se muestra de solo lectura para
    // que el usuario vea el valor vigente sin poder modificarlo por acá.
    campoStockActual.disabled = true;
    campoStockActual.required = false;
    notaStockActual.style.display = "block";
    window.scrollTo({ top: 0, behavior: "smooth" });
  } catch (error) {
    if (error.sesionExpirada) return;
    mostrarEstado("No se pudo cargar el producto para editar.", "error");
  }
}

async function eliminarProducto(id) {
  if (!confirm("¿Eliminar este producto del inventario? Esta acción no se puede deshacer.")) return;

  try {
    const resp = await apiFetch(`${API_BASE_URL}/${id}`, { method: "DELETE" });
    if (resp.status === 204) {
      mostrarEstado("Producto eliminado correctamente.", "ok");
      cargarProductos();
    } else if (resp.status === 200) {
      // El producto tenía movimientos asociados: se dio de baja lógica
      // (activo=false) en vez de eliminarse físicamente, para conservar
      // el historial. No aparece más en el listado, pero sigue existiendo.
      mostrarEstado("El producto tiene movimientos registrados: se dio de baja en vez de eliminarse.", "ok");
      cargarProductos();
    } else {
      const data = await resp.json();
      mostrarEstado(data.mensaje || data.detail || "No se pudo eliminar el producto.", "error");
    }
  } catch (error) {
    if (error.sesionExpirada) return;
    mostrarEstado("Error de conexión al eliminar el producto.", "error");
  }
}

// Barrera contra envíos duplicados: mientras hay una petición en curso (doble
// clic, Enter repetido) cualquier otro envío se ignora.
let envioProductoEnCurso = false;

form.addEventListener("submit", async (evento) => {
  evento.preventDefault();
  if (envioProductoEnCurso) return;

  const idExistente = campoId.value;
  const esEdicion = Boolean(idExistente);

  const payload = {
    nombre: campoNombre.value.trim(),
    marca: campoMarca.value.trim() || null,
    categoria: campoCategoria.value.trim() || null,
    precio_venta: parseFloat(campoPrecio.value),
    stock_minimo: parseInt(campoStockMinimo.value, 10),
  };

  // stock_actual solo se envía al crear (stock inicial). En edición no se
  // incluye: el backend (PUT) ni siquiera acepta este campo, ya que el
  // stock se gestiona mediante movimientos de inventario, no editando la
  // ficha del producto.
  if (!esEdicion) {
    payload.stock_actual = parseInt(campoStockActual.value, 10);
  }

  const url = esEdicion ? `${API_BASE_URL}/${idExistente}` : API_BASE_URL;
  const metodo = esEdicion ? "PUT" : "POST";

  envioProductoEnCurso = true;
  limpiarErroresDeCampo(form);
  bloquearBoton(btnGuardar, esEdicion ? "Guardando cambios..." : "Guardando...");
  btnCancelar.disabled = true;
  form.setAttribute("aria-busy", "true");

  try {
    const resp = await apiFetchConTimeout(url, {
      method: metodo,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    const data = await leerJson(resp);

    if (resp.ok) {
      mostrarEstado(esEdicion ? "Producto actualizado correctamente." : "Producto creado correctamente.", "ok");
      limpiarFormulario();
      cargarProductos();
    } else if (resp.status === 400 && Array.isArray(data.detalles)) {
      // Cada error se muestra junto a su campo; lo que no tenga campo va al aviso general.
      const sinCampo = mostrarErroresDeCampo(data.detalles, MAPA_CAMPOS_PRODUCTO);
      mostrarEstado(sinCampo.length ? sinCampo.join(" ") : "Revisá los campos marcados en rojo.", "error");
    } else {
      mostrarEstado(data.mensaje || data.detail || "Ocurrió un error al guardar el producto.", "error");
    }
  } catch (error) {
    if (error.sesionExpirada) return;
    // POST de producto no es idempotente en el backend: si la respuesta no
    // llegó, el producto pudo haberse creado igual. Se refresca el listado
    // para que el usuario lo verifique antes de reintentar.
    mostrarEstado(
      describirFallaDeRed(
        error,
        esEdicion
          ? "Tus datos siguen en el formulario: podés reintentar."
          : "No podemos confirmar si el producto se guardó: revisá el inventario antes de volver a intentar, para no duplicarlo."
      ),
      "error"
    );
    if (!esEdicion) cargarProductos();
  } finally {
    envioProductoEnCurso = false;
    liberarBoton(btnGuardar, campoId.value ? "Guardar cambios" : "Guardar producto");
    btnCancelar.disabled = false;
    form.removeAttribute("aria-busy");
  }
});

// El error de un campo desaparece apenas el usuario empieza a corregirlo.
form.addEventListener("input", (evento) => limpiarErrorDeCampo(evento.target));

// Botón de la guía: rellena el formulario con un ejemplo (no guarda nada).
const btnEjemplo = document.getElementById("btn-ejemplo");
btnEjemplo.addEventListener("click", () => {
  if (campoId.value) {
    mostrarEstado("Estás editando un producto: cancelá la edición para usar el ejemplo.", "error");
    return;
  }
  limpiarErroresDeCampo(form);
  campoNombre.value = "Muñeca Bebota";
  campoMarca.value = "Playkids";
  campoCategoria.value = "Muñecas";
  campoPrecio.value = "12500.50";
  campoStockActual.value = "20";
  campoStockMinimo.value = "5";
  campoNombre.focus();
});

btnCancelar.addEventListener("click", limpiarFormulario);

async function cargarDatosDePrueba() {
  const cantidad = parseInt(campoCantidadSeed.value, 10) || 20;

  const confirmado = confirm(
    `¿Generar ${cantidad} productos de prueba con datos aleatorios? Se agregan al inventario actual, no lo reemplazan.`
  );
  if (!confirmado) return;

  btnSeed.disabled = true;
  const textoOriginal = btnSeed.textContent;
  btnSeed.textContent = "Generando...";

  try {
    const resp = await apiFetch(`${SEED_URL}?cantidad=${cantidad}`, { method: "POST" });
    const data = await resp.json();

    if (resp.ok) {
      mostrarEstado(`Se cargaron ${data.length} productos de prueba.`, "ok");
      cargarProductos();
    } else {
      mostrarEstado(data.mensaje || "No se pudieron generar los productos de prueba.", "error");
    }
  } catch (error) {
    if (error.sesionExpirada) return;
    mostrarEstado("Error de conexión al generar datos de prueba.", "error");
  } finally {
    btnSeed.disabled = false;
    btnSeed.textContent = textoOriginal;
  }
}

btnSeed.addEventListener("click", cargarDatosDePrueba);


// --- Restricciones de entrada en formularios ---
// HTML5 input[type=number] acepta "e", "E", "+", "-" y "." porque son
// válidos en notación científica (1e5, -1.5). En un inventario no tienen
// sentido, así que los bloqueamos a nivel de teclado y pegado.

function restringirInputNumerico(input, { permitirDecimal = false } = {}) {
  if (!input) return;
  const teclasBloqueadas = ["e", "E", "+", "-"];
  if (!permitirDecimal) teclasBloqueadas.push(".");
  input.addEventListener("keydown", (evento) => {
    if (teclasBloqueadas.includes(evento.key)) evento.preventDefault();
  });
  input.addEventListener("paste", (evento) => {
    const texto = (evento.clipboardData || window.clipboardData).getData("text");
    const patron = permitirDecimal ? /^[0-9]*\.?[0-9]*$/ : /^[0-9]*$/;
    if (!patron.test(texto)) evento.preventDefault();
  });
}

// Los DNI son 8 dígitos numéricos: se filtra todo lo que no sea dígito
// y se limita a 8 caracteres.
function restringirInputDni(input) {
  if (!input) return;
  input.addEventListener("input", () => {
    input.value = input.value.replace(/\D/g, "").slice(0, 8);
  });
}

// --- Tooltips de ayuda de los campos (botón "?") ---
// Con el mouse se muestran al pasar por encima; con teclado, al enfocar el
// botón; en pantallas táctiles, al tocarlo (se alterna la clase "abierto").
// Escape los oculta (el tooltip vuelve a aparecer al volver a entrar o enfocar).
function cerrarTooltips(excepto) {
  document.querySelectorAll(".rotulo-fila.abierto").forEach((fila) => {
    if (fila !== excepto) fila.classList.remove("abierto");
  });
}

document.addEventListener("click", (evento) => {
  const icono = evento.target.closest(".ayuda-icono");
  if (!icono) return cerrarTooltips();
  const fila = icono.closest(".rotulo-fila");
  cerrarTooltips(fila);
  fila.classList.remove("oculto");
  fila.classList.toggle("abierto");
});

document.addEventListener("keydown", (evento) => {
  if (evento.key !== "Escape") return;
  document.querySelectorAll(".rotulo-fila").forEach((fila) => {
    fila.classList.remove("abierto");
    fila.classList.add("oculto");
  });
  if (document.activeElement && document.activeElement.classList.contains("ayuda-icono")) {
    document.activeElement.blur();
  }
});

document.addEventListener("mouseover", (evento) => {
  const fila = evento.target.closest && evento.target.closest(".rotulo-fila");
  if (fila) fila.classList.remove("oculto");
});
document.addEventListener("focusin", (evento) => {
  const fila = evento.target.closest && evento.target.closest(".rotulo-fila");
  if (fila) fila.classList.remove("oculto");
});


// Aplicar restricciones a los inputs del formulario de productos.
restringirInputNumerico(campoPrecio, { permitirDecimal: true });
restringirInputNumerico(campoStockActual);
restringirInputNumerico(campoStockMinimo);
restringirInputNumerico(campoCantidadSeed);


restaurarSesion();