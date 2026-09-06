#!/usr/bin/env bash
# Mercari Sniper — lanceur Linux / macOS
set -uo pipefail
cd "$(dirname "$0")"

echo
echo "  ############################################################"
echo "  #  ATTENTION : ceci est l'ANCIENNE version (Mercari v2).   #"
echo "  #  Elle ne scanne QUE Mercari et n'a PAS Telegram.         #"
echo "  #                                                          #"
echo "  #  La version actuelle est Buyee Radar :                   #"
echo "  #     interromps (Ctrl+C) et lance  ./run.sh               #"
echo "  ############################################################"
echo
read -r -t 15 -p "  Lancer quand même l'ancienne version ? [O/n] " answer || answer=O
case "${answer:-O}" in [nN]*) echo; exit 0 ;; esac
echo

echo
echo "  =========================================="
echo "    Mercari Sniper — démarrage"
echo "  =========================================="
echo

PIP_USER=""

# ── 1. Python du système (sert aussi aux réparations) ─────────────────────
if command -v python3 >/dev/null 2>&1; then
    BOOT="python3"
elif command -v python >/dev/null 2>&1; then
    BOOT="python"
else
    echo "  [X] Python 3 est introuvable. Installe-le, puis relance ce script."
    exit 1
fi

if ! "$BOOT" -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)'; then
    echo "  [X] Python 3.10+ requis (détecté: $("$BOOT" --version 2>&1))"
    exit 1
fi

VENV_PY=".venv/bin/python"

# ── 2. Environnement virtuel ──────────────────────────────────────────────
if [[ ! -x "$VENV_PY" ]]; then
    echo "  [i] Première utilisation, préparation en cours…"
    echo "  [i] Création de l'environnement virtuel (.venv)…"
    "$BOOT" -m venv .venv || true
fi

# ── 3. pip est-il réellement utilisable ? ─────────────────────────────────
# Un venv peut exister SANS pip : sur Debian/Ubuntu le paquet python3-venv
# est séparé, et l'amorçage échoue silencieusement. Vérifier la présence de
# l'exécutable ne suffit donc pas.
PY=""
if [[ -x "$VENV_PY" ]] && "$VENV_PY" -m pip --version >/dev/null 2>&1; then
    PY="$VENV_PY"
elif [[ -x "$VENV_PY" ]]; then
    echo "  [i] pip absent de l'environnement, réparation…"
    if "$VENV_PY" -m ensurepip --default-pip >/dev/null 2>&1 \
       && "$VENV_PY" -m pip --version >/dev/null 2>&1; then
        echo "  [OK] pip restauré."
        echo
        PY="$VENV_PY"
    else
        echo "  [i] Réparation impossible, reconstruction de l'environnement…"
        rm -rf .venv
        "$BOOT" -m venv .venv >/dev/null 2>&1 || true
        if [[ -x "$VENV_PY" ]] && "$VENV_PY" -m pip --version >/dev/null 2>&1; then
            echo "  [OK] Environnement reconstruit."
            echo
            PY="$VENV_PY"
        fi
    fi
fi

# ── 4. Dernier recours : le Python du système ─────────────────────────────
# Le bot n'a pas besoin d'un environnement virtuel, seulement de Python et
# de ses dépendances. Mieux vaut fonctionner sans isolation que pas du tout.
if [[ -z "$PY" ]]; then
    echo
    echo "  [!] Impossible d'obtenir pip dans l'environnement virtuel."
    if ! "$BOOT" -m pip --version >/dev/null 2>&1; then
        echo
        echo "  [X] pip est introuvable, y compris dans le Python du système."
        echo "      Sur Debian/Ubuntu :  sudo apt install python3-venv python3-pip"
        echo "      Ailleurs          :  $BOOT -m ensurepip --default-pip"
        echo
        exit 1
    fi
    echo "      Repli sur le Python du système (installation utilisateur)."
    echo
    PY="$BOOT"
    PIP_USER="--user"
fi

# ── 5. Dépendances ────────────────────────────────────────────────────────
if ! "$PY" -c 'import httpx, fastapi, uvicorn, yaml, cryptography' >/dev/null 2>&1; then
    echo "  [i] Installation des dépendances…"
    echo "      (une à deux minutes la première fois)"
    echo
    "$PY" -m pip install --upgrade pip >/dev/null 2>&1

    if ! "$PY" -m pip install $PIP_USER -e .; then
        echo
        echo "  [!] Installation du paquet échouée, repli sur les dépendances seules."
        echo
        if ! "$PY" -m pip install $PIP_USER -r requirements.txt; then
            echo
            echo "  [X] L'installation a échoué. Vérifie ta connexion, puis relance."
            exit 1
        fi
    fi

    if ! "$PY" -c 'import httpx, fastapi, uvicorn, yaml, cryptography' >/dev/null 2>&1; then
        echo
        echo "  [X] Les dépendances restent introuvables après installation."
        echo "      Lance à la main :  $PY -m pip install -r requirements.txt"
        exit 1
    fi
    echo
    echo "  [OK] Installation terminée."
    echo
fi

# ── 6. Configuration ──────────────────────────────────────────────────────
if [[ ! -f config.yaml ]]; then
    echo "  [i] Création de config.yaml depuis tes keywords…"
    "$PY" main.py init
    echo
fi

# ── 7. Lancement ──────────────────────────────────────────────────────────
"$PY" main.py run "$@"
code=$?

echo
if [[ $code -ne 0 ]]; then
    echo "  Le bot s'est arrêté avec le code $code."
    echo "  Diagnostic :  $PY main.py doctor"
    echo "  Journal     :  logs/sniper.log"
else
    echo "  Bot arrêté proprement."
fi
exit $code
