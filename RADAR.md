# Radar Mercari

Détection en temps réel des nouvelles annonces **Mercari Japon**, avec
dashboard web et notifications Telegram.

```bash
radar init          # crée radar.yaml
radar doctor        # vérifie l'installation
radar run           # scanner + dashboard → http://127.0.0.1:8899
radar scrape-test   # que contient la page de recherche Mercari ?
```

**Windows :** double-clique `run.bat`. **macOS / Linux :** `./run.sh`.

---

## Comment ça marche

Deux chemins vers Mercari, et le bot choisit tout seul.

**La page de recherche** (`mode: web`) — celle des nouvelles annonces :

```
https://jp.mercari.com/search?keyword=…&sort=created_time&order=desc&status=on_sale
```

Le lecteur essaie trois formes, dans l'ordre : le script `__NEXT_DATA__`,
le flux RSC (`self.__next_f.push`, le routeur App de Next.js — les objets
JSON y sont recollés avant lecture), puis le DOM. Il dit laquelle a
fonctionné.

**L'API de recherche** (`mode: api`) — `api.mercari.jp`, du JSON structuré,
authentifié par DPoP : une paire de clés générée au démarrage, en mémoire.
Aucun compte, aucun mot de passe, aucun cookie.

**`mode: auto`** (défaut) — la page d'abord, l'API en secours si la page ne
rend rien d'exploitable, avec un message qui le dit. C'est le garde-fou :
si Mercari monte sa liste en JavaScript, aucun lecteur HTTP ne peut la
voir, et le bot doit continuer à trouver des annonces plutôt que de se
taire.

### Savoir ce que la page contient vraiment

```bash
radar scrape-test "nike acg"
radar scrape-test "nike" --save page.html
```

La commande va chercher la page **depuis ta machine** et rapporte quels
marqueurs sont présents, ce que chaque lecteur a extrait, et quoi faire
ensuite. C'est la seule façon de répondre à la question — je n'ai jamais
pu charger cette page, elle est bloquée depuis l'environnement où ce code
est écrit.

### Dans les deux cas

- **Tri par date de publication.** Les annonces les plus récentes en tête.
- **Latence mesurée.** Quand la date de publication est disponible, le
  délai affiché est réel. Sinon l'interface affiche « — », jamais « 0 s ».
- **Aucun contournement de limite de débit.** Un 429 est respecté,
  `Retry-After` compris, et un disjoncteur écarte la source le temps
  qu'elle se remette.

---

## Installation

Prérequis : **Python 3.10+**. Node.js seulement si tu veux recompiler le
dashboard — une version compilée est fournie.

```bash
python -m venv .venv
source .venv/bin/activate        # Windows : .venv\Scripts\activate
pip install -e .
radar doctor
```

---

## Configuration

Deux fichiers, une règle stricte :

| Fichier | Contenu | Versionné ? |
|---|---|---|
| `radar.yaml` | tous les réglages | oui, il est fait pour être partagé |
| `.env` | **uniquement** les secrets | **non**, git-ignoré |

Aucun secret ne peut atterrir dans `radar.yaml` : la sérialisation lève si
un jeton ou une URL de webhook s'y glisse, et un test le vérifie.

Le fichier se relit tout seul en cas de faute de frappe : une valeur mal
tapée est convertie quand c'est possible (`'8,5'` → `8.5`, `oui` → `true`),
et retombe sur la valeur par défaut sinon, **en nommant la clé fautive**.
Le bot ne s'arrête jamais pour ça.

### Ajouter un mot-clé

Depuis le dashboard (page **Réglages**), ou dans `radar.yaml` :

```yaml
keywords:
  - name: Nike ACG
    search: ["ナイキ ACG", "nike acg"]   # envoyé à Mercari
    exclude: ["レプリカ", "コピー"]        # rejette l'annonce
    priority: high                      # high 2s · medium 5s · low 20s
    min_price: 3000                     # en yens
    max_price: 40000
```

Quelques principes :

- **`search` élargit, `exclude` resserre.** Sans `search`, c'est le `name`
  qui part vers Mercari.
- Les termes latins sont comparés **par frontière de préfixe** (`nike` ne
  matche pas `nikeish`), les termes japonais par **sous-chaîne** — il n'y a
  pas d'espaces en japonais.
