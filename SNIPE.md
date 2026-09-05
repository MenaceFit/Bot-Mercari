# Snipe — architecture v3

Monitoring multi-marketplace orienté **faible latence**. Détecte les nouvelles
annonces correspondant à des mots-clés, les filtre, les déduplique, et les
pousse vers Telegram / Discord / console.

Ce document explique l'architecture et les décisions. Pour l'installation,
voir la section « Démarrer » plus bas.

---

## Audit de la version précédente

### Ce qui allait déjà

Mesuré, pas supposé :

| Point | État avant refonte |
|---|---|
| Boucle de scan asynchrone | ✅ asyncio de bout en bout |
| Appels bloquants dans le chemin critique | ✅ aucun (`grep` sur `requests.`, `time.sleep`) |
| Réutilisation des connexions | ✅ un client HTTP/2 persistant, keep-alive 90 s |
| Écritures disque dans la boucle | ✅ aucune — SQLite écrit par lots, hors boucle |
| Le scanner attend-il les notifications | ✅ non — `notifier.notify()` sans `await` |

La v1 (celle avec `requests.post()` + `sleep(0.8)` dans la boucle de scan)
avait déjà été corrigée. Le point de départ était donc meilleur que le
cahier des charges ne le supposait.

### Coût réel du pipeline local

Mesuré sur 200 annonces réalistes (japonais + latin + bruit) :

```
Normalisation      0,013 ms / 200 annonces
Filtrage           0,306 ms / 200 annonces
Déduplication      0,104 ms / 200 annonces
─────────────────────────────────────────
Par annonce         2,12 µs
Débit            472 000 annonces/s
```

**Conclusion de l'audit : le pipeline local est négligeable.** Optimiser le
filtrage n'aurait aucun effet mesurable. Le budget de latence est entièrement
consommé par deux choses :

1. le délai d'indexation de la marketplace (hors de portée) ;
2. l'intervalle entre deux scans (le seul vrai levier).

Toute l'architecture v3 découle de ce constat.

### Ce qui manquait vraiment

| Manque | Réponse v3 |
|---|---|
| Une seule marketplace | `BaseSource` + 4 implémentations, ajout sans toucher au cœur |
| Pas de Telegram | `TelegramNotifier`, avec repli texte si la photo est refusée |
| Pas de mesure par étape | T0→T5 mesurés, `--benchmark` détaille chaque étape |
| Cadence unique pour tous les mots-clés | Trois priorités, budget réparti explicitement |
| Pas de health check | Par source, avec détection PANNE / RÉTABLIE |
| Recherche large vs filtrage local non arbitrés | `QueryPlanner`, arbitrage sur mesures |

---

## Buyee : pourquoi le bot ne détecte pas dessus

Buyee est un **proxy d'achat** : il achète pour vous sur des marketplaces
japonaises réservées au marché intérieur (Yahoo Auctions, Mercari, Rakuten,
ZOZOTOWN, Rakuma… une trentaine de sites) et réexpédie à l'international.

Ses pages de recherche sont un **miroir** de celles des marketplaces
sous-jacentes. Il faut donc que :

```
la marketplace publie  →  Buyee récupère et réindexe  →  Buyee affiche
```

Détecter via Buyee ajoute ce délai d'indexation, qu'on ne contrôle pas et
qu'on ne peut pas mesurer, plus un saut réseau. Pour un projet dont le
critère numéro un est la latence, c'est le mauvais sens.

**L'architecture retenue est l'inverse :**

```
détecter sur la marketplace source (au plus près de la publication)
                        ↓
        construire l'URL Buyee correspondante pour l'achat
```

On garde le bénéfice de Buyee — pouvoir acheter depuis l'étranger — sans
payer son délai. Le lien d'achat est calculé pour chaque annonce détectée
(section `buyee` de `config.yaml`).

Une source Buyee reste disponible, à n'activer que pour une marketplace
qu'aucune source directe ne couvre.

### Ce que j'ai pu vérifier, et ce que je n'ai pas pu

| Point | Vérifié ? |
|---|---|
| Buyee agrège 30+ marketplaces dont Mercari, Rakuten, ZOZOTOWN | Oui, par recherche web |
| L'API publique Yahoo Auctions n'est plus ouverte aux tiers | Oui, par recherche web |
| Structure HTML des pages Buyee / Yahoo | **Non** — domaines bloqués |
| Format exact des URL d'achat Buyee | **Non** — domaine bloqué |
| Chemin réseau vers l'API Mercari | **Non** — domaine bloqué |

