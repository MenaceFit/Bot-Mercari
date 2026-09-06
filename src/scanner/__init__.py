"""Surface de test en ligne de commande du scanner Buyee.

Trois commandes, exactement celles du cahier des charges :

    python -m scanner.test_sources          état réel de chaque source
    python -m scanner.test_query "nike trail"   une requête, toutes les sources
    python -m scanner.test_live             60 s de surveillance en direct

Elles n'ont aucune logique propre : elles appellent `buyee_radar`. Leur
raison d'être est la vérification — pouvoir répondre, preuve à l'appui, à
« mon scanner peut-il vraiment récupérer les résultats de cette source ? »
sans lancer le dashboard.
"""

__all__ = ["main_sources", "main_query", "main_live"]


def main_sources(argv=None):
    from .test_sources import main
    return main(argv)


def main_query(argv=None):
    from .test_query import main
    return main(argv)


def main_live(argv=None):
    from .test_live import main
    return main(argv)
