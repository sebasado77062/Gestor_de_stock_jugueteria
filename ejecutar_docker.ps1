# Levanta TODO el sistema con Docker Compose desde Windows: redis, rabbitmq, auth,
# productos, movimientos, worker-alertas y frontend. A diferencia de
# ejecutar_local.ps1 (sin Docker, sin RabbitMQ ni worker), aca corre el sistema
# completo: Redis compartido (el logout invalida el token en los tres servicios),
# RabbitMQ y el worker de alertas.
#
# Requiere Docker Desktop (con "docker compose" v2) iniciado.
#
# Uso (desde la carpeta del proyecto, en PowerShell):
#   powershell -ExecutionPolicy Bypass -File .\ejecutar_docker.ps1                 # construir y levantar
#   powershell -ExecutionPolicy Bypass -File .\ejecutar_docker.ps1 -NoAbrir        # sin abrir el navegador
#   powershell -ExecutionPolicy Bypass -File .\ejecutar_docker.ps1 -Detener        # detener (conserva los datos)
#   powershell -ExecutionPolicy Bypass -File .\ejecutar_docker.ps1 -BorrarDatos    # detener y BORRAR los datos
#
# Que hace al levantar:
#   1. Verifica que Docker y "docker compose" esten disponibles y funcionando.
#   2. Crea .env a partir de .env.example si no existe.
#   3. Si .env no tiene AUTH_SECRET_KEY (obligatoria; el compose se detiene sin ella),
#      genera una aleatoria. Si ya existe NO la toca: cambiarla invalidaria las sesiones.
#   4. Ejecuta "docker compose up -d --build" y espera a que los servicios respondan.
#
# Importante: usa los mismos puertos que ejecutar_local.ps1 (8000, 8001, 8002, 8080).
# Si lo tenias corriendo, cerra esas ventanas antes de usar este script.

param(
    [switch]$Detener,
    [switch]$BorrarDatos,
    [switch]$NoAbrir
)

$ErrorActionPreference = "Stop"
$raiz = $PSScriptRoot
Set-Location $raiz
$envPath = Join-Path $raiz ".env"
$envEjemploPath = Join-Path $raiz ".env.example"

function Fallar($mensaje) {
    Write-Host ""
    Write-Host "ERROR: $mensaje" -ForegroundColor Red
    exit 1
}

# Ejecuta un comando nativo sin mostrar su salida y devuelve $true si termino bien.
# (Con $ErrorActionPreference = "Stop", Windows PowerShell trata como error lo que
# un programa escribe en stderr; por eso se baja a "Continue" dentro de la funcion.)
function Funciona($exe, $argumentos) {
    $ErrorActionPreference = "Continue"
    try {
        & $exe @argumentos 2>$null | Out-Null
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    }
}

# Lee el valor de una variable del .env (o devuelve el valor por defecto).
function Leer-Env($nombre, $defecto) {
    if (Test-Path $envPath) {
        $linea = Get-Content $envPath | Where-Object { $_ -match "^\s*$nombre\s*=" } | Select-Object -Last 1
        if ($linea) {
            $valor = ($linea -split "=", 2)[1].Trim().Trim('"').Trim("'")
            if ($valor) { return $valor }
        }
    }
    return $defecto
}

# ---------------------------------------------------------------------------
# 1. Docker disponible y funcionando
# ---------------------------------------------------------------------------
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Fallar "No se encontro Docker. Instala Docker Desktop (https://www.docker.com/products/docker-desktop/), abrilo y volve a ejecutar este script."
}
if (-not (Funciona "docker" @("info"))) {
    Fallar "Docker esta instalado pero no responde. Abri Docker Desktop, espera a que diga 'Engine running' y volve a ejecutar este script."
}
if (-not (Funciona "docker" @("compose", "version"))) {
    Fallar "Tu Docker no tiene 'docker compose' (v2). Actualiza Docker Desktop a una version reciente."
}

