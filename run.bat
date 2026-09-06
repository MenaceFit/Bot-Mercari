@echo off
REM Buyee Radar - lanceur Windows
setlocal
cd /d "%~dp0"

echo.
echo   =================================================
echo     Buyee Radar - scanner multi-marketplace
echo   =================================================
echo.

set PY=.venv\Scripts\python.exe
set BOOT=python
set PIPUSER=

%BOOT% --version >nul 2>&1
if errorlevel 1 (
  echo   [X] Python introuvable. Installe-le depuis python.org
  echo       en cochant "Add python.exe to PATH".
  pause & exit /b 1
)

if not exist "%PY%" (
  echo   [i] Premiere utilisation, preparation de l'environnement...
  %BOOT% -m venv .venv
)

REM Un venv peut exister SANS pip : on teste pip, on ne le suppose pas.
"%PY%" -m pip --version >nul 2>&1
if errorlevel 1 (
  echo   [i] pip absent, reparation...
  "%PY%" -m ensurepip --default-pip >nul 2>&1
  "%PY%" -m pip --version >nul 2>&1
  if errorlevel 1 (
    echo   [i] Reconstruction de l'environnement...
    rmdir /s /q .venv
    %BOOT% -m venv .venv
    "%PY%" -m pip --version >nul 2>&1
    if errorlevel 1 (
      echo   [!] Repli sur le Python du systeme.
      set PY=%BOOT%
      set PIPUSER=--user
    )
  )
)

"%PY%" -c "import httpx, yaml, selectolax, fastapi, uvicorn" >nul 2>&1
if errorlevel 1 (
  echo   [i] Installation des dependances ^(une a deux minutes^)...
  echo.
  "%PY%" -m pip install --upgrade pip >nul 2>&1
  "%PY%" -m pip install %PIPUSER% -e .
  if errorlevel 1 (
    echo   [!] Repli sur requirements.txt
    "%PY%" -m pip install %PIPUSER% -r requirements.txt
    if errorlevel 1 (
      echo   [X] Installation impossible. Verifie ta connexion.
      pause & exit /b 1
    )
  )
  echo   [OK] Installation terminee.
  echo.
)

if not exist radar.yaml (
  echo   [i] Creation de radar.yaml...
  "%PY%" -m buyee_radar init
  echo.
)

REM Sans selecteurs, les sources reelles ne ramenent RIEN. La calibration
REM les decouvre sur cette machine, ou les sites sont joignables. Elle ne
REM se relance pas si une source est deja calibree.
REM Inutile en mode demo : les sources simulees n'ont pas de selecteurs a
REM decouvrir, et ce mode promet de ne faire aucun appel reseau.
echo %* | find "--demo" >nul
if not errorlevel 1 goto :skip_calibrate

echo   [i] Verification des sources...
"%PY%" -m buyee_radar calibrate --if-needed
if errorlevel 1 (
  echo.
  echo   [!] Aucune source reelle n'a pu etre calibree.
  echo       Le scanner demarre quand meme. Pour un essai hors ligne :
  echo         run.bat --demo
  echo.
)
:skip_calibrate

echo   Dashboard : http://127.0.0.1:8899
echo   ^(Ctrl+C pour arreter^)
echo.

REM Le navigateur s'ouvre en parallele : l'API met une seconde a repondre,
REM et on ne veut surtout pas retarder le demarrage du scanner pour ca.
start "" /b cmd /c "timeout /t 3 >nul & start http://127.0.0.1:8899"

"%PY%" -m buyee_radar run %*
echo.
echo   Radar arrete. Journal : logs\radar.jsonl
pause
