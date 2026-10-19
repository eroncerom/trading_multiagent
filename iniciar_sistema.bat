@echo off
title Lanzador - Sistema de Trading Cuantitativo
echo =======================================================
echo   Iniciando Sistema de Trading Cuantitativo Agentico
echo =======================================================
echo.

cd /d "c:\proyectos\trading-agent-system"

echo [1/3] Comprobando si Docker Desktop esta activo...
docker info >nul 2>&1
if %errorlevel% neq 0 (
    echo [AVISO] Docker Desktop no esta listo o se esta iniciando.
    echo Intentando abrir Docker Desktop...
    start "" "C:\Program Files\Docker\Docker\Docker Desktop.exe"
    echo Esperando a que el motor de Docker responda...
    
    :wait_docker
    timeout /t 3 /nobreak >nul
    docker info >nul 2>&1
    if %errorlevel% neq 0 (
        echo   ...esperando a Docker Desktop...
        goto wait_docker
    )
)

echo [OK] Docker Desktop esta activo.
echo Levantando contenedores (FastAPI + n8n)...
docker compose up -d
echo [OK] Contenedores operativos en puertos 8000 (Backend) y 5678 (n8n).
echo.

echo [2/3] Levantando Panel Web (Frontend Next.js)...
cd frontend
start "Frontend Web - Trading System" cmd /k "npm run dev"
echo [OK] Servidor web iniciado. Esperando a que compile...
timeout /t 5 /nobreak > nul
echo.

echo [3/3] Abriendo panel de control en tu navegador...
start http://localhost:3000

echo.
echo =======================================================
echo   Sistema operativo en: http://localhost:3000
echo   Motor cuantitativo:   http://localhost:8000/docs
echo   Orquestador n8n:      http://localhost:5678
echo =======================================================
echo.
pause
