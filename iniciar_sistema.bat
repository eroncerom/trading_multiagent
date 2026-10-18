@echo off
title Lanzador - Sistema de Trading Cuantitativo
echo =======================================================
echo   Iniciando Sistema de Trading Cuantitativo Agentico
echo =======================================================
echo.

cd /d "c:\proyectos\trading-agent-system"

echo [1/3] Verificando y levantando contenedores Docker (FastAPI + n8n)...
docker compose up -d
if %errorlevel% neq 0 (
    echo [AVISO] Docker no respondio o no esta corriendo. Asegurate de que Docker Desktop este abierto.
) else (
    echo [OK] Contenedores activos en puertos 8000 (Backend) y 5678 (n8n).
)
echo.

echo [2/3] Levantando Panel Web (Frontend Next.js)...
cd frontend
start "Frontend Web - Trading System" cmd /k "npm run dev"
echo [OK] Servidor web iniciado en segundo plano.
echo.

echo [3/3] Abriendo panel de control en tu navegador...
timeout /t 3 /nobreak > nul
start http://localhost:3000

echo.
echo =======================================================
echo   Sistema operativo en: http://localhost:3000
echo   Motor cuantitativo:   http://localhost:8000/docs
echo   Orquestador n8n:      http://localhost:5678
echo =======================================================
echo.
pause
