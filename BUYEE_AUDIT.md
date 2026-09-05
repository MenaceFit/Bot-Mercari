# Audit d'accès Buyee — ce qui a pu être établi, et ce qui ne l'a pas été

> Document de référence cité par `src/buyee_radar/adapters/buyee_html.py`.
> Il explique **pourquoi** les sources réelles arrivent en `NEEDS_SELECTORS`
> plutôt qu'en « prêtes à l'emploi », et ce qu'il reste à faire côté
> utilisateur pour les activer.

---

## 1. Méthode et limite de la méthode

L'environnement dans lequel ce code a été écrit est un conteneur dont la
sortie réseau passe par une passerelle filtrante. Résultat des tests :

| Hôte | Résultat |
|---|---|
| `buyee.jp` | `000` — 403 sur le `CONNECT` |
| `api.mercari.jp` | `000` — 403 sur le `CONNECT` |
| `auctions.yahoo.co.jp` | `000` — 403 sur le `CONNECT` |
| `fril.jp` (Rakuma) | `000` — 403 sur le `CONNECT` |
| `paypayfleamarket.yahoo.co.jp` | `000` — 403 sur le `CONNECT` |

Trace exacte, pour qu'elle soit reproductible :

```
> CONNECT buyee.jp:443 HTTP/1.1
< HTTP/1.1 403 Forbidden
```

Les outils de récupération de page disponibles ont répondu de la même
manière (`EGRESS_BLOCKED` sur `buyee.jp`), et l'outil de recherche
disponible n'expose pas de fonction de *scrape*, uniquement de recherche.

> À noter : `buyee-radar benchmark` peut afficher des temps DNS/TCP/TLS pour
> `buyee.jp` même dans cet environnement — il mesure l'établissement de
> connexion, pas une requête HTTP aboutie. Les deux ne disent pas la même
> chose, et c'est la seconde qui compte ici.

**Conséquence directe : aucune page de résultats Buyee n'a jamais été
chargée pendant l'écriture de ce code.** Tout ce qui suit vient de l'index
des moteurs de recherche, qui renvoie des **URL réelles** — c'est une preuve
de *structure d'URL*, ce n'est pas une preuve de *contenu HTML*.

Cette distinction gouverne toute l'architecture du projet.

---

## 2. Ce qui est établi : les URL

URL réellement présentes dans l'index, reproduites telles quelles :

```
https://buyee.jp/item/search/query/photocards?lang=en
https://buyee.jp/item/search/query/{mot-clé}/category/{id}?lang=en
https://buyee.jp/item/search?query={mot-clé}&category={id}&lang=en
https://buyee.jp/item/search/advanced/yahoo/auction
https://buyee.jp/mercari/search?seller=488413427&lang=en
https://www.buyee.jp/mercari/search?seller=968116560&page=2
https://buyee.jp/rakuma/?lang=en
https://buyee.jp/paypayfleamarket/?lang=en
https://shop.buyee.jp/{partenaire}/shopping/search/category/{id}?page=5
```

Ce qu'elles démontrent :

- Buyee **segmente par namespace de marketplace** : `/mercari/`, `/rakuma/`,
  `/paypayfleamarket/`, et `/item/` pour les enchères (JDirectItems).
- `/mercari/search` **existe** et accepte au moins `seller=`, `page=` et
  `lang=`.
- `/item/search/query/{mot-clé}` **existe** et est la forme canonique de la
  recherche par mot-clé côté enchères, avec des variantes `/category/{id}`
  et `?query=`.
- `shop.buyee.jp` est un **sous-domaine différent**, organisé par boutique
  partenaire et par catégorie.

## 3. Ce qui n'est PAS établi

1. **Le balisage HTML des pages de résultats.** Aucune classe CSS, aucune
   structure DOM, aucun attribut. Rien.
2. **Le nom exact du paramètre de mot-clé** sur `/mercari/search`,
   `/rakuma/search` et `/paypayfleamarket/search` (l'index n'expose que
   `seller=`). `keyword=` est l'hypothèse retenue, surchargeable.
3. **L'existence même** de `/rakuma/search` et `/paypayfleamarket/search` :
   déduite par symétrie avec `/mercari/search`, pas observée.
4. **Le paramètre de tri par date décroissante**, essentiel pour du
   monitoring temps réel. Les valeurs présentes dans le code sont des
   hypothèses raisonnables, marquées comme telles.

---

## 4. Pourquoi ne pas avoir écrit les sélecteurs « au jugé »

Écrire `node.css_first("li.itemCard")` sans avoir vu la page produirait du
code **d'apparence crédible et faux**. Et un scraper faux ne lève pas
d'exception : il renvoie zéro annonce, en silence, indéfiniment. Le bot
tournerait, le dashboard afficherait « en ligne », les compteurs
resteraient à zéro, et rien n'indiquerait pourquoi.

C'est le pire mode de panne possible pour un outil dont le seul travail est
de ne rien rater.

