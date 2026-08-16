# ⚡ Mercari Sniper v2

Détection **temps réel** des nouvelles annonces Mercari Japon, avec dashboard
web live, notifications Discord et mesure de latence de bout en bout.

Réécriture complète du bot Tkinter d'origine : **~9× moins de requêtes**,
détection en **quelques secondes** au lieu de 30-60, et plus aucune fermeture
silencieuse au lancement.

---

## Démarrage rapide

### Windows
Double-clique **`run.bat`**. Il installe les dépendances, génère la config
depuis tes anciens fichiers, lance le bot et ouvre le dashboard.
*La fenêtre ne se ferme jamais toute seule* — en cas de souci, le message
reste affiché.

### Linux / macOS
```bash
chmod +x run.sh && ./run.sh
```

### Manuellement
```bash
python -m venv .venv
.venv/bin/pip install -e .          # Windows: .venv\Scripts\pip

.venv/bin/python main.py doctor     # vérifie l'installation
.venv/bin/python main.py init       # crée config.yaml depuis tes keywords.json
.venv/bin/python main.py run        # c'est parti → http://127.0.0.1:8420
```

> **`main.py` plutôt que `-m mercari_sniper`.** Le projet est en layout
> `src/` : `python -m mercari_sniper` ne fonctionne qu'après
> `pip install -e .`. `main.py` ajoute `src/` au chemin d'import lui-même, il
> suffit donc que les dépendances soient là. Après un `pip install -e .`, la
> commande `mercari-sniper` est équivalente et disponible partout.

### Essayer sans réseau
```bash
mercari-sniper run --demo
```
Génère de fausses annonces plausibles : tu vois le dashboard vivre,
sans envoyer la moindre requête à Mercari.

---

## Le dashboard

`http://127.0.0.1:8420` — mise à jour par WebSocket, sans rechargement.

| | |
|---|---|
| **Flux live** | Les annonces apparaissent dès la détection, avec vignette, prix, badge de rareté et latence réelle |
| **Latence** | Écart entre la publication par le vendeur et ta détection — c'est **la** métrique qui compte |
| **Keywords à chaud** | Ajout/suppression sans redémarrer le bot |
| **Sources** | État de chaque requête : trouvailles, nombre de polls, temps de réponse, erreurs |
| **Filtres** | Par rareté ou par texte, côté navigateur |
| **Alerte sonore** | Bip à chaque trouvaille (bouton 🔔) |
| **Figer** | Stoppe le défilement pour cliquer tranquillement — le bot continue de scanner |

---

## Ce qui a changé, et pourquoi

### 1. Le bot se fermait instantanément → corrigé

**Cause.** Le script v1 importait `mercapi`, `requests` et `tkinter` au niveau
module, avant tout `try`. Une seule dépendance manquante levait un
`ModuleNotFoundError` **avant** d'atteindre `mainloop()` : la console se
fermait aussitôt, emportant la trace avec elle. J'ai reproduit le symptôme à
l'identique.

**Corrections.**
- Les imports lourds sont **tardifs** — une dépendance absente donne un
  message clair avec la commande `pip install` exacte à copier.
- `mercari-sniper doctor` diagnostique tout : version de Python, paquets,
  config, droits d'écriture, webhook.
- Le `main()` capture toute exception, l'affiche en clair, et **maintient la
  fenêtre ouverte** quand il détecte un lancement par double-clic (via
  `GetConsoleProcessList`).
- Le `FileHandler` de log ne peut plus faire échouer le démarrage : un dossier
  non inscriptible dégrade vers la console au lieu de tuer le process.
- `run.bat` se termine par `pause`, quoi qu'il arrive.

### 2. Requêtes : 63 → 7 par cycle

La v1 envoyait **une requête par keyword**. Avec 61 keywords, chaque cycle
coûtait 61 requêtes — d'où l'intervalle de 30-60 s pour ne pas se faire
bloquer.

