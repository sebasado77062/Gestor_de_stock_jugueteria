# Levanta todo el proyecto en Windows SIN Docker y SIN Redis (usa un Redis en
# memoria). Requiere solo Python 3.10+ instalado.
#
# Uso (desde la carpeta del proyecto, en PowerShell):
#   .\ejecutar_local.ps1
# Si Windows bloquea el script:
#   powershell -ExecutionPolicy Bypass -File .\ejecutar_local.ps1

$ErrorActionPreference = "Stop"
$raiz = $PSScriptRoot
$secreto = "dev-secret-local"

function Preparar-Servicio($nombre) {
    $dir = Join-Path $raiz "services\$nombre"
    if (-not (Test-Path "$dir\venv")) {
        Write-Host "Creando entorno virtual de $nombre..."
        python -m venv "$dir\venv"
    }
    Write-Host "Instalando dependencias de $nombre..."
    & "$dir\venv\Scripts\python.exe" -m pip install -q -r "$dir\requirements-local.txt"
    New-Item -ItemType Directory -Force "$dir\data" | Out-Null
}

Preparar-Servicio "auth"
Preparar-Servicio "productos"

# config.js del frontend (URLs + botones de acceso rápido)
@'
window.APP_CONFIG = {
  PRODUCTOS_API_URL: "http://localhost:8000",
  AUTH_API_URL: "http://localhost:8001",
  USUARIOS_PRUEBA: {
    habilitado: true,
    admin: { email: "admin@test.com", password: "admin12345" },
    empleado: { email: "empleado@test.com", password: "empleado123" }
  }
};
'@ | Set-Content -Encoding UTF8 (Join-Path $raiz "frontend\config.js")

$comunes = "`$env:AUTH_SECRET_KEY='$secreto'; `$env:REDIS_URL='memory://';"

$cmdAuth = "$comunes " +
  "`$env:HABILITAR_USUARIOS_PRUEBA='true'; " +
  "`$env:PRUEBA_ADMIN_EMAIL='admin@test.com'; `$env:PRUEBA_ADMIN_PASSWORD='admin12345'; " +
  "`$env:PRUEBA_EMPLEADO_EMAIL='empleado@test.com'; `$env:PRUEBA_EMPLEADO_PASSWORD='empleado123'; " +
  "cd '$raiz\services\auth'; .\venv\Scripts\python.exe -m uvicorn app.main:app --port 8001"

$cmdProductos = "$comunes cd '$raiz\services\productos'; .\venv\Scripts\python.exe -m uvicorn app.main:app --port 8000"

$cmdFront = "cd '$raiz\frontend'; python -m http.server 8080"

# Cada servicio en su propia ventana (cerrala para detenerlo)
Start-Process powershell -ArgumentList "-NoExit", "-Command", $cmdAuth
Start-Process powershell -ArgumentList "-NoExit", "-Command", $cmdProductos
Start-Process powershell -ArgumentList "-NoExit", "-Command", $cmdFront

Start-Sleep -Seconds 4
Write-Host ""
Write-Host "Listo. Abrí http://localhost:8080"
Write-Host "  Auth (Swagger):      http://localhost:8001/docs"
Write-Host "  Productos (Swagger): http://localhost:8000/docs"
Start-Process "http://localhost:8080"
