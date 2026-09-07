# ⚡ Mercari Sniper v2.2

> ### 🆕 La version à utiliser
>
> **[Radar Mercari](RADAR.md)** — détection temps réel des nouvelles
> annonces Mercari Japon via l'API officielle, dashboard web, scoring de
> rareté, notifications Telegram et Discord.
>
> **Windows :** double-clique `run.bat` · **macOS / Linux :** `./run.sh`
>
> Les générations précédentes restent dans le dépôt, avec leur propre
> fichier de configuration : `run-legacy-v2.bat` et `run-snipe.bat`.

Détection **temps réel** des nouvelles annonces Mercari Japon, avec dashboard
web live, notifications Discord et mesure de latence de bout en bout.

Réécriture complète du bot Tkinter d'origine : **~9× moins de requêtes**,
détection en **quelques secondes** au lieu de 30-60, et plus aucune fermeture
silencieuse au lancement.

---

## Démarrage rapide

> **Tu pars d'un PC neuf ?** Suis **[INSTALLATION.md](INSTALLATION.md)** —
> guide pas à pas depuis l'installation de Python, avec une vérification
> après chaque étape.
>
> **Sur téléphone ?** Voir **[MOBILE.md](MOBILE.md)** — installation comme
> application (Android et iOS), ou APK Android compilé par GitHub Actions.

### Windows
Double-clique **`run-legacy-v2.bat`** (l'ancien `run.bat` lance désormais
Buyee Radar). Il installe les dépendances, génère la config depuis tes
anciens fichiers, lance le bot et ouvre le dashboard.
*La fenêtre ne se ferme jamais toute seule* — en cas de souci, le message
reste affiché.

