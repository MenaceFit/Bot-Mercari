#!/usr/bin/env bash
# Mercari Sniper — lanceur Linux / macOS
set -uo pipefail
cd "$(dirname "$0")"

echo
echo "  =========================================="
echo "    Mercari Sniper — démarrage"
echo "  =========================================="
echo

VENV_PY=".venv/bin/python"

# ── Environnement virtuel ─────────────────────────────────────────────────
if [[ ! -x "$VENV_PY" ]]; then
    echo "  [i] Première utilisation, préparation en cours…"
    echo

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

    echo "  [i] Création de l'environnement virtuel (.venv)…"
    if ! "$BOOT" -m venv .venv; then
        echo "  [X] Impossible de créer l'environnement virtuel."
        exit 1
    fi
    echo "  [OK] Environnement créé."
    echo
fi

PY="$VENV_PY"

# ── Dépendances ───────────────────────────────────────────────────────────
# main.py sait trouver le paquet tout seul (layout src/) : seules les
# dépendances externes sont indispensables au lancement.
if ! "$PY" -c 'import httpx, fastapi, uvicorn, yaml, cryptography' >/dev/null 2>&1; then
    echo "  [i] Installation des dépendances…"
    echo "      (une à deux minutes la première fois)"
    echo
    "$PY" -m pip install --upgrade pip >/dev/null 2>&1

    # Installation complète : dépendances + commande `mercari-sniper`.
    if ! "$PY" -m pip install -e .; then
        echo
        echo "  [!] Installation du paquet échouée, repli sur les dépendances seules."
        echo
        if ! "$PY" -m pip install -r requirements.txt; then
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

# ── Configuration ─────────────────────────────────────────────────────────
if [[ ! -f config.yaml ]]; then
    echo "  [i] Création de config.yaml depuis tes keywords…"
    "$PY" main.py init
    echo
fi

# ── Lancement ─────────────────────────────────────────────────────────────
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
