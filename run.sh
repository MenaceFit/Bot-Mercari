#!/usr/bin/env bash
# Mercari Sniper — lanceur Linux / macOS
set -uo pipefail
cd "$(dirname "$0")"

echo
echo "  =========================================="
echo "    Mercari Sniper — démarrage"
echo "  =========================================="
echo

# ── Choix de l'interpréteur ───────────────────────────────────────────────
if [[ -x ".venv/bin/python" ]]; then
    PY=".venv/bin/python"
    echo "  [i] Environnement virtuel détecté"
elif command -v python3 >/dev/null 2>&1; then
    PY="python3"
else
    echo "  [X] Python 3 est introuvable."
    echo "      Installe-le, puis relance ce script."
    exit 1
fi

# ── Version minimale ──────────────────────────────────────────────────────
if ! "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)'; then
    echo "  [X] Python 3.10+ requis (détecté: $("$PY" --version 2>&1))"
    exit 1
fi

# ── Dépendances ───────────────────────────────────────────────────────────
if ! "$PY" -c 'import httpx, fastapi, uvicorn, yaml, cryptography' >/dev/null 2>&1; then
    echo "  [i] Première utilisation : installation des dépendances…"
    echo
    if [[ ! -d .venv ]]; then
        "$PY" -m venv .venv && PY=".venv/bin/python"
        echo "  [i] Environnement virtuel créé dans .venv/"
    fi
    "$PY" -m pip install --upgrade pip >/dev/null 2>&1
    if ! "$PY" -m pip install -r requirements.txt; then
        echo
        echo "  [X] L'installation des dépendances a échoué."
        exit 1
    fi
    echo
    echo "  [OK] Dépendances installées."
    echo
fi

# ── Configuration ─────────────────────────────────────────────────────────
if [[ ! -f config.yaml ]]; then
    echo "  [i] Aucune config.yaml : création depuis tes fichiers existants…"
    "$PY" -m mercari_sniper init
    echo
fi

# ── Lancement ─────────────────────────────────────────────────────────────
"$PY" -m mercari_sniper run "$@"
code=$?

echo
if [[ $code -ne 0 ]]; then
    echo "  Le bot s'est arrêté avec le code $code."
    echo "  Diagnostic :  $PY -m mercari_sniper doctor"
    echo "  Journal     :  logs/sniper.log"
else
    echo "  Bot arrêté proprement."
fi
exit $code
