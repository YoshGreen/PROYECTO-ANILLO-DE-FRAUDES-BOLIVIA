@echo off
setlocal
title Sistema Antifraude Yape Bolivia - Base de Datos III
color 0B

echo =================================================================
echo   SISTEMA DE DETECCION DE ANILLOS DE FRAUDE - YAPE BOLIVIA
echo   Base de Datos III - MongoDB & Redis (100%% Local)
echo =================================================================
echo.

:: Detectar ejecutable de Python
set "PY_CMD="
if exist "venv\Scripts\python.exe" (
    set "PY_CMD=venv\Scripts\python.exe"
) else (
    where python >nul 2>nul
    if %errorlevel% equ 0 (
        set "PY_CMD=python"
    ) else (
        where py >nul 2>nul
        if %errorlevel% equ 0 (
            set "PY_CMD=py"
        ) else (
            if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" (
                set "PY_CMD=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
            )
        )
    )
)

if "%PY_CMD%"=="" (
    color 0C
    echo [ERROR] No se encontro Python instalado en el sistema.
    echo Por favor instala Python 3.10 o superior marcando "Add Python to PATH".
    echo Descarga: https://www.python.org/downloads/
    echo.
    pause
    exit /b 1
)

echo Usando: %PY_CMD%
echo.

:: Verificar dependencias rapidas
%PY_CMD% -c "import flask, pymongo, redis" >nul 2>nul
if %errorlevel% neq 0 (
    echo Instalando dependencias requeridas (Flask, PyMongo, Redis)...
    %PY_CMD% -m pip install -r requirements.txt
    if %errorlevel% neq 0 (
        echo [AVISO] Fallo la instalacion automatica con pip. Intenta ejecutar manualmente: pip install -r requirements.txt
    )
)

echo.
echo Iniciando servidor Flask...
echo Abre tu navegador en: http://127.0.0.1:5000
echo Para detener el servidor presiona Ctrl + C.
echo.
%PY_CMD% app.py

pause
