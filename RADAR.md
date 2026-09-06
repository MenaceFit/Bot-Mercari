# Buyee Radar — scanner multi-marketplace temps réel

> **MERCARI IS A SOURCE. BUyee IS THE TARGET SEARCH ECOSYSTEM.**

Buyee est la **plateforme cible**. Les marketplaces japonaises en sont les
**sources internes**. Une requête part vers toutes, en parallèle.

```
   BUyee  (intermédiaire, plateforme cible)      Mandarake  (enseigne directe)
     │                                                │
     ├── Cross-Search ── une requête, cinq sources    └── stock d'occasion
     │     ├─ Mercari                                     sort=arrival
     │     ├─ Rakuma                                      upToMinutes=N
     │     ├─ JDirectItems Auction
     │     ├─ JDirectItems Fleamarket
     │     └─ LuxeWholeSale
     │
     └── Catalogues ── JDI Shopping · Rakuten · Amazon · ZOZOTOWN
                       UNSUPPORTED : pas de flux de nouveautés
```

Buyee expose **lui-même** une recherche transversale
(`/item/crosssearch/query/{mot-clé}`) et déclare ses sites supportés : une
requête couvre les cinq marketplaces d'occasion. Les namespaces dédiés
restent utiles pour paginer et trier source par source. Les catalogues
marchands sont déclarés **UNSUPPORTED** avec leur motif : une fiche
produit durable et réapprovisionnée n'est pas une nouvelle annonce.

**Mandarake** est une seconde plateforme, directe celle-là : une enseigne
japonaise d'occasion (manga, figurines, doujinshi, jouets vintage,
rétrogaming) qui vend son propre stock et expédie elle-même — inaccessible
via Buyee. C'est aussi le flux de nouveautés le mieux outillé du projet :
sa recherche accepte `sort=arrival` et `upToMinutes=N`, donc « ce qui est
arrivé dans les N dernières minutes ». Ailleurs la fraîcheur se déduit d'un
filigrane d'horodatage ; ici elle se demande.

```bash
buyee-radar init         # crée radar.yaml
buyee-radar doctor       # vérifie l'installation
buyee-radar run --demo   # essai complet, sans réseau  →  http://127.0.0.1:8899

python -m scanner.test_sources           # état réel de chaque source
python -m scanner.test_query "nike trail"  # une requête, toutes les sources
python -m scanner.test_live              # 60 s de surveillance en direct
```

---

## Sommaire