- Tout est normalisé en NFKC : demi-largeur et pleine largeur se valent.
- Le **budget de requêtes est partagé**. Ajouter des mots-clés le répartit,
  il ne grandit pas tout seul. Priorise.

### Les réglages qui comptent

```yaml
mercari:
  max_concurrent: 6      # au-delà de ~8, Mercari répond 429
  page_size: 60          # annonces par requête (120 max)

scanner:
  budget_per_second: 8.0 # requêtes/s, tous mots-clés confondus
  warmup: true           # premier passage silencieux

filters:
  max_age_seconds: 900   # au-delà, l'annonce n'est plus « nouvelle »
  exclude: []            # termes bannis, toutes recherches confondues

api_host: 127.0.0.1      # 0.0.0.0 pour y accéder depuis le téléphone
api_port: 8899
```

---

## Notifications Telegram

Copie `.env.example` vers `.env`.

1. Sur Telegram, écris à **@BotFather** → `/newbot` → il te donne le jeton.
2. Pour recevoir dans un **canal** : crée-le, ajoute ton bot, donne-lui le
   rôle **administrateur** (sans ça il ne peut rien publier).
3. Renseigne `.env` :

```env
TELEGRAM_BOT_TOKEN=123456:AAE...
TELEGRAM_CHAT_ID=@mon_canal          # ou -1001234567890
TELEGRAM_TOPIC_ID=                   # sujet d'un groupe Forum, optionnel
```

4. Vérifie **avant** de lancer le scanner :

```bash
radar notify-test
```

Un message tient en quatre lignes : le nom, le **prix en euros**, la
source, le lien.

```
NIKE ACG トレイル ジャケット 新品未使用

76 €  ·  ¥12 500
Mercari Japon  ·  VERY RARE

🔗 Voir sur Mercari
```

L'euro passe devant parce que c'est le chiffre qu'on compare à son budget.
**Si le taux de change est indisponible, seul le yen s'affiche** — un euro
inventé serait pire qu'un yen seul.

> **Le scanner n'attend jamais une notification.** L'envoi passe par une
> file asynchrone bornée : si Telegram est lent, la détection continue à la
> même cadence.

---

## Le dashboard

**http://127.0.0.1:8899** — deux pages.

- **Flux** — trois chiffres, les mots-clés surveillés, les annonces. Un
  onglet *Archive* cherche dans tout ce qui a été enregistré.
- **Réglages** — ajouter et supprimer des mots-clés, et l'état technique
  replié en bas.

Chaque annonce affiche le **délai de détection** : l'écart entre la
publication sur Mercari et le moment où le bot l'a vue. Quand la donnée
manque, il affiche **« — »**, jamais « 0 s ».

Pour y accéder depuis le téléphone, mets `api_host: 0.0.0.0` et ouvre
`http://<IP-du-PC>:8899` sur le même Wi-Fi.

---

## Dépannage

`radar doctor` d'abord.

| Symptôme | Cause probable | Correctif |
|---|---|---|
| Rien ne remonte | aucun mot-clé actif | ajoute-en un dans Réglages |
| `HTTP 403` sur Mercari | réseau qui filtre, ou VPN | teste sans VPN ; `mercari.proxy` si besoin |
| Flux vide en `mode: web` | la page monte sa liste en JavaScript | `radar scrape-test` le confirme ; mets `mode: api` |
| Beaucoup de 429 | `max_concurrent` ou budget trop hauts | baisse `max_concurrent` à 4 |
| Rien sur Telegram | le bot n'est pas admin du canal | `radar notify-test` le dit |
| Page inaccessible du téléphone | `api_host: 127.0.0.1` | mets `0.0.0.0` |
| « port occupé » | un autre radar tourne | il bascule tout seul sur le suivant |

Les logs structurés sont dans `logs/radar.jsonl`, une ligne JSON par
événement :

```bash
grep '"level":"ERROR"' logs/radar.jsonl | tail -20
```

---

## Essayer sans réseau

```bash
radar run --demo
```

Une source **`sim_mercari`** produit un flux synthétique. Le préfixe
`sim_` est visible partout dans l'interface — rien ne se fait passer pour
du réel.
