# Levanta auth + productos + movimientos + frontend en Windows SIN Docker y SIN Redis
# (usa un Redis en memoria). Requiere Python 3.10 a 3.13 (recomendado 3.12).
# Nota: RabbitMQ y el worker de alertas NO corren en este modo. Los movimientos se
# registran igual: el evento queda guardado en el outbox de Movimientos (no se publica
# porque no hay broker; el relay esta desactivado aqui para no llenar el log de errores).
# El logout SI invalida el token en los tres servicios: como cada uno tiene su propio
# Redis en memoria, productos y movimientos le preguntan a auth si el token fue revocado.
# Si tenes Docker instalado, ejecutar_docker.ps1 levanta el sistema COMPLETO (con RabbitMQ
# y el worker). Ambos scripts usan los mismos puertos: no los corras a la vez.
#
# Uso (desde la carpeta del proyecto ya descomprimida, en PowerShell):
#   powershell -ExecutionPolicy Bypass -File .\ejecutar_local.ps1

$ErrorActionPreference = "Stop"
$raiz = $PSScriptRoot
$secreto = "dev-secret-local"

function Fallar($mensaje) {
    Write-Host ""
    Write-Host "ERROR: $mensaje" -ForegroundColor Red
    exit 1
}

# Busca un Python compatible (3.10 - 3.13). Las dependencias fijadas del
# proyecto NO se pueden instalar en Python 3.14 o superior.
function Buscar-Python {
    $ErrorActionPreference = "Continue"
    if (Get-Command py -ErrorAction SilentlyContinue) {
        foreach ($v in "3.12", "3.13", "3.11", "3.10") {
            try {
                $exe = & py "-$v" -c "import sys; print(sys.executable)" 2>$null
                if ($LASTEXITCODE -eq 0 -and $exe) { return ([string]$exe).Trim() }
            } catch { }
        }
    }
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) {
        try {
            $ok = & $cmd.Source -c "import sys; print(int((3,10) <= sys.version_info[:2] <= (3,13)))" 2>$null
            if ($LASTEXITCODE -eq 0 -and ([string]$ok).Trim() -eq "1") { return $cmd.Source }
        } catch { }
    }
    return $null
}

$py = Buscar-Python
if (-not $py) {
    Fallar ("No se encontro un Python compatible (3.10 a 3.13).`n" +
            "Instala Python 3.12 desde https://www.python.org/downloads/ (marca 'Add python.exe to PATH').`n" +
            "Si ya lo tenes, puede ser el alias de la Microsoft Store o una version 3.14+.")
}
Write-Host "Usando Python: $py"

# Puertos que necesita el proyecto
foreach ($p in 8000, 8001, 8002, 8080) {
    $uso = Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue
    if ($uso) { Fallar "El puerto $p ya esta en uso. Cerra lo que lo use (o una ejecucion anterior) y reintenta." }
}

function Preparar-Servicio($nombre) {
    $dir = Join-Path $raiz "services\$nombre"
    if (-not (Test-Path "$dir\requirements-local.txt")) {
        Fallar "No existe $dir\requirements-local.txt. Ejecuta el script desde la carpeta del proyecto descomprimida."
    }
    if (-not (Test-Path "$dir\venv\Scripts\python.exe")) {
        Write-Host "Creando entorno virtual de $nombre..."
        & $py -m venv "$dir\venv"
        if ($LASTEXITCODE -ne 0) { Fallar "No se pudo crear el entorno virtual de $nombre." }
    }
    Write-Host "Instalando dependencias de $nombre (puede tardar)..."
    & "$dir\venv\Scripts\python.exe" -m pip install -q -r "$dir\requirements-local.txt"
    if ($LASTEXITCODE -ne 0) {
        Fallar "Fallo la instalacion de dependencias de $nombre (mira el error de pip arriba). Si usas Python 3.14+, instala 3.12."
    }
    New-Item -ItemType Directory -Force "$dir\data" | Out-Null
}

Preparar-Servicio "auth"
Preparar-Servicio "productos"
Preparar-Servicio "movimientos"