# ---------------------------------------------------------------------------
# Modo detener / borrar datos
# ---------------------------------------------------------------------------
if ($Detener -or $BorrarDatos) {
    $ErrorActionPreference = "Continue"
    if ($BorrarDatos) {
        Write-Host "Esto detiene el sistema y BORRA todos los datos: productos, movimientos," -ForegroundColor Yellow
        Write-Host "usuarios, colas de RabbitMQ y la blacklist de tokens." -ForegroundColor Yellow
        $respuesta = Read-Host "Escribi SI (en mayusculas) para continuar"
        if ($respuesta -cne "SI") {
            Write-Host "Cancelado: no se borro nada."
            exit 0
        }
        & docker compose down -v
    } else {
        & docker compose down
    }
    if ($LASTEXITCODE -ne 0) { Fallar "docker compose down fallo (codigo $LASTEXITCODE)." }
    Write-Host ""
    if ($BorrarDatos) {
        Write-Host "Sistema detenido y datos borrados." -ForegroundColor Green
    } else {
        Write-Host "Sistema detenido. Los datos se conservan; para volver a levantarlo ejecuta este script sin parametros." -ForegroundColor Green
    }
    exit 0
}

# ---------------------------------------------------------------------------
# 2. Archivo .env
# ---------------------------------------------------------------------------
if (-not (Test-Path $envPath)) {
    if (-not (Test-Path $envEjemploPath)) {
        Fallar "No existe .env ni .env.example en $raiz. Ejecuta el script desde la carpeta del proyecto."
    }
    Copy-Item $envEjemploPath $envPath
    Write-Host "Se creo .env a partir de .env.example." -ForegroundColor Green
}

# ---------------------------------------------------------------------------
# 3. AUTH_SECRET_KEY (obligatoria; .env.example no la incluye a proposito)
# ---------------------------------------------------------------------------
$contenidoEnv = [System.IO.File]::ReadAllText($envPath)
# OJO: se usa [ \t] y no \s, porque \s tambien abarca el salto de linea y una linea
# "AUTH_SECRET_KEY=" vacia "encontraria" como valor la primera letra de la linea siguiente.
if ($contenidoEnv -notmatch "(?m)^[ \t]*AUTH_SECRET_KEY[ \t]*=[ \t]*[^ \t\r\n]") {
    $bytes = New-Object byte[] 48
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    $rng.GetBytes($bytes)
    $secreto = ($bytes | ForEach-Object { $_.ToString("x2") }) -join ""

    $utf8SinBom = New-Object System.Text.UTF8Encoding($false)
    if ($contenidoEnv -match "(?m)^[ \t]*AUTH_SECRET_KEY[ \t]*=[ \t]*\r?$") {
        # La variable existe pero vacia (p. ej. un placeholder): se completa en el lugar,
        # sin dejar una segunda linea duplicada.
        $nuevoContenido = [regex]::Replace($contenidoEnv, "(?m)^[ \t]*AUTH_SECRET_KEY[ \t]*=[ \t]*(?=\r?$)", "AUTH_SECRET_KEY=$secreto")
        [System.IO.File]::WriteAllText($envPath, $nuevoContenido, $utf8SinBom)
    } else {
        $linea = "AUTH_SECRET_KEY=$secreto"
        if ($contenidoEnv.Length -gt 0 -and -not $contenidoEnv.EndsWith("`n")) {
            $linea = [Environment]::NewLine + $linea
        }
        $linea = $linea + [Environment]::NewLine
        [System.IO.File]::AppendAllText($envPath, $linea, $utf8SinBom)
    }
    Write-Host "Se genero una AUTH_SECRET_KEY aleatoria y se guardo en .env (no la compartas ni subas ese archivo)." -ForegroundColor Green
}

# ---------------------------------------------------------------------------
# 4. Construir y levantar
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "Construyendo y levantando los contenedores (la primera vez puede tardar varios minutos)..."
$ErrorActionPreference = "Continue"
& docker compose up -d --build
if ($LASTEXITCODE -ne 0) {
    Fallar "docker compose up fallo (codigo $LASTEXITCODE). Causas comunes: un puerto ocupado (cerra ejecutar_local.ps1 si lo tenias abierto) o un error de construccion. Detalle: docker compose logs"
}
$ErrorActionPreference = "Stop"