Les trois domaines répondent `403` au CONNECT de la passerelle réseau de
l'environnement de développement. C'est la raison pour laquelle les sources
HTML sont **pilotées par sélecteurs configurables** plutôt qu'écrites en
dur : un sélecteur faux se corrige dans le YAML en regardant la page dans un
navigateur, sans toucher au code.

---

## Architecture

```
                    ┌─────────────────────┐
                    │  QueryPlanner       │  combien de requêtes, lesquelles
                    └──────────┬──────────┘
                               ▼
                    ┌─────────────────────┐
                    │  Scheduler          │  priorités, budget, anti-dérive
                    └──────────┬──────────┘
                 ┌─────────────┼─────────────┐
                 ▼             ▼             ▼
            ┌─────────┐   ┌─────────┐   ┌─────────┐
            │ Mercari │   │  Yahoo  │   │  Buyee  │   sources indépendantes
            └────┬────┘   └────┬────┘   └────┬────┘
                 └─────────────┼─────────────┘
                               ▼
                      ┌─────────────────┐
                      │   Normalizer    │  NFKC, casse, ponctuation
                      └────────┬────────┘
                               ▼
                      ┌─────────────────┐
                      │  Deduplicator   │  L1 mémoire + L2 SQLite
                      └────────┬────────┘
                               ▼
                      ┌─────────────────┐
                      │  FilterEngine   │  index inversé, ~4 µs/annonce
                      └────────┬────────┘
                               ▼
                        asyncio.Queue        ← le scanner repart ICI
                 ┌─────────────┴─────────────┐
                 ▼                           ▼
           ┌────────────┐              ┌────────────┐
           │  Telegram  │              │  Discord   │   un worker chacun
           └────────────┘              └────────────┘
```

### Arborescence

```
src/snipe/
├── main.py              CLI : run / benchmark / doctor / init
├── app.py               assemblage config → scanner
├── benchmark.py         mesures par étape
├── config/settings.py   YAML + secrets d'environnement
├── sources/
│   ├── base.py          BaseSource, Listing, horodatages T0→T5
│   ├── mercari.py       API v2, DPoP, HTTP/2 persistant
│   ├── html_source.py   scraper générique piloté par sélecteurs
│   ├── yahoo_auction.py préréglage Yahoo (désactivé par défaut)
│   ├── buyee.py         liens d'achat + source optionnelle
│   ├── simulator.py     marketplace fictive, pour tests et benchmark
│   └── dpop.py          signature ES256
├── core/
│   ├── normalizer.py    normalisation + comparaison de termes
│   ├── keywords.py      recherche distante vs filtrage local
│   ├── planner.py       ⚑ l'arbitrage économie / exhaustivité
│   ├── scheduler.py     priorités, budget, ordonnancement sans dérive
│   ├── scanner.py       la boucle, la détection de trou
│   ├── filters.py       filtrage local
│   ├── deduplicator.py  cache L1 borné
│   └── metrics.py       latences, percentiles, santé des sources
├── notifications/
│   ├── base.py          ⚑ la file qui découple scanner et notifications
│   ├── telegram.py
│   ├── discord.py
│   └── console.py
└── storage/sqlite.py    WAL, écritures groupées hors boucle
```

---

## Le cœur du système : `QueryPlanner`

Le cahier des charges demande « FEW REMOTE REQUESTS + FAST LOCAL FILTERING » :
chercher `ナイキ` une fois, puis retrouver localement `division`, `trail`,
`tokyo`. L'intuition est juste — 4 µs de filtrage local contre 100 à 300 ms
d'aller-retour réseau.

**Mais cette stratégie a une limite dure, et elle est silencieuse.**

Une marketplace ne renvoie que les N annonces les plus récentes (120 chez
Mercari). Si elle en publie plus de N entre deux scans, la page ne remonte
plus jusqu'au passage précédent : il y a un **trou**, et les annonces tombées
dedans ne seront jamais vues. Sur `ナイキ`, ce trou est permanent — et rien ne
le signale, puisque la page est pleine de résultats.

Le planificateur commence donc par la version économe, et **se rétracte dès
que la mesure montre que ça coûte des annonces** :

```
5 mots-clés, 2 familles de recherche
        ↓
PLAN INITIAL — 2 requêtes
  'アンダーアーマー'   → Under Armour
  'ナイキ'           → Nike Division, Nike Tokyo, Nike Trail

   >>> la source signale un trou sur ナイキ

PLAN RÉVISÉ — 7 requêtes
  'アンダーアーマー'      → Under Armour
  'ナイキ ディビジョン'    → Nike Division
  'ナイキ division'      → Nike Division
  'ナイキ トレイル'       → Nike Trail
  'ナイキ trail'         → Nike Trail
  'ナイキ 東京'          → Nike Tokyo
  'ナイキ tokyo'         → Nike Tokyo
```

