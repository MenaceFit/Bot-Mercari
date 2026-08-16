#!/usr/bin/env python3
"""Lanceur autonome — fonctionne sans installer le paquet.

Le projet utilise un layout `src/`, donc `python -m mercari_sniper` échoue
tant que `pip install -e .` n'a pas été fait. Ce fichier court-circuite le
problème en ajoutant `src/` au chemin d'import : il suffit que les
dépendances soient présentes.

    python main.py doctor
    python main.py run
"""

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent / "src"
if SRC.is_dir():
    sys.path.insert(0, str(SRC))

try:
    from mercari_sniper.cli import main
except ImportError as exc:
    # Ne jamais mourir sur une trace illisible : on explique quoi faire.
    print()
    print("=" * 64)
    print("  IMPOSSIBLE DE CHARGER LE BOT")
    print("=" * 64)
    print(f"  Détail : {exc}")
    print()
    if not SRC.is_dir():
        print(f"  Le dossier {SRC} est introuvable.")
        print("  Lance ce script depuis la racine du projet décompressé,")
        print("  celle qui contient 'src', 'run.bat' et 'requirements.txt'.")
    else:
        print("  Il manque probablement des dépendances. Installe-les avec :")
        print()
        print(f"      {Path(sys.executable).name} -m pip install -r requirements.txt")
    print("=" * 64)
    print()
    try:
        input("  Appuie sur Entrée pour fermer… ")
    except Exception:
        pass
    sys.exit(1)

if __name__ == "__main__":
    sys.exit(main())