# Puertos publicados (los mismos nombres de variable que usa docker-compose.yml)
$puertoAuth = Leer-Env "AUTH_PORT" "8001"
$puertoProductos = Leer-Env "PRODUCTOS_PORT" "8000"
$puertoMovimientos = Leer-Env "MOVIMIENTOS_PORT" "8002"
$puertoWorker = Leer-Env "WORKER_PORT" "8003"
$puertoFrontend = Leer-Env "FRONTEND_PORT" "8080"
$puertoRabbit = Leer-Env "RABBITMQ_MANAGEMENT_PORT" "15672"

$comprobaciones = @(
    @{ Nombre = "auth";           Url = "http://localhost:$puertoAuth/health" },
    @{ Nombre = "productos";      Url = "http://localhost:$puertoProductos/health" },
    @{ Nombre = "movimientos";    Url = "http://localhost:$puertoMovimientos/health" },
    @{ Nombre = "worker-alertas"; Url = "http://localhost:$puertoWorker/health" },
    @{ Nombre = "frontend";       Url = "http://localhost:$puertoFrontend/" }
)

Write-Host ""
Write-Host "Esperando a que los servicios respondan (hasta 2 minutos)..."
$pendientes = @($comprobaciones)
foreach ($i in 1..40) {
    $siguientes = @()
    foreach ($c in $pendientes) {
        try {
            $resp = Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 $c.Url
            if ($resp.StatusCode -ne 200) { $siguientes += $c }
        } catch {
            $siguientes += $c
        }
    }
    $pendientes = @($siguientes)
    if ($pendientes.Count -eq 0) { break }
    Start-Sleep -Seconds 3
}

Write-Host ""
if ($pendientes.Count -eq 0) {
    Write-Host "Listo. El sistema completo esta corriendo." -ForegroundColor Green
} else {
    $nombres = ($pendientes | ForEach-Object { $_.Nombre }) -join ", "
    Write-Host "Todavia no responden: $nombres" -ForegroundColor Yellow
    Write-Host "Puede que solo necesiten mas tiempo. Revisa el estado con:  docker compose ps" -ForegroundColor Yellow
    Write-Host "y los errores con:  docker compose logs -f <servicio>" -ForegroundColor Yellow
}

$rabbitUsuario = Leer-Env "RABBITMQ_DEFAULT_USER" "admin"
$rabbitClave = Leer-Env "RABBITMQ_DEFAULT_PASS" "admin123"
$adminEmail = Leer-Env "PRUEBA_ADMIN_EMAIL" "admin@test.com"
$adminClave = Leer-Env "PRUEBA_ADMIN_PASSWORD" "admin12345"
$empleadoEmail = Leer-Env "PRUEBA_EMPLEADO_EMAIL" "empleado@test.com"
$empleadoClave = Leer-Env "PRUEBA_EMPLEADO_PASSWORD" "empleado123"

Write-Host ""
Write-Host "  Frontend:                http://localhost:$puertoFrontend"
Write-Host "  Auth (Swagger):          http://localhost:$puertoAuth/docs"
Write-Host "  Productos (Swagger):     http://localhost:$puertoProductos/docs"
Write-Host "  Movimientos (Swagger):   http://localhost:$puertoMovimientos/docs"
Write-Host "  Worker (healthcheck):    http://localhost:$puertoWorker/health"
Write-Host "  RabbitMQ (panel):        http://localhost:$puertoRabbit   ($rabbitUsuario / $rabbitClave)"
Write-Host ""
Write-Host "  Usuarios de prueba (si HABILITAR_USUARIOS_PRUEBA=true, el valor por defecto):"
Write-Host "    admin:    $adminEmail / $adminClave"
Write-Host "    empleado: $empleadoEmail / $empleadoClave"
Write-Host ""
Write-Host "  Ver logs:         docker compose logs -f"
Write-Host "  Ver las alertas:  docker compose logs -f worker-alertas"
Write-Host "  Detener:          .\ejecutar_docker.ps1 -Detener"

if (-not $NoAbrir) {
    Start-Process "http://localhost:$puertoFrontend"
}
