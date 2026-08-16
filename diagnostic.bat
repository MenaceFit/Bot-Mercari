@echo off
REM Diagnostic complet — a lancer si run.bat ne fonctionne pas.
setlocal
cd /d "%~dp0"
title Mercari Sniper - Diagnostic

set "PY="
if exist ".venv\Scripts\python.exe" (
    set "PY=.venv\Scripts\python.exe"
) else (
    where py >nul 2>&1 && set "PY=py -3"
    if not defined PY where python >nul 2>&1 && set "PY=python"
)

if not defined PY (
    echo.
    echo   [X] Python introuvable sur ce systeme.
    echo       Installe-le depuis https://python.org en cochant "Add Python to PATH".
    echo.
    pause
    exit /b 1
)

%PY% -m mercari_sniper doctor
echo.
pause
endlocal