Or tes keywords partagent des racines : `ナイキ トレイル`, `ナイキ ベスト`,
`ナイキ 東京`… commencent tous par `ナイキ`. Le bot regroupe donc par racine,
interroge **7 requêtes larges** (120 annonces chacune), et retrouve les 61
keywords par **matching local**. Résultat mesuré sur ton vrai fichier :

```
61 keywords → 7 sources
```

Moins de requêtes pour la même couverture ⇒ on peut poller **toutes les 2 s**
au lieu de 60, sans augmenter la charge.

### 3. Accès direct à l'API, connexion chaude

Fini `mercapi`. Le bot signe lui-même ses jetons **DPoP** (JWT ES256) et garde
**un seul client HTTP/2 ouvert** pour toute la session. Plus de handshake TLS
à chaque scan — c'est le gros du gain de latence.

### 4. Les notifications ne bloquent plus la détection

La v1 faisait `requests.post()` + `time.sleep(0.8)` **dans la boucle de
scan** : 20 trouvailles = 16 secondes pendant lesquelles le bot ne regardait
plus rien. Ici un worker asynchrone dépile en tâche de fond, gère les 429 de
Discord et le `Retry-After`, pendant que le scan continue.

### 5. Déduplication sans I/O

La v1 réécrivait `seen_items.json` (jusqu'à 15 000 entrées) à chaque scan.
Maintenant : un `set` en mémoire pour la décision (O(1)), et des écritures
SQLite groupées dans un thread séparé. La boucle de détection ne touche
jamais le disque.

### 6. Ordonnancement sans dérive

La v1 faisait `sleep(interval)` *après* le travail : le temps de requête
s'ajoutait à chaque tour, et l'intervalle réel dérivait. Ici l'échéance
suivante se calcule depuis la précédente (`deadline += interval`), avec un
jitter qui désynchronise les sources.

### 7. Résistance aux limites de débit

Un token bucket **global** plafonne le débit toutes sources confondues :
ajouter un keyword **répartit** le budget au lieu de l'augmenter. Sur un 429,
le débit global baisse de 30 % (la limite est par IP — ralentir une seule
source ne servirait à rien), puis remonte progressivement après l'accalmie.

### Récapitulatif

| | v1 | v2 |
|---|---|---|
| Requêtes / cycle | 61 | **7** |
| Intervalle | 30-60 s | **2 s** |
| Latence de détection | 30-90 s | **quelques secondes** |
| Connexions HTTP | rouvertes à chaque scan | **HTTP/2 persistant** |
| Discord | bloque le scan | **asynchrone** |
| Dédup | réécriture JSON complète | **set mémoire + SQLite groupé** |
| Interface | Tkinter local | **dashboard web temps réel** |
| Crash au lancement | trace invisible | **diagnostic + fenêtre maintenue** |
| Tests | aucun | **147** |

---

## Configuration

Deux fichiers, séparés par nature :

- **`config.yaml`** — réglages, versionnable. Voir `config.example.yaml`,
  entièrement commenté.
- **`.env`** — secrets, **git-ignoré**. Voir `.env.example`.

```bash
cp .env.example .env
# puis renseigne DISCORD_WEBHOOK_URL
```

### Réglages qui comptent

```yaml
poll:
  interval: 2.0            # cible par source
  global_rate_limit: 5.0   # req/s toutes sources — au-delà de ~8, risque de 429
  burst_on_hit: true       # re-poll immédiat après une trouvaille
  warmup: true             # 1er tour silencieux

filters:
  max_age_seconds: 900     # ignore ce qui est plus vieux que 15 min
  exclude_words: [ジャンク, 訳あり]

notify:
  min_rarity: RARE         # ne notifier qu'à partir de RARE
```

**Aller plus vite ?** Baisse `poll.interval` et monte `global_rate_limit`.
Au-delà de ~8 req/s tu prends des 429 ; le bot ralentit alors tout seul, mais
tu perds en réactivité. `interval: 1.5` + `rate_limit: 6` est un bon
compromis agressif.

---

## Commandes

```bash
python main.py doctor            # diagnostic complet (ou: mercari-sniper doctor)
mercari-sniper init              # config.yaml depuis les fichiers v1
mercari-sniper init --force      # écrase la config existante
mercari-sniper run               # bot + dashboard
mercari-sniper run --demo        # hors ligne, annonces simulées
mercari-sniper run --port 9000   # autre port
mercari-sniper run --no-dashboard  # console seulement
mercari-sniper run -v            # logs de débogage
mercari-sniper once 'nike acg'   # une recherche ponctuelle
```

---

## Migration depuis la v1

`mercari-sniper init` reprend automatiquement :

| Fichier v1 | Devient |
|---|---|
| `keywords.json` | `keywords:` dans `config.yaml` |
| `mercari_config.json` → `min_price`, `max_price`, `warmup_first_run` | `filters:` / `poll:` |
| `mercari_config.json` → `webhook_url` | `DISCORD_WEBHOOK_URL` dans `.env` |
| `seen_items.json` | importé dans SQLite au 1er lancement (pas de re-spam) |

> **Sécurité.** Ton ancien `mercari_config.json` contenait le webhook Discord
> **et un token Apify en clair**. Si ce fichier a été partagé ou commité,
> **révoque les deux** : le webhook dans les réglages du salon Discord, le
> token dans Apify → Settings → Integrations. La v2 ne lit les secrets que
> depuis `.env`, qui est git-ignoré.

---

## Architecture

```
src/mercari_sniper/
├── cli.py           Entrée CLI · gestion d'erreurs · fenêtre maintenue ouverte
├── engine.py        Ordonnancement, dédup, matching, dispatch
├── config.py        YAML + env + import v1
├── matching.py      Normalisation NFKC, règles, index inversé, rareté
├── models.py        Listing + calcul de latence
├── store.py         SQLite (WAL), écritures groupées hors boucle
├── ratelimit.py     Token bucket global
├── events.py        Pub/sub → WebSocket
├── dpop.py          Jetons DPoP ES256
├── server.py        API REST + WebSocket
├── backends/        mercari_api (réel) · simulator (hors ligne)
├── notifiers/       discord (async) · console
└── web/index.html   Dashboard autonome
```

**Modèle d'exécution.** Une tâche asyncio par source, toutes partageant un
token bucket global. Le matching local (index inversé sur le mot le plus
discriminant de chaque règle) n'évalue qu'une poignée de règles par titre au
lieu des 61.

---

## Tests

```bash
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest -q      # 147 tests
```

Couvrent notamment : la signature DPoP vérifiée cryptographiquement,
l'équivalence entre l'index inversé et l'évaluation naïve, le warmup, la
déduplication, le backoff sur 429, et le fait qu'**aucun secret ne peut
atterrir dans le YAML**.

---

## Dépannage

| Symptôme | Cause probable | Solution |
|---|---|---|
| La fenêtre se ferme aussitôt | Dépendance manquante | `diagnostic.bat` (Windows) ou `mercari-sniper doctor` |
| `ModuleNotFoundError` | Dépendances non installées | `pip install -r requirements.txt` |
| Aucune annonce ne remonte | Warmup en cours (1er tour silencieux) | Attends un cycle ; ou `poll.warmup: false` |
| Beaucoup de 429 | Débit trop élevé | Baisse `global_rate_limit` ou monte `poll.interval` |
| Pas de notification Discord | Webhook absent | Renseigne `DISCORD_WEBHOOK_URL` dans `.env` |
| Dashboard inaccessible | Port occupé | `mercari-sniper run --port 9000` |
| `403` sur l'API | Réseau filtré (VPN, proxy d'entreprise) | Teste avec `--demo` pour isoler |

Le journal complet est dans `logs/sniper.log`.

---

## Notes

Ce bot interroge une API publique non documentée : elle peut changer sans
préavis. Le `global_rate_limit` par défaut (5 req/s) reste raisonnable ;
le pousser trop haut expose à un blocage temporaire de ton IP.

Usage personnel de veille. Respecte les conditions d'utilisation de Mercari.
