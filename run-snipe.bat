@echo off
REM Snipe - lanceur Windows
setlocal
cd /d "%~dp0"

echo.
echo   ==========================================
echo     Snipe - monitoring multi-marketplace
echo   ==========================================
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

"%PY%" -c "import httpx, yaml, cryptography, selectolax" >nul 2>&1
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

if not exist config.yaml (
  echo   [i] Creation de config.yaml...
  "%PY%" -m snipe init
  echo.
)

"%PY%" -m snipe run %*
echo.
echo   Bot arrete. Journal : logs\snipe.log
pause
