@echo off
REM ============================================================
REM  Mercari Sniper - lanceur Windows
REM  Cette fenetre NE SE FERME JAMAIS toute seule.
REM ============================================================
setlocal EnableExtensions
cd /d "%~dp0"
title Mercari Sniper

echo.
echo   ==========================================
echo     Mercari Sniper - demarrage
echo   ==========================================
echo.

set "VENV_PY=.venv\Scripts\python.exe"
if exist "%VENV_PY%" goto :have_venv

REM ---- Aucun environnement virtuel : en creer un ------------
echo   [i] Premiere utilisation, preparation en cours...
echo.

set "BOOT="
py -3 --version >nul 2>&1
if not errorlevel 1 set "BOOT=py -3"
if defined BOOT goto :boot_ok
python --version >nul 2>&1
if not errorlevel 1 set "BOOT=python"
:boot_ok
if not defined BOOT goto :no_python

echo   [i] Creation de l'environnement virtuel (.venv)...
%BOOT% -m venv .venv
if errorlevel 1 goto :venv_failed
if not exist "%VENV_PY%" goto :venv_failed
echo   [OK] Environnement cree.
echo.

:have_venv
set "PY=%VENV_PY%"

REM ---- Le bot est-il utilisable en l'etat ? -----------------
REM  main.py sait trouver le paquet tout seul : seules les
REM  dependances externes sont indispensables.
"%PY%" -c "import httpx, fastapi, uvicorn, yaml, cryptography" >nul 2>&1
if not errorlevel 1 goto :ready

echo   [i] Installation des dependances...
echo       (une a deux minutes la premiere fois^)
echo.
"%PY%" -m pip install --upgrade pip >nul 2>&1

REM  Installation complete : dependances + commande mercari-sniper.
"%PY%" -m pip install -e .
if not errorlevel 1 goto :verify

echo.
echo   [!] Installation du paquet echouee, repli sur les dependances seules.
echo.
"%PY%" -m pip install -r requirements.txt
if errorlevel 1 goto :install_failed

:verify
"%PY%" -c "import httpx, fastapi, uvicorn, yaml, cryptography" >nul 2>&1
if errorlevel 1 goto :install_failed
echo.
echo   [OK] Installation terminee.
echo.

:ready
REM ---- Config absente : on la genere ------------------------
if exist "config.yaml" goto :launch
echo   [i] Creation de config.yaml depuis tes keywords...
"%PY%" main.py init
echo.

:launch
"%PY%" main.py run %*
set "CODE=%ERRORLEVEL%"

echo.
if "%CODE%"=="0" goto :clean_exit
echo   ==========================================
echo     Le bot s'est arrete avec le code %CODE%
echo   ==========================================
echo.
echo   Diagnostic :  diagnostic.bat
echo   Journal    :  logs\sniper.log
goto :fin

:clean_exit
echo   Bot arrete proprement.
goto :fin

REM ============================================================
:no_python
echo.
echo   [X] Python est introuvable sur ce systeme.
echo.
echo       Installe Python 3.10 ou plus depuis https://python.org
echo       IMPORTANT : coche "Add Python to PATH" pendant l'installation,
echo       puis relance ce fichier.
echo.
goto :fin

:venv_failed
echo.
echo   [X] Impossible de creer l'environnement virtuel.
echo.
echo       Essaie manuellement dans ce dossier :
echo           %BOOT% -m venv .venv
echo.
echo       Si le probleme persiste, verifie que tu as les droits
echo       d'ecriture ici : %CD%
echo.
goto :fin

:install_failed
echo.
echo   [X] L'installation des dependances a echoue.
echo.
echo       Verifie ta connexion internet, puis relance ce fichier.
echo       Pour voir le detail de l'erreur, lance a la main :
echo           "%PY%" -m pip install -e .
echo.
goto :fin

:fin
echo.
pause
endlocal