Deux signaux, tous deux mesurés :

- **trou** — la page ne remonte plus au scan précédent. Signal fort : la
  requête perd des annonces *maintenant*. Dégroupage immédiat.
- **rendement** — trouvailles par annonce examinée. Signal faible : après
  400 annonces observées et moins de 0,5 % de rendement, dégroupage.

Un dégroupage n'est jamais annulé : repasser en mode large recommencerait à
perdre des annonces pour économiser des requêtes.

**Les requêtes précises sont synthétisées**, pas inventées par l'utilisateur :
`ナイキ` + `ディビジョン` → `ナイキ ディビジョン`. Une par écriture alternative,
parce que `ナイキ ディビジョン` ne trouverait pas un titre écrit
`NIKE DIVISION`.

---

## Mesure de la latence

Cinq étapes, nommées comme dans le cahier des charges :

```
T0  published_at   publication par le vendeur
T1  available_at   indexation par la source        (souvent INCONNU)
T2  requested_at   départ de notre requête
T3  received_at    réponse reçue
T4  matched_at     annonce retenue par le filtre
T5  notified_at    notification partie
```

**Une latence dont un bout est inconnu n'est pas enregistrée.** La plupart des
marketplaces ne disent pas quand elles ont indexé une annonce (T1) ; les pages
HTML ne donnent souvent même pas T0. Un horodatage deviné ferait baisser
artificiellement les percentiles.

Concrètement : une annonce Yahoo Auctions n'affichera **pas** de « détectée en
X s », parce que la page de résultats ne dit pas quand elle a été publiée.
Une annonce Mercari en affichera une, parce que l'API donne `created`.

---

## Démarrer

### Windows

```
1. Installe Python depuis python.org
   → coche « Add python.exe to PATH » à l'installation
2. Double-clic sur run-snipe.bat
```

Le lanceur crée l'environnement, installe les dépendances, génère
`config.yaml`, et démarre. Il répare aussi un environnement virtuel créé
sans pip (cas fréquent), et se replie sur le Python du système si nécessaire.

### Linux / macOS

```bash
./run-snipe.sh
```

### Installation manuelle

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows : .venv\Scripts\activate
pip install -e .
snipe init                          # crée config.yaml
cp .env.example .env                # puis remplis-le
snipe doctor                        # vérifie tout
snipe run
```

### Commandes

| Commande | Effet |
|---|---|
| `snipe run` | Le bot complet |
| `snipe run --demo` | Marketplace simulée, **aucune requête réseau** |
| `snipe run --dry-run` | Scanne et affiche, **n'envoie rien** |
| `snipe run --once` | Un seul cycle, puis sortie (utile en CI) |
| `snipe run --budget 4` | Limite le débit à 4 requêtes/seconde |
| `snipe benchmark` | Mesure les latences par étape |
| `snipe doctor` | Diagnostic, y compris le test du canal Telegram |
| `snipe init` | Crée `config.yaml` depuis le modèle |

`python -m snipe …` fonctionne à l'identique si la commande n'est pas dans
le PATH.

### Telegram en trois étapes

1. Sur Telegram, écris à **@BotFather** → `/newbot` → il te donne le jeton
2. **Envoie un message à ton bot** — sans ça il n'a pas le droit de t'écrire
3. Écris à **@userinfobot** → il te donne ton `chat_id`

Mets les deux dans `.env`, puis `snipe doctor` : il teste le jeton **et**
l'accès au chat séparément (un jeton valide avec un mauvais `chat_id` est le
cas le plus fréquent, et il ne se voit qu'à la première annonce si on ne le
teste pas).

---

## Configuration

### Mots-clés : quatre champs, et pourquoi

```yaml
keywords:
  - name: Nike Division        # nom lisible, affiché dans la notification
    search: [ナイキ]            # ce qui est RÉELLEMENT envoyé à la marketplace
    include: [ディビジョン, division]   # filtré EN LOCAL — alternatives (OU)
    exclude: [シューズ, sneakers]      # filtré EN LOCAL
    priority: high             # 2 s (high) / 5 s (medium) / 20 s (low)