1. [Installation](#1-installation)
2. [Premier démarrage](#2-premier-démarrage)
3. [Configuration](#3-configuration)
4. [Activer une source réelle](#4-activer-une-source-réelle)
5. [Ajouter un mot-clé](#5-ajouter-un-mot-clé)
6. [Notifications](#6-notifications)
7. [Le dashboard](#7-le-dashboard)
8. [Architecture](#8-architecture)
9. [Performance](#9-performance)
10. [Limitations connues](#10-limitations-connues)
11. [Dépannage](#11-dépannage)

---

## 1. Installation

Prérequis : **Python 3.10+**. Node.js 18+ uniquement si tu veux recompiler
le dashboard (une version compilée est déjà fournie).

### Windows

Double-clique sur **`run-radar.bat`**. Il crée l'environnement virtuel,
installe les dépendances et lance le scanner. Rien d'autre à faire.

### macOS / Linux

```bash
chmod +x run-radar.sh
./run-radar.sh
```

### Installation manuelle

```bash
python -m venv .venv
source .venv/bin/activate        # Windows : .venv\Scripts\activate
pip install -e .
buyee-radar doctor
```

---

## 2. Premier démarrage

```bash
buyee-radar init          # copie le modèle vers radar.yaml
buyee-radar run --demo    # 4 sources simulées, aucune requête réseau
```

Ouvre **http://127.0.0.1:8899**. Le flux se remplit en quelques secondes.

Le mode `--demo` utilise quatre sources explicitement nommées `sim_*`. Elles
servent à valider toute la chaîne — planification, déduplication, filtrage,
scoring, stockage, WebSocket, dashboard — sans toucher au réseau. Elles ne
se font passer pour rien : le préfixe est visible partout.

Ajoute `--dry-run` pour détecter et afficher sans rien envoyer.

### Les commandes

| Commande | Rôle |
|---|---|
| `buyee-radar run` | scanner + API + dashboard |
| `buyee-radar run --demo` | idem, sources simulées |
| `buyee-radar once` | un seul cycle, puis sortie |
| `buyee-radar calibrate` | découvre les sélecteurs CSS d'une source |
| `buyee-radar health` | joignabilité et statut de chaque source |
| `buyee-radar benchmark` | latences mesurées par étape |
| `buyee-radar doctor` | diagnostic de l'installation |
| `buyee-radar init` | crée `radar.yaml` |
| `buyee-radar notify-test` | envoie une annonce d'exemple sur Telegram/Discord |

`buyee-radar --demo` sans sous-commande équivaut à `buyee-radar run --demo`.

---

## 3. Configuration

Deux fichiers, une règle stricte :

| Fichier | Contenu | Versionné ? |
|---|---|---|
| `radar.yaml` | tous les réglages | oui, il est fait pour être partagé |
| `.env` | **uniquement** les secrets | **non**, git-ignoré |

> Le fichier s'appelle `radar.yaml` et non `config.yaml` : la génération
> précédente (`snipe`) utilise déjà ce nom-là avec un autre schéma. Deux
> outils qui se disputent le même fichier, ça donne un des deux qui démarre
> en silence avec ses valeurs par défaut. Pour utiliser un autre chemin :
> `buyee-radar -c mon-fichier.yaml run`.

Aucun secret ne peut atterrir dans `radar.yaml` : la sérialisation lève une
`AssertionError` si un jeton, un `chat_id` ou une URL de webhook s'y glisse,
et un test le vérifie. Le YAML peut donc être envoyé en capture d'écran
quand tu demandes de l'aide.

### Les sections de `radar.yaml`

```yaml
scanner:
  interval_high: 2.0        # cadence des mots-clés prioritaires (s)
  interval_medium: 5.0
  interval_low: 20.0
  budget_per_second: 8.0    # requêtes/s, TOUTES sources confondues
  max_concurrency: 20       # connexions ouvertes en même temps
  max_catchup_pages: 3      # pages en plus quand un trou est détecté
  warmup: true              # premier passage silencieux

filters:
  min_price: null           # en yens
  max_price: null
  exclude: []               # termes bannis, toutes recherches confondues
  exclude_regex: []
  conditions: []            # ex. ["new", "like_new"]
  max_age_seconds: 900      # au-delà, l'annonce n'est plus « nouvelle »

scoring:
  notify_min_score: 0       # 0 = tout arrive au dashboard
  telegram_min_score: 70
  discord_min_score: 50
  bargain_price: 6000       # en dessous : bonus « bonne affaire »
  expensive_price: 60000

currency:
  target: EUR
  fixed_rate: null          # le renseigner supprime tout appel réseau
  allow_network: false

storage:
  database: data/radar.db
  retention_days: 30
  flush_every: 5.0          # cadence d'écriture en base (s)

buyee:
  enabled: true
  affiliate_id: ""          # ajouté aux liens d'achat s'il est renseigné

api_host: 127.0.0.1         # 0.0.0.0 pour y accéder depuis le téléphone
api_port: 8899
```

`budget_per_second` est un **budget partagé**. Ajouter des mots-clés le
répartit, il ne grandit pas tout seul — c'est volontaire : c'est ce qui
empêche le bot de marteler Buyee quand la liste s'allonge.

### Les réglages par variable d'environnement

```bash
RADAR_LOG_LEVEL=DEBUG      # verbosité
RADAR_BUDGET=12            # surcharge budget_per_second
TELEGRAM_ENABLED=0         # coupe Telegram sans toucher au YAML
```

---

## 4. Activer une source réelle

Les sources réelles arrivent **désactivées et non calibrées**. La raison
complète est dans **[BUYEE_AUDIT.md](BUYEE_AUDIT.md)**, en résumé :
`buyee.jp` était inaccessible depuis l'environnement de développement (403
sur le `CONNECT`). La **structure des URL** a pu être établie à partir d'URL
réelles indexées ; le **balisage HTML**, non — aucune page n'a jamais été
chargée.

Plutôt que d'inventer des sélecteurs CSS crédibles et faux — un scraper faux
ne lève pas d'erreur, il renvoie zéro annonce en silence — **l'extraction
est traitée comme une donnée** : les sélecteurs vivent dans `radar.yaml` et
sont découverts sur ta machine, où Buyee répond.

```bash
# 1. Vérifie que la source répond depuis chez toi
buyee-radar health

# 2. Découvre les sélecteurs, sans rien enregistrer
buyee-radar calibrate --source mercari --keyword nike --dry-run
```

La commande affiche un **échantillon extrait** : titres, prix, liens. Relis-le.
S'il est cohérent, enregistre :

```bash
buyee-radar calibrate --source mercari --keyword nike
```

Puis dans `radar.yaml` :

```yaml
sources:
  mercari:
    enabled: true
```

Le dashboard propose aussi un bouton **Calibrer** sur la page *Sources*.

### Statut des sources

| Source | Identifiant | Statut |
|---|---|---|
| Buyee Cross-Search | `crosssearch` | **URL vérifiée** — active par défaut |
| JDirectItems Auction | `jdirectitems_auction` | **URL vérifiée** |
| Mercari | `mercari` | **URL vérifiée** (`keyword=` attesté) |
| Rakuma | `rakuma` | à calibrer — chemin de recherche déduit |
| JDirectItems Fleamarket | `jdirectitems_fleamarket` | à calibrer — chemin déduit |
| LuxeWholeSale | `luxewholesale` | via cross-search uniquement |
| JDirectItems Shopping | `jdirectitems_shopping` | **non supportée** — catalogue par boutique |
| Rakuten | `rakuten` | **non supportée** — catalogue marchand |
| Amazon | `amazon` | **non supportée** — catalogue marchand |
| ZOZOTOWN | `zozotown` | **non supportée** — catalogue de mode neuve |
| **Mandarake** | `mandarake` | **URL vérifiée** — plateforme directe, hors Buyee |

Les quatre sources non supportées sont **refusées au démarrage** même si tu
les actives, avec le motif en clair. Elles ne sont ni simulées ni masquées.

---

## 5. Ajouter un mot-clé

Dans `radar.yaml` :

```yaml
keywords:
  - name: Nike Dunk Low
    search: ["nike dunk low", "ナイキ ダンク ロー"]   # ce qui part vers Buyee
    include: ["dunk"]              # doit apparaître dans le titre
    exclude: ["レプリカ", "copy"]  # rejette l'annonce
    exclude_regex: []
    priority: high                 # high | medium | low
    min_price: 3000                # en yens
    max_price: 40000
    sources: []                    # vide = toutes les sources actives
    enabled: true
```

Ou depuis le dashboard, page **Mots-clés** : ajout, suppression et
activation sont écrits dans `radar.yaml` à chaud.

Quelques principes utiles :

- **`search` élargit, `include` resserre.** Le planificateur regroupe les
  recherches proches en une requête large, puis mesure : si le rendement
  d'une requête large tombe sous 0,5 % après 400 annonces, il la remplace
  par les requêtes précises. Une requête large qui rate une annonce est
  rétrogradée immédiatement.
- Les termes latins sont comparés **par frontière de préfixe** (`nike` ne
  matche pas `nikeish`), les termes CJK par **sous-chaîne** — le japonais
  n'a pas d'espaces.
- Tout est normalisé en NFKC : les demi-largeurs et pleines largeurs se
  valent.
- Les `exclude` d'un mot-clé ne s'appliquent qu'à lui. Pour bannir un terme
  partout — les parfums, le maquillage —, utilise `filters.exclude`.

---

## 6. Notifications

Copie `.env.example` vers `.env` et remplis-le. **Ces valeurs ne vont jamais
dans `radar.yaml`.**

### Recevoir les annonces dans un canal Telegram

C'est la configuration recommandée : un canal dédié se consulte d'un pouce,
se partage, et garde l'historique.

1. Sur Telegram, écris à **@BotFather** → `/newbot` → il te donne le jeton.
2. **Crée un canal**, ajoute ton bot dedans, et donne-lui le rôle
   **administrateur**. Sans ça, il ne peut rien publier.
3. Renseigne `.env` :

```env
TELEGRAM_BOT_TOKEN=123456:AAE...
TELEGRAM_CHAT_ID=@mon_canal_radar     # ou -1001234567890 si le canal est privé
```

4. Vérifie **avant** de lancer le scanner :

```bash
buyee-radar notify-test
```

La commande envoie une annonce d'exemple, affiche exactement ce qu'elle
vise, et dit ce qui a échoué.

### Trouver `TELEGRAM_CHAT_ID`

| Destination | Valeur à mettre | Comment l'obtenir |
|---|---|---|
| **Canal public** | `@nom_du_canal` | rien à chercher, c'est le nom |
| **Canal privé** | `-1001234567890` | transfère un message du canal à **@userinfobot** |
| **Groupe** | `-1001234567890` | idem, ou méthode `getUpdates` ci-dessous |
| **Chat privé** | `123456789` | écris à **@userinfobot** |

La méthode qui marche toujours, sans bot tiers : fais parler le bot une
fois (écris-lui, ou publie dans le canal où il est administrateur), puis

```bash
curl -s "https://api.telegram.org/bot<TON_JETON>/getUpdates" | python -m json.tool
```

et lis `"chat": { "id": … }`. Les canaux et groupes commencent par `-100`,
un chat privé est un nombre positif. Si la liste est vide : le bot n'a rien
vu passer, ou il n'est pas administrateur du canal.

### Écrire dans un salon précis

**Telegram — un sujet d'un groupe Forum.** Si ton groupe est en mode
« Sujets », tu peux viser un salon en particulier plutôt que le sujet
général :

```env
TELEGRAM_CHAT_ID=-1001234567890
TELEGRAM_TOPIC_ID=42
```

L'identifiant du sujet se lit dans l'URL Telegram Web —
`.../c/1234567890/42`, c'est le **dernier** nombre.

**Discord — un fil d'un salon.** Le webhook est déjà lié à un salon : c'est
celui que tu choisis en le créant. Pour viser un **fil** ou un post de forum
à l'intérieur :

```env
DISCORD_THREAD_ID=1234567890123456789
```

Active le mode développeur (Paramètres → Avancés), puis clic droit sur le
fil → *Copier l'identifiant*.

Ces deux valeurs sont facultatives, et traitées comme des secrets : elles
vivent dans `.env`, jamais dans `radar.yaml`.

### Le format des messages

Par défaut (`telegram_style: clean`), un message tient en quatre lignes :
le **nom de l'annonce**, le **prix en euros**, la source, et le **lien**.

```
NIKE ACG トレイル ジャケット 新品未使用

76 €  ·  ¥12 500
JDirectItems Auction  ·  VERY RARE

🔗 Ouvrir sur Buyee
```

Le prix en euros vient en premier parce que c'est celui qu'on compare à ce
qu'on est prêt à payer ; le yen reste en second parce que c'est lui qui
figure sur la page. **Quand le taux de change n'est pas disponible, seul le
yen s'affiche** — un euro inventé serait pire qu'un yen seul.

Le lien pointe vers Buyee pour les sources Buyee, et vers la page Mandarake
pour Mandarake — là où l'achat se fait réellement.

`telegram_style: detailed` ajoute le mot-clé, le score et la latence.

### Discord

Paramètres du salon → **Intégrations** → **Webhooks** → *Nouveau webhook* →
*Copier l'URL*.

```env
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/.../...
```

Puis dans `radar.yaml` :

```yaml
notifications:
  discord_enabled: true
  telegram_enabled: true
  telegram_style: clean     # clean | detailed
  telegram_photo: true
  telegram_silent: false
  console: true
  max_queue: 500
  max_retries: 3
```

Les seuils par canal sont dans `scoring` : `telegram_min_score` (70 par
défaut) et `discord_min_score` (50). Le dashboard, lui, reçoit tout.

> **Le scanner n'attend jamais une notification.** L'envoi passe par une
> file asynchrone bornée avec ré-essais ; si Telegram est lent ou en panne,
> la détection continue à la même cadence. Aucun `requests.post()`, aucun
> `time.sleep()` sur le chemin critique.

## 7. Le dashboard

**http://127.0.0.1:8899** — Next.js exporté en statique, servi par l'API.

| Page | Contenu |
|---|---|
| **Vue d'ensemble** | KPI temps réel, flux récent, santé des sources |
| **Flux** | toutes les détections, filtrables par source / palier / mot-clé |
| **Sources** | statut, latence, taux d'erreur, disjoncteur, bouton *Calibrer* |
| **Mots-clés** | ajout, suppression, activation |
| **Analytics** | volume dans le temps, répartition par source / palier / mot-clé |
| **Historique** | recherche dans tout ce qui a été stocké |
| **Système** | mémoire, file d'écriture, file de notification, événements |

Chaque annonce affiche **trois horodatages distincts** :

- **Source** — quand la marketplace dit que l'annonce est parue ;
- **Détectée** — quand le scanner l'a vue ;
- **Latence** — l'écart entre les deux.

Quand une source ne publie pas d'horodatage fiable, la latence affiche
**« — »**, jamais « 0 ms ». Le projet ne promet pas de détection
instantanée : il mesure et affiche ce qu'il constate.

### Accéder depuis le téléphone

```yaml
api_host: 0.0.0.0
```

Puis ouvre `http://<IP-de-ton-PC>:8899` depuis le téléphone, sur le même
Wi-Fi. Sur Windows, autorise Python dans le pare-feu à la première demande.

### Recompiler le dashboard

```bash
cd frontend
npm install
npm run build      # produit frontend/out, servi automatiquement par l'API
npm run dev        # développement, port 3000, API sur 8899
npm run typecheck  # tsc --noEmit
```

Le `package.json` pilote aussi le scanner Python, pour éviter de jongler
entre deux dossiers :

| Script | Équivalent |
|---|---|
| `npm run scanner` | `buyee-radar run` |
| `npm run demo` | `buyee-radar run --demo` |
| `npm run once` | `buyee-radar once --demo --dry-run` |
| `npm run health` | `buyee-radar health` |
| `npm run benchmark` | `buyee-radar benchmark` |
| `npm run doctor` | `buyee-radar doctor` |
| `npm test` | `pytest tests/radar -q` |

---

### Vérifier que le multi-source fonctionne vraiment

Trois commandes, faites pour répondre à « **mon scanner peut-il
techniquement récupérer les résultats de cette source depuis Buyee ?** » —
question très différente de « Buyee supporte-t-il cette plateforme ? ».

```bash
python -m scanner.test_sources              # une vraie recherche par source
python -m scanner.test_query "nike trail"   # le total est la SOMME des sources
python -m scanner.test_live                 # chaque détection, en direct
```

Ajoute `--demo` pour les exécuter sur les sources simulées, sans réseau.
`test_query` affiche le détail par source, jamais un total sans
provenance :

```
  ✓ [Mercari]                   12 results   184 ms
  ✓ [Rakuma]                     8 results   241 ms
  ✓ [JDirectItems Auction]      14 results   332 ms
  ✓ [JDirectItems Fleamarket]    5 results   298 ms
  · [ZOZOTOWN]                   non supportée — catalogue de mode neuve…

  Total: 39 results
```

## 8. Architecture

```
                    ┌──────────────┐
   radar.yaml ───▶ │   Settings   │ ◀─── .env  (secrets uniquement)
                    └──────┬───────┘
                           │
        ┌──────────────────┼───────────────────┐
        ▼                  ▼                   ▼
   ┌─────────┐      ┌─────────────┐     ┌─────────────┐
   │ Adapters│      │  Scheduler  │     │  Filters    │
   │ Buyee   │─────▶│  budget +   │────▶│  + Scoring  │
   │ Simul.  │      │  priorités  │     └──────┬──────┘
   └─────────┘      └─────────────┘            │
        │                                      ▼
        │           ┌──────────────────────────────────┐
        └──────────▶│           Scanner                │
                    │  checkpoints · déduplication L1  │
                    │  détection de trous · métriques  │
                    └───┬───────────┬──────────────┬───┘
                        ▼           ▼              ▼
                 ┌───────────┐ ┌─────────┐ ┌──────────────┐
                 │  SQLite   │ │EventBus │ │NotificationHub│
                 │  (L2)     │ │  (WS)   │ │ file bornée  │
                 └───────────┘ └────┬────┘ └──────┬───────┘
                                    ▼             ▼
                              Dashboard    Discord / Telegram
```

Les pièces qui comptent :

- **Adapters** — un contrat unique (`MarketplaceAdapter`). Ils ne lèvent pas
  d'exception sur un échec attendu : ils renvoient un `SearchResult` avec
  `ok=False` et un motif. Une source qui tombe n'arrête pas les autres.
- **Scheduler** — budget de requêtes/seconde réparti par priorité
  (haute = 10, moyenne = 3, basse = 1), planification **sans dérive** : les
  échéances sont calculées en absolu, pas par `sleep` cumulés.
- **Optimiseur de requêtes** — regroupe les recherches proches en requêtes
  larges, et rétrograde une requête large **sur preuve** : un trou détecté,
  ou un rendement < 0,5 % après 400 annonces.
- **Détection de trous** — par filigrane d'horodatage, pas par « la page est
  100 % nouvelle ». Un trou déclenche des pages de rattrapage, bornées par
  `max_catchup_pages`.
- **Déduplication** — L1 en mémoire (FIFO bornée, coût constant) puis L2 en
  SQLite. La clé est `source:id`, pas le titre.
- **Disjoncteur** — par source, `CLOSED / OPEN / HALF_OPEN`, temporisation
  doublée à chaque échec et plafonnée. Une source ouverte est écartée
  immédiatement, sans requête.
- **Bus d'événements** — publication **non bloquante**, une file bornée par
  abonné, compteur de messages perdus, tampon de rejeu pour un client qui se
  reconnecte.
- **Stockage** — SQLite en WAL, `synchronous=NORMAL`, écritures groupées
  hors de la boucle d'événements.

### Les paliers de rareté

Le score va de 0 à 100 et il est **explicable** : chaque composante est
conservée et affichée dans le dashboard (correspondance 40, titre exact 15,
marque premium 14, ligne rare 12, bonus de source, prix bas +10 / élevé −8,
rareté observée, historique vendeur).

| Score | Palier |
|---|---|
| 0–49 | `NORMAL` |
| 50–69 | `RARE` |
| 70–89 | `VERY RARE` |
| 90–100 | `ULTRA RARE` |

Les composantes statistiques (rareté observée, historique vendeur) valent
**0 tant qu'il y a moins de 20 observations**. Un score inventé sur trois
annonces ne vaut rien.

---

## 9. Performance

```bash
buyee-radar benchmark              # latences par étape, mesurées
```

Le chemin critique est mesuré en six points :

| Étape | Signification |
|---|---|
| **T0** `created_at` | l'annonce paraît sur la marketplace |
| **T1** `available_at` | elle devient visible sur Buyee |
| **T2** `requested_at` | le scanner lance la requête |
| **T3** `detected_at` | la réponse est parsée, l'annonce est nouvelle |
| **T4** `matched_at` | filtres et scoring terminés |
| **T5** `notified_at` | notification partie |

Quand un horodatage n'est pas connu, il reste à zéro et **n'entre pas dans
les histogrammes** — plutôt que de fabriquer une latence flatteuse.

Ordres de grandeur mesurés en mode `--demo` sur cette machine :
réseau p95 ≈ 360 ms, détection p50 ≈ 1,7 s. Sur des sources réelles, la
latence dépend surtout de Buyee et de ta connexion.

Les choix qui font la latence :

- un seul client `httpx` **HTTP/2**, connexions persistantes réutilisées ;
- toutes les sources scannées **en parallèle**, jamais en séquence ;
- parsing HTML avec **selectolax** (nettement plus rapide que BeautifulSoup) ;
- déduplication L1 en mémoire avant tout accès disque ;
- écritures SQLite groupées, hors du chemin critique ;
- notifications en file asynchrone — le scanner ne les attend jamais.

---

## 10. Limitations connues

Dites franchement, parce qu'un outil de sniping qui ment sur ce qu'il sait
faire est pire qu'inutile :

1. **Les sélecteurs des sources réelles doivent être calibrés sur ta
   machine.** Voir [BUYEE_AUDIT.md](BUYEE_AUDIT.md). Tant que ce n'est pas
   fait, la source est affichée « à calibrer » et n'est pas planifiée.
2. **`jdi_shopping` et `rakuten` sont non supportées** — ce sont des
   catalogues, pas des flux de nouvelles annonces. Elles ne sont pas
   simulées pour faire joli.
3. **Aucun contournement de protection.** Si Buyee sert un CAPTCHA ou une
   page anti-bot, l'adapter s'arrête et le signale. Il n'insiste pas, il ne
   déguise rien, il ne persiste pas la réponse.
4. **La latence n'est pas nulle et ne le sera jamais.** Elle dépend de la
   cadence de scan, du temps que met Buyee à indexer l'annonce d'origine, et
   du réseau. Le dashboard affiche la vraie mesure.
5. **Le tri par date n'est pas confirmé** sur tous les namespaces. Les
   paramètres présents sont des hypothèses, surchargeables via
   `extra_params` dans `radar.yaml`.
6. **Le budget de requêtes est partagé.** Cent mots-clés en priorité haute
   ne donnent pas cent fois plus de requêtes — ils se partagent le même
   budget. Priorise.

---

## 11. Dépannage

**`buyee-radar doctor`** d'abord. Il vérifie Python, les dépendances, la
configuration, les secrets, les sources et l'accès disque.

| Symptôme | Cause probable | Correctif |
|---|---|---|
| « aucune source active » | tout est désactivé dans `radar.yaml` | active une source, ou lance `--demo` |
| Source « à calibrer », zéro annonce | sélecteurs vides | `buyee-radar calibrate --source <nom>` |
| Dashboard vide mais des détections dans les logs | vidage en base pas encore passé | attends `storage.flush_every` (5 s), ou baisse-le |
| Page inaccessible depuis le téléphone | `api_host: 127.0.0.1` | mets `0.0.0.0`, et autorise Python dans le pare-feu |
| Rien sur Telegram | tu n'as jamais écrit au bot | envoie-lui un message, puis `buyee-radar doctor` |
| Trop de résultats hors sujet | filtres trop larges | ajoute des `include`, et des `filters.exclude` globaux |
| Source en disjoncteur ouvert | erreurs répétées | regarde la page *Système* ; la temporisation double puis se referme seule |
| `Address already in use` | un autre radar tourne | change `api_port`, ou ferme l'autre |

Les logs structurés sont dans **`logs/radar.jsonl`** — une ligne JSON par
événement, faciles à filtrer :

```bash
grep '"level":"ERROR"' logs/radar.jsonl | tail -20
```

---

## Licence et usage

Outil personnel, pages publiques uniquement, en GET, à cadence bornée.
Respecte les conditions d'utilisation de Buyee et des marketplaces
d'origine.
