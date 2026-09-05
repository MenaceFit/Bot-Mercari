#!/usr/bin/env bash
# Snipe — lanceur Linux / macOS
set -uo pipefail
cd "$(dirname "$0")"

echo
echo "  =========================================="
echo "    Snipe — monitoring multi-marketplace"
echo "  =========================================="
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

if ! "$PY" -c 'import httpx, yaml, cryptography, selectolax' >/dev/null 2>&1; then
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

[[ -f config.yaml ]] || { echo "  [i] Création de config.yaml…"; "$PY" -m snipe init; echo; }

"$PY" -m snipe run "$@"
code=$?
echo
[[ $code -ne 0 ]] && echo "  Arrêt avec le code $code. Diagnostic : $PY -m snipe doctor"
exit $code