```

`include` est une liste d'**alternatives d'écriture** : `ディビジョン` OU
`division`. Exiger les deux ne matcherait jamais rien. Chaque alternative peut
contenir plusieurs mots, tous requis (`nathan bell`).

**Un mot-clé ne matche que les annonces ramenées par SES propres recherches.**
Sans cette règle, `Nike Tokyo` (include : `東京`) attribuerait une annonce
`アンダーアーマー 東京` — la notification aurait l'air plausible mais la marque
serait fausse.

### Budget de requêtes

```yaml
scanner:
  budget_per_second: 8.0
```

Ajouter des mots-clés **répartit** ce budget, il ne grandit pas tout seul.
Quand il ne suffit plus, le bot le dit :

```
plan : 34 requêtes sur 2 source(s) pour 12 mot(s)-clé(s)
       — budget saturé, cadence réelle 4.25s
```

Les priorités pèsent dans la répartition : une tâche `high` reçoit dix fois
la part d'une `low`.

### Sources HTML

Yahoo Auctions et Buyee sont lus en HTML, via des sélecteurs CSS dans
`config.yaml`. **Ceux livrés sont un point de départ non vérifié** — le
domaine était inaccessible pendant le développement. Pour les corriger :

1. Ouvre une page de résultats dans ton navigateur
2. Clic droit → Inspecter sur une annonce
3. Reporte les classes réelles dans `config.yaml`

Tant que rien n'est extrait, le bot le journalise explicitement :

```
WARNING yahoo_auction : aucune annonce extraite — les sélecteurs CSS sont
        probablement à mettre à jour dans config.yaml (item='li.Product')
```

---

## Robustesse

| Situation | Comportement |
|---|---|
| Une source tombe | Les autres continuent. Espacement progressif, journal `PANNE` puis `RÉTABLIE` |
| 429 (limite de débit) | Pause selon `Retry-After`, budget global réduit de 30 %, remontée progressive |
| Timeout réseau | Retenté avec recul exponentiel jusqu'à 3 fois |
| Réponse invalide | L'annonce est ignorée, la page continue d'être traitée |
| Exception inattendue dans une source | Attrapée et comptée, le scanner survit |
| Telegram en panne | Retentes, puis abandon **de cette notification seulement** — le worker survit |
| File de notification pleine | Les plus anciennes sont abandonnées et comptées. Le scan ne ralentit jamais |
| Redémarrage | Les annonces vues sont rechargées depuis SQLite — pas de re-notification |
| Coupure de courant | WAL + `synchronous=NORMAL` : au pire la dernière transaction, retrouvée au scan suivant |

---

## Tests

```bash
pytest                       # 576 tests
pytest tests/v3 -q           # 154 tests de la nouvelle architecture
python -m compileall src     # vérification syntaxique
```

Ce que couvrent les tests de `tests/v3/` :

- **Normalisation** — NFKC, demi/pleine chasse, ancrage des termes latins
- **Planificateur** — regroupement, dégroupage sur trou, synthèse des
  requêtes précises, invariant de couverture
- **Ordonnanceur** — priorités, répartition du budget, absence de dérive
- **Sources** — parsing Mercari sur réponse capturée, source HTML sur
  fixtures, validation des sélecteurs, prix en notation japonaise et
  européenne
- **Intégration** — warmup, déduplication au redémarrage, détection de trou,
  isolation des sources, et **le scanner ne s'arrête jamais pour attendre une
  notification** (vérifié en comptant les requêtes face à un canal lent)
- **Secrets** — un test échoue si un secret peut atteindre `config.yaml`

---

## Ce qui n'a pas pu être vérifié

Par honnêteté, et parce que le cahier des charges l'exige explicitement :

1. **Aucune requête réelle vers Mercari, Yahoo Auctions ou Buyee.** Les trois
   domaines sont bloqués par la passerelle réseau de l'environnement de
   développement (`403` au CONNECT). Le parsing est testé sur des réponses
   capturées et des fixtures ; le chemin réseau ne l'est pas.

2. **Les sélecteurs HTML de Yahoo Auctions et Buyee sont des hypothèses.**
   C'est pourquoi ils sont configurables et que ces sources sont désactivées
   par défaut.

3. **Les gabarits d'URL Buyee ne sont pas confirmés.** Ils sont dans le YAML,
   corrigeables sans toucher au code.

4. **Aucune promesse de latence chiffrée.** Le benchmark mesure ce qui est
   mesurable et marque « INJOIGNABLE » ce qui ne l'est pas. La latence réelle
   dépend du délai d'indexation de la marketplace, sur lequel aucun code n'a
   de prise.

Ce que le benchmark mesure réellement sur ta machine :

```
snipe benchmark
```
