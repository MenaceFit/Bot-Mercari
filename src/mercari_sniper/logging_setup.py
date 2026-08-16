"""Configuration du logging : console + fichier rotatif."""

from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path


def setup_logging(level: str = "INFO", log_dir: str | Path = "logs") -> Path | None:
    """Installe les handlers. Renvoie le chemin du fichier de log, si écrit.

    Un dossier de logs non inscriptible ne doit jamais empêcher le bot de
    démarrer — c'est une des causes de fermeture instantanée de la v1, qui
    ouvrait son FileHandler au niveau module.
    """
    root = logging.getLogger()
    root.setLevel(getattr(logging, str(level).upper(), logging.INFO))

    for handler in list(root.handlers):
        root.removeHandler(handler)

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)-7s] %(message)s", "%H:%M:%S")
    )
    root.addHandler(console)

    # Les libs HTTP sont très bavardes en DEBUG.
    for noisy in ("httpx", "httpcore", "hpack", "websockets", "uvicorn.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    try:
        log_dir = Path(log_dir)
        log_dir.mkdir(parents=True, exist_ok=True)
        path = log_dir / "sniper.log"
        file_handler = logging.handlers.RotatingFileHandler(
            path, maxBytes=5_000_000, backupCount=3, encoding="utf-8"
        )
        file_handler.setFormatter(
            logging.Formatter(
                "%(asctime)s [%(levelname)-7s] %(name)s: %(message)s",
                "%Y-%m-%d %H:%M:%S",
            )
        )
        root.addHandler(file_handler)
        return path
    except OSError as exc:
        root.warning("journalisation fichier désactivée (%s)", exc)
        return None