### Linux / macOS
```bash
chmod +x run-legacy-v2.sh && ./run-legacy-v2.sh
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
| **Flux live** | Les annonces arrivent par WebSocket, avec vignette, prix, palier de rareté et latence réelle |
| **Latence de détection** | Écart entre la publication par le vendeur et ta détection — c'est **la** métrique qui compte |
| **Activité** | Courbe des trouvailles par minute sur 30 min, avec repère au survol |
| **Mots-clés à chaud** | Ajout/suppression sans redémarrer ; sauvegardés dans `config.yaml` automatiquement |
| **Sources** | État de chaque requête : santé, cadence, trouvailles, rattrapages |
| **Couverture** | Alerte quand le budget de requêtes ne suffit plus, ou qu'un mot-clé n'est couvert par aucune source |
| **Thème clair / sombre** | Suit le système, avec bascule manuelle mémorisée |
| **Filtres** | Par palier de rareté ou par texte, côté navigateur |
| **Commander** | Bouton direct vers la page Buyee de l'annonce, le proxy d'achat qui commande sur Mercari et réexpédie |
| **Alerte sonore · Figer** | Bip à chaque trouvaille ; fige le fil pour cliquer tranquillement (le bot continue de scanner) |
| **Application mobile** | Installable depuis le navigateur (PWA) ou via l'APK Android — voir [MOBILE.md](MOBILE.md) |

L'état de santé d'une source et le palier d'une annonce sont toujours portés
par **une icône et un libellé**, jamais par la couleur seule : vert et rouge
sont indiscernables en deutéranopie.

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

### 2. Une requête précise par mot-clé

La v1 envoyait une requête par keyword, toutes les 30-60 s. Une version
intermédiaire a tenté l'inverse : regrouper les mots-clés par leur premier
terme (`ナイキ トレイル` + `ナイキ ベスト` → une seule requête `ナイキ`) et
retrouver les mots-clés par matching local. **C'était une erreur**, et elle
expliquait l'essentiel des « il ne trouve rien » :

Mercari ne renvoie que les **120 annonces les plus récentes**. Sur une requête
large comme `ナイキ`, ces 120 places couvrent quelques secondes et sont à 99 %
hors sujet. Le budget de requêtes partait donc presque entièrement dans des
articles qui ne pouvaient pas matcher — et comme la page débordait à chaque
scan, on ratait quand même des annonces.

Le bot interroge maintenant **le mot-clé tel quel**. Mercari fait le filtrage
côté serveur : les 120 places sont toutes pertinentes et couvrent des heures.

Le coût est assumé : N mots-clés = N requêtes par cycle. Le budget se répartit
explicitement entre elles, et le dashboard affiche la cadence réellement
tenue (« chaque mot-clé revisité toutes les X s ») plutôt que de laisser les
sources prendre du retard en silence.

Les `config.yaml` produits par la version intermédiaire se réparent seuls :
une source automatique dont le rendement reste sous 0,5 % après 400 annonces
examinées est remplacée par des requêtes précises.

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

### 8. Les annonces perdues (v2.1)

Une source ne voit que les `page_size` annonces les plus récentes. Si plus
d'une page était publiée entre deux scans, le surplus disparaissait
définitivement — sans que rien ne le signale.

Le bot détecte maintenant ce cas : **si *toutes* les annonces d'une page sont
inédites**, c'est le symptôme d'un débordement. Il remonte alors les pages
suivantes jusqu'à retrouver une annonce déjà connue (la continuité est
rétablie), puis resserre l'intervalle de cette source. Le compteur de
rattrapages est visible dans le dashboard.

### 9. Les mots-clés ajoutés qui ne cherchaient rien (v2.1)

Trois défauts se cumulaient :

- **La déduplication était irréversible.** Une annonce écartée faute de
  mot-clé correspondant était marquée « vue » pour toujours — un mot-clé
  ajouté ensuite ne pouvait plus jamais la retrouver. Toutes les annonces
  brutes passent désormais par un tampon de 30 min ; ajouter un mot-clé le
  **repasse immédiatement** sur ce tampon (les trouvailles apparaissent avec
  l'étiquette « rattrapage »).
- **La couverture n'était pas vérifiée sérieusement.** Le test était une
  comparaison de sous-chaînes brutes, fragile avec l'espacement japonais. Il
  travaille maintenant sur du texte normalisé : une source couvre un mot-clé
  si tous ses termes s'y retrouvent. Sinon, une source dédiée est créée et
  **démarre aussitôt**.
- **Rien n'était sauvegardé.** Les mots-clés ajoutés depuis le dashboard ne
  vivaient qu'en mémoire et disparaissaient au redémarrage. Ils sont
  maintenant écrits dans `config.yaml` (écriture différée : dix ajouts
  d'affilée = une seule écriture).

### 10. Cadence adaptative et couverture (v2.1)

L'intervalle de chaque source suit désormais son **débit réel** : une source
qui frôle le débordement accélère, une source calme s'espace et rend son
budget aux autres. Et comme ajouter des mots-clés peut dépasser le budget de
requêtes, le dashboard **prévient explicitement** quand la demande dépasse
`global_rate_limit`, ou qu'un mot-clé n'est couvert par aucune source.

### 11. Aucun mot-clé par défaut (v2.1)

Le bot démarre vierge. On ajoute ses mots-clés depuis le dashboard, qui les
sauvegarde. `mercari-sniper init` continue d'importer un `keywords.json` v1
s'il en trouve un.

### 12. Commander en un geste (v2.2)

Chaque annonce détectée porte un lien direct vers sa page **Buyee**, le proxy
d'achat qui commande sur Mercari et réexpédie à l'international. Le lien
apparaît dans le dashboard (bouton « Commander ») et dans les notifications
Discord.

Le format d'URL de Buyee n'a **pas** pu être vérifié en ligne pendant le
développement (buyee.jp était bloqué par le réseau). Les gabarits sont donc
**configurables** dans `config.yaml` : si un lien tombe à côté, une ligne
suffit à le corriger, sans toucher au code.

### 13. Le bot sur le téléphone (v2.2)

Le dashboard est une **PWA** : « Ajouter à l'écran d'accueil » suffit à
l'installer comme application, sur Android comme sur iOS. Un projet Android
complet (`android/`) fournit en plus un vrai APK, compilé automatiquement par
GitHub Actions.

L'application est un **client**, pas le moteur : Android arrête les processus
en arrière-plan, une boucle de scan à 2 s y serait tuée en quelques minutes.
Le PC scanne, le téléphone affiche et permet de commander.

### Récapitulatif

| | v1 | v2 |
|---|---|---|
| Requêtes / cycle | 61 | **7** |
| Intervalle | 30-60 s | **2 s** |
| Latence de détection | 30-90 s | **quelques secondes** |
| Connexions HTTP | rouvertes à chaque scan | **HTTP/2 persistant** |
| Discord | bloque le scan | **asynchrone** |
| Dédup | réécriture JSON complète | **set mémoire + SQLite groupé** |
| Annonces perdues | silencieuses | **détectées et rattrapées** |
| Mot-clé ajouté | mémoire seule, sans rattrapage | **persisté + rattrapage immédiat** |
| Interface | Tkinter local | **dashboard web temps réel** |
| Crash au lancement | trace invisible | **diagnostic + fenêtre maintenue** |
| Tests | aucun | **244** |

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
mercari-sniper run --lan         # accessible depuis le téléphone (même Wi-Fi)
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
├── buffer.py        Tampon d'annonces récentes (rend la dédup réversible)
├── buyee.py         Liens de commande vers le proxy d'achat
├── server.py        API REST + WebSocket
├── backends/        mercari_api (réel) · simulator (hors ligne)
├── notifiers/       discord (async) · console
└── web/             dashboard + PWA (manifeste, service worker, icônes)

android/             Application Android (WebView) — voir MOBILE.md
.github/workflows/   Compilation automatique de l'APK
```

