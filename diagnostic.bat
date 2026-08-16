@echo off
REM Diagnostic complet - a lancer si run.bat ne fonctionne pas.
setlocal EnableExtensions
cd /d "%~dp0"
title Mercari Sniper - Diagnostic

echo.
echo   ==========================================
echo     Mercari Sniper - Diagnostic
echo   ==========================================
echo.

set "PY="
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
if defined PY goto :run

py -3 --version >nul 2>&1
if not errorlevel 1 set "PY=py -3"
if defined PY goto :run
python --version >nul 2>&1
if not errorlevel 1 set "PY=python"

if not defined PY (
    echo   [X] Python introuvable sur ce systeme.
    echo.
    echo       Installe-le depuis https://python.org en cochant
    echo       "Add Python to PATH", puis relance ce fichier.
    echo.
    pause
    exit /b 1
)

echo   [!] Aucun environnement virtuel : diagnostic sur le Python systeme.
echo       Lance run.bat pour creer l'environnement.
echo.

:run
REM main.py fonctionne meme si le paquet n'est pas installe.
%PY% main.py doctor
echo.
pause
endlocal
