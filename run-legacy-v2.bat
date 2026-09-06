@echo off
REM ============================================================
REM  Mercari Sniper - lanceur Windows
REM  Cette fenetre NE SE FERME JAMAIS toute seule.
REM ============================================================
setlocal EnableExtensions
cd /d "%~dp0"
title Mercari Sniper


echo.
echo   ############################################################
echo   #  ATTENTION : ceci est l'ANCIENNE version (Mercari v2).   #
echo   #  Elle ne scanne QUE Mercari et n'a PAS Telegram.         #
echo   #                                                          #
echo   #  La version actuelle est Buyee Radar :                   #
echo   #     ferme cette fenetre et lance  run.bat                #
echo   ############################################################
echo.
choice /C ON /N /T 15 /D O /M "  Lancer quand meme l'ancienne version ? [O/N] (O dans 15s) "
if errorlevel 2 exit /b 0
echo.
echo   ==========================================
echo     Mercari Sniper - demarrage
echo   ==========================================
echo.

REM ---- 1. Python du systeme (toujours resolu : sert aussi aux reparations)
set "BOOT="
py -3 --version >nul 2>&1
if not errorlevel 1 set "BOOT=py -3"
if defined BOOT goto :boot_ok
python --version >nul 2>&1
if not errorlevel 1 set "BOOT=python"
:boot_ok
if not defined BOOT goto :no_python

REM ---- 2. Environnement virtuel -------------------------------
set "VENV_PY=.venv\Scripts\python.exe"
if exist "%VENV_PY%" goto :check_pip

echo   [i] Premiere utilisation, preparation en cours...
echo   [i] Creation de l'environnement virtuel (.venv)...
%BOOT% -m venv .venv
if not exist "%VENV_PY%" goto :venv_failed

REM ---- 3. pip est-il reellement utilisable ? -------------------
REM  Un venv peut exister SANS pip : l'amorcage echoue parfois
REM  (antivirus, ensurepip indisponible). Verifier que python.exe
REM  existe ne suffit donc pas.
:check_pip
set "PY=%VENV_PY%"
%PY% -m pip --version >nul 2>&1
if not errorlevel 1 goto :have_python

echo   [i] pip absent de l'environnement, reparation...
%PY% -m ensurepip --default-pip
%PY% -m pip --version >nul 2>&1
if not errorlevel 1 (
    echo   [OK] pip restaure.
    echo.
    goto :have_python
)

echo   [i] Reparation impossible, reconstruction de l'environnement...
rmdir /s /q .venv >nul 2>&1
%BOOT% -m venv .venv
if not exist "%VENV_PY%" goto :fallback
%PY% -m pip --version >nul 2>&1
if errorlevel 1 goto :fallback
echo   [OK] Environnement reconstruit.
echo.
goto :have_python

REM ---- 4. Dernier recours : le Python du systeme ---------------
REM  Le bot n'a pas besoin d'un environnement virtuel, seulement
REM  de Python et de ses dependances. Mieux vaut fonctionner sans
REM  isolation que ne pas demarrer du tout.
:fallback
echo.
echo   [!] Impossible d'obtenir pip dans l'environnement virtuel.
echo       Repli sur le Python du systeme (installation utilisateur).
echo.
%BOOT% -m pip --version >nul 2>&1
if errorlevel 1 goto :no_pip_anywhere
set "PY=%BOOT%"
set "PIP_USER=--user"

REM ---- 5. Dependances ------------------------------------------
:have_python
%PY% -c "import httpx, fastapi, uvicorn, yaml, cryptography" >nul 2>&1
if not errorlevel 1 goto :ready

echo   [i] Installation des dependances...
echo       (une a deux minutes la premiere fois^)
echo.
%PY% -m pip install --upgrade pip >nul 2>&1

%PY% -m pip install %PIP_USER% -e .
if not errorlevel 1 goto :verify

echo.
echo   [!] Installation du paquet echouee, repli sur les dependances seules.
echo.
%PY% -m pip install %PIP_USER% -r requirements.txt
if errorlevel 1 goto :install_failed

:verify
%PY% -c "import httpx, fastapi, uvicorn, yaml, cryptography" >nul 2>&1
if errorlevel 1 goto :install_failed
echo.
echo   [OK] Installation terminee.
echo.

:ready
if exist "config.yaml" goto :launch
echo   [i] Creation de config.yaml depuis tes keywords...
%PY% main.py init
echo.

:launch
%PY% main.py run %*
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
echo       Verifie aussi que tu as les droits d'ecriture ici :
echo           %CD%
echo.
goto :fin

:no_pip_anywhere
echo.
echo   [X] pip est introuvable, y compris dans le Python du systeme.
echo.
echo       Repare l'installation de Python :
echo         Parametres ^> Applications ^> Python ^> Modifier ^> Repair
echo       en veillant a cocher "pip".
echo.
echo       Ou, en ligne de commande :
echo           %BOOT% -m ensurepip --default-pip
echo.
goto :fin

:install_failed
echo.
echo   [X] L'installation des dependances a echoue.
echo.
echo       Verifie ta connexion internet, puis relance ce fichier.
echo       Si un antivirus est actif, autorise ce dossier.
echo.
echo       Pour voir le detail de l'erreur, lance a la main :
echo           %PY% -m pip install -r requirements.txt
echo.
goto :fin

:fin
echo.
pause
endlocal