## 5. La conception qui en découle : l'extraction est une donnée

Les sélecteurs ne sont pas dans le code. Ils sont dans `radar.yaml`, et la
commande

```bash
buyee-radar calibrate --source mercari --keyword nike
```

les **découvre automatiquement** sur la machine de l'utilisateur, là où
`buyee.jp` est joignable. L'algorithme (`adapters/calibrate.py`) :

1. charge une page de résultats réelle ;
2. cherche le conteneur d'annonce = le `tag.class` répétitif le **plus
   profond** dont ≥ 70 % des occurrences contiennent un lien, ≥ 50 % un
   prix, et dont les contenus texte sont **distincts** (un menu de
   navigation se répète aussi, mais son contenu varie peu et il n'a pas de
   prix — le mot « menu », « nav », « wrapper », « container » dans le nom
   de classe est en plus exclu explicitement) ;
3. en déduit les sélecteurs de titre, prix, lien, image ;
4. renvoie un **échantillon extrait** que l'utilisateur relit avant
   d'enregistrer (`--dry-run` pour ne rien écrire).

Tant que les sélecteurs sont vides, l'adapter se déclare `NEEDS_SELECTORS`,
n'est **pas planifié** par le scanner, et le dashboard l'affiche comme tel.
Jamais « en ligne » alors qu'il ne ramène rien.

---

## 6. Statut par source

| Source | Niveau | Motif |
|---|---|---|
| `jdi_auction` | **URL_VERIFIED** | `/item/search/query/{mot-clé}` attestée par de nombreuses URL indexées. Sélecteurs à calibrer. |
| `mercari` | **NEEDS_SELECTORS** | Namespace `/mercari/search` confirmé ; paramètre de mot-clé et tri à confirmer. |
| `rakuma` | **NEEDS_SELECTORS** | Namespace `/rakuma/` confirmé ; chemin de recherche déduit par symétrie. |
| `jdi_fleamarket` | **NEEDS_SELECTORS** | Namespace `/paypayfleamarket/` confirmé ; chemin de recherche déduit par symétrie. |
| `jdi_shopping` | **UNSUPPORTED** | `shop.buyee.jp` est un catalogue de boutiques partenaires, organisé par boutique et catégorie. Aucune recherche par mot-clé à l'échelle du site n'a pu être établie — et un catalogue de boutique n'est pas un flux de nouveautés : le surveiller pour du sniping n'aurait pas de sens. |
| `rakuten` | **UNSUPPORTED** | Catalogue de marchands : fiches produit durables et réapprovisionnées, pas de flux de nouvelles annonces. Aucun namespace `/rakuten/search` trouvé dans l'index. |

Les deux sources `UNSUPPORTED` sont **refusées au démarrage** même si elles
sont activées dans `radar.yaml`, avec le motif en clair dans les logs et
dans l'API. Elles ne sont pas simulées, pas remplacées, pas masquées.

---

## 7. Ce qui remplace les sources réelles pour la démonstration

Quatre adapters **explicitement nommés `sim_*`** (`sim_mercari`,
`sim_rakuma`, `sim_jdi_fleamarket`, `sim_jdi_auction`) produisent un flux
synthétique, avec des cadences et des latences distinctes par source. Ils
servent à valider la chaîne complète — planification, déduplication,
filtrage, scoring, stockage, WebSocket, dashboard — sans réseau.

Ils ne se font passer pour rien : le préfixe `sim_` est dans le nom de la
source, il apparaît tel quel dans le dashboard, et un test vérifie qu'aucune
marketplace réelle ne se déclare pleinement vérifiée.

```bash
buyee-radar once --demo --dry-run     # chaîne complète, zéro réseau
```

---

## 8. Respect des plateformes

- Uniquement des pages **publiques**, en **GET**, avec un User-Agent honnête.
- Concurrence **bornée par adapter**, budget de requêtes/seconde global.
- `Retry-After` respecté ; disjoncteur par source avec temporisation
  exponentielle plafonnée.
- **Aucun contournement** de CAPTCHA, d'authentification, de limite de débit
  ou de protection anti-bot. Si une page renvoie une signature de challenge
  (`captcha`, `recaptcha`, `unusual traffic`, `アクセスが制限`…), l'adapter
  **s'arrête et le signale** au lieu d'insister — et ne persiste rien de
  cette réponse.

---

## 9. Comment cet audit peut être refait

Sur une machine où `buyee.jp` répond :

```bash
buyee-radar health                       # joignabilité + statut par source
buyee-radar calibrate --dry-run          # découverte, sans écrire
buyee-radar calibrate --source mercari   # découverte + enregistrement
```

Si `calibrate` échoue ou renvoie un échantillon incohérent, c'est que la
page n'a pas la forme supposée — corrige alors `search_url` et `selectors`
à la main dans `radar.yaml`, les deux sont prévus pour ça. Aucune
modification de code n'est nécessaire pour suivre un changement de Buyee.
