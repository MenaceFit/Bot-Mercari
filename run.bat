@echo off
REM ============================================================
REM  Mercari Sniper - lanceur Windows
REM  Cette fenetre NE SE FERME JAMAIS toute seule : en cas
REM  d'erreur, le message reste affiche jusqu'a ce que tu
REM  appuies sur une touche.
REM ============================================================
setlocal
cd /d "%~dp0"
title Mercari Sniper

echo.
echo   ==========================================
echo     Mercari Sniper - demarrage
echo   ==========================================
echo.

REM --- Trouver un Python utilisable -------------------------
set "PY="
if exist ".venv\Scripts\python.exe" (
    set "PY=.venv\Scripts\python.exe"
    echo   [i] Environnement virtuel detecte
) else (
    where py >nul 2>&1 && set "PY=py -3"
    if not defined PY (
        where python >nul 2>&1 && set "PY=python"
    )
)

if not defined PY (
    echo.
    echo   [X] Python est introuvable.
    echo.
    echo       Installe Python 3.10 ou plus depuis https://python.org
    echo       IMPORTANT : coche "Add Python to PATH" pendant l'installation.
    echo.
    goto :fin
)

REM --- Premiere installation --------------------------------
%PY% -c "import httpx, fastapi, uvicorn, yaml, cryptography" >nul 2>&1
if errorlevel 1 (
    echo   [i] Premiere utilisation : installation des dependances...
    echo       (cela peut prendre une minute^)
    echo.
    %PY% -m pip install --upgrade pip >nul 2>&1
    %PY% -m pip install -r requirements.txt
    if errorlevel 1 (
        echo.
        echo   [X] L'installation des dependances a echoue.
        echo       Verifie ta connexion internet, puis relance ce fichier.
        echo.
        goto :fin
    )
    echo.
    echo   [OK] Dependances installees.
    echo.
)

REM --- Config absente : on la genere ------------------------
if not exist "config.yaml" (
    echo   [i] Aucune config.yaml : creation depuis tes fichiers existants...
    %PY% -m mercari_sniper init
    echo.
)

REM --- Lancement --------------------------------------------
%PY% -m mercari_sniper run %*
set "CODE=%ERRORLEVEL%"

echo.
if not "%CODE%"=="0" (
    echo   ==========================================
    echo     Le bot s'est arrete avec le code %CODE%
    echo   ==========================================
    echo.
    echo   Pour diagnostiquer, lance :  diagnostic.bat
    echo   Le journal complet est dans :  logs\sniper.log
) else (
    echo   Bot arrete proprement.
)

:fin
echo.
pause
endlocal