**Modèle d'exécution.** Une tâche asyncio par source, toutes partageant un
token bucket global. Le matching local (index inversé sur le mot le plus
discriminant de chaque règle) n'évalue qu'une poignée de règles par titre au
lieu des 61.

---

## Tests

```bash
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest -q      # 244 tests
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
| Trop peu de trouvailles | Requête trop large : beaucoup d'annonces examinées, presque aucune retenue | Regarde la tuile **Rendement** et le panneau **Couverture**. Préfère `ナイキ トレイル` à `ナイキ` |
| Chaque mot-clé revisité trop lentement | Plus de sources que le budget n'en permet | Monte `poll.global_rate_limit`, ou retire des mots-clés. La cadence réelle est affichée |
| Des parfums / cosmétiques remontent | Mot-clé de marque seule | Panneau **Filtrage** : garde « Parfums, cosmétiques et soins » coché, ou précise le mot-clé |
| Une pièce attendue ne remonte jamais | Un filtre l'écarte | La tuile **Rendement** compte les annonces écartées. Décoche un groupe ou retire ton mot exclu |
| Pas de notification Discord | Webhook absent | Renseigne `DISCORD_WEBHOOK_URL` dans `.env` |
| Dashboard inaccessible | Port occupé | `mercari-sniper run --port 9000` |
| `ERR_ADDRESS_INVALID` dans le navigateur | `0.0.0.0` a été saisi : c'est l'adresse d'écoute, pas une destination | Ouvre `http://127.0.0.1:8420` — celle que le bot affiche |
| Le téléphone dit « Bot injoignable » | Le bot n'écoute que sur le PC | Relance avec `--lan`, puis saisis l'adresse `192.168.…` affichée |
| `403` sur l'API | Réseau filtré (VPN, proxy d'entreprise) | Teste avec `--demo` pour isoler |

Le journal complet est dans `logs/sniper.log`.

---

## Notes

Ce bot interroge une API publique non documentée : elle peut changer sans
préavis. Le `global_rate_limit` par défaut (5 req/s) reste raisonnable ;
le pousser trop haut expose à un blocage temporaire de ton IP.

Usage personnel de veille. Respecte les conditions d'utilisation de Mercari.