# config.js del frontend (URLs + botones de acceso rapido)
@'
window.APP_CONFIG = {
  PRODUCTOS_API_URL: "http://localhost:8000",
  AUTH_API_URL: "http://localhost:8001",
  MOVIMIENTOS_API_URL: "http://localhost:8002",
  USUARIOS_PRUEBA: {
    habilitado: true,
    admin: { email: "admin@test.com", password: "admin12345" },
    empleado: { email: "empleado@test.com", password: "empleado123" }
  }
};
'@ | Set-Content -Encoding UTF8 (Join-Path $raiz "frontend\config.js")

$comunes = "`$env:AUTH_SECRET_KEY='$secreto'; `$env:REDIS_URL='memory://';"

$cmdAuth = "$comunes " +
  "`$env:HABILITAR_CONSULTA_REVOCACION='true'; " +
  "`$env:HABILITAR_USUARIOS_PRUEBA='true'; " +
  "`$env:PRUEBA_ADMIN_EMAIL='admin@test.com'; `$env:PRUEBA_ADMIN_PASSWORD='admin12345'; " +
  "`$env:PRUEBA_EMPLEADO_EMAIL='empleado@test.com'; `$env:PRUEBA_EMPLEADO_PASSWORD='empleado123'; " +
  "cd '$raiz\services\auth'; .\venv\Scripts\python.exe -m uvicorn app.main:app --port 8001"

# Productos y movimientos consultan a auth si un token fue revocado (logout) y
# productos consulta a movimientos si un producto tiene historial antes de borrarlo.
$cmdProductos = "$comunes `$env:AUTH_SERVICE_URL='http://localhost:8001'; `$env:MOVIMIENTOS_SERVICE_URL='http://localhost:8002'; cd '$raiz\services\productos'; .\venv\Scripts\python.exe -m uvicorn app.main:app --port 8000"

$cmdMovimientos = "$comunes `$env:AUTH_SERVICE_URL='http://localhost:8001'; `$env:OUTBOX_RELAY_HABILITADO='false'; `$env:PRODUCTOS_SERVICE_URL='http://localhost:8000'; cd '$raiz\services\movimientos'; .\venv\Scripts\python.exe -m uvicorn app.main:app --port 8002"

$cmdFront = "cd '$raiz\frontend'; & '$py' -m http.server 8080"

# Cada servicio en su propia ventana (cerrala para detenerlo)
Start-Process powershell -ArgumentList "-NoExit", "-Command", $cmdAuth
Start-Process powershell -ArgumentList "-NoExit", "-Command", $cmdProductos
Start-Process powershell -ArgumentList "-NoExit", "-Command", $cmdMovimientos
Start-Process powershell -ArgumentList "-NoExit", "-Command", $cmdFront

# Espera a que auth, productos y movimientos respondan antes de abrir el navegador
Write-Host "Esperando a que los servicios arranquen..."
$listo = $false
foreach ($i in 1..20) {
    Start-Sleep -Seconds 1
    try {
        Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 "http://localhost:8001/health" | Out-Null
        Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 "http://localhost:8000/health" | Out-Null
        Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 "http://localhost:8002/health" | Out-Null
        $listo = $true
        break
    } catch { }
}

Write-Host ""
if ($listo) {
    Write-Host "Listo. Abri http://localhost:8080" -ForegroundColor Green
} else {
    Write-Host "Los servicios tardan en responder: revisa las ventanas que se abrieron por si hay errores." -ForegroundColor Yellow
}
Write-Host "  Auth (Swagger):      http://localhost:8001/docs"
Write-Host "  Productos (Swagger): http://localhost:8000/docs"
Write-Host "  Movimientos (Swagger): http://localhost:8002/docs"
Write-Host "  (Sin RabbitMQ: los avisos de stock bajo no se procesan; los eventos quedan guardados en el outbox de Movimientos)" -ForegroundColor Yellow
Write-Host "  Usuarios de prueba:  admin@test.com / admin12345  |  empleado@test.com / empleado123"
Start-Process "http://localhost:8080"
