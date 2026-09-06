#!/usr/bin/env bash
# Buyee Radar — lanceur Linux / macOS
set -uo pipefail
cd "$(dirname "$0")"

echo
echo "  ================================================="
echo "    Buyee Radar — scanner multi-marketplace"
echo "  ================================================="
echo

if command -v python3 >/dev/null 2>&1; then BOOT=python3
elif command -v python >/dev/null 2>&1; then BOOT=python
else
    echo "  [X] Python 3 introuvable. Installe-le, puis relance ce script."
    exit 1
fi

if ! "$BOOT" -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)'; then
    echo "  [X] Python 3.10+ requis (détecté : $("$BOOT" --version 2>&1))"
    exit 1
fi

VENV_PY=".venv/bin/python"
if [[ ! -x "$VENV_PY" ]]; then
    echo "  [i] Première utilisation, préparation de l'environnement…"
    "$BOOT" -m venv .venv || true
fi

# Un venv peut exister SANS pip (sur Debian/Ubuntu, python3-venv est un
# paquet séparé et l'amorçage échoue en silence). Vérifier l'exécutable ne
# suffit donc pas : on teste pip lui-même.
PY=""
PIP_USER=""
if [[ -x "$VENV_PY" ]] && "$VENV_PY" -m pip --version >/dev/null 2>&1; then
    PY="$VENV_PY"
elif [[ -x "$VENV_PY" ]]; then
    echo "  [i] pip absent de l'environnement, réparation…"
    if "$VENV_PY" -m ensurepip --default-pip >/dev/null 2>&1 \
       && "$VENV_PY" -m pip --version >/dev/null 2>&1; then
        PY="$VENV_PY"
    else
        rm -rf .venv
        "$BOOT" -m venv .venv >/dev/null 2>&1 || true
        [[ -x "$VENV_PY" ]] && "$VENV_PY" -m pip --version >/dev/null 2>&1 && PY="$VENV_PY"
    fi
fi
if [[ -z "$PY" ]]; then
    echo "  [!] Impossible d'obtenir pip dans l'environnement virtuel."
    if ! "$BOOT" -m pip --version >/dev/null 2>&1; then
        echo "  [X] pip introuvable même dans le Python du système."
        echo "      Debian/Ubuntu :  sudo apt install python3-venv python3-pip"
        exit 1
    fi
    echo "      Repli sur le Python du système (installation utilisateur)."
    PY="$BOOT"; PIP_USER="--user"
fi

if ! "$PY" -c 'import httpx, yaml, selectolax, fastapi, uvicorn' >/dev/null 2>&1; then
    echo "  [i] Installation des dépendances (une à deux minutes)…"
    "$PY" -m pip install --upgrade pip >/dev/null 2>&1
    if ! "$PY" -m pip install $PIP_USER -e .; then
        echo "  [!] Installation du paquet échouée, repli sur requirements.txt"
        "$PY" -m pip install $PIP_USER -r requirements.txt || {
            echo "  [X] Installation impossible. Vérifie ta connexion."
            exit 1
        }
    fi
    echo "  [OK] Installation terminée."
    echo
fi

[[ -f radar.yaml ]] || { echo "  [i] Création de radar.yaml…"; "$PY" -m buyee_radar init; echo; }

# Sans sélecteurs, les sources réelles ne ramènent RIEN. La calibration les
# découvre sur cette machine, où les sites sont joignables. Elle ne se
# relance pas si une source est déjà calibrée.
# Inutile en mode démo : les sources simulées n'ont pas de sélecteurs à
# découvrir, et on ne veut surtout pas d'appel réseau dans un mode qui
# promet de n'en faire aucun.
DEMO=0
for arg in "$@"; do [[ "$arg" == "--demo" ]] && DEMO=1; done

if [[ $DEMO -eq 0 ]] && ! { echo "  [i] Vérification des sources…"; \
     "$PY" -m buyee_radar calibrate --if-needed; }; then
    echo
    echo "  [!] Aucune source réelle n'a pu être calibrée."
    echo "      Le scanner démarre quand même. Pour un essai hors ligne :"
    echo "        ./run.sh --demo"
    echo
fi

echo "  Dashboard : http://127.0.0.1:8899"
echo "  (Ctrl+C pour arrêter)"
echo

# Le navigateur s'ouvre en parallèle : l'API met une seconde à répondre, et
# on ne veut surtout pas retarder le démarrage du scanner pour ça.
if command -v xdg-open >/dev/null 2>&1; then OPEN=xdg-open
elif command -v open >/dev/null 2>&1; then OPEN=open
else OPEN=""; fi
[[ -n "$OPEN" ]] && ( sleep 3; "$OPEN" http://127.0.0.1:8899 >/dev/null 2>&1 ) &

"$PY" -m buyee_radar run "$@"
code=$?
echo
[[ $code -ne 0 ]] && echo "  Arrêt avec le code $code. Diagnostic : $PY -m buyee_radar doctor"
exit $code
