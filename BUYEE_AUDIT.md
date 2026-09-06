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

## 2. La découverte qui structure le projet : le cross-search

Buyee expose **lui-même** une recherche transversale :

```
https://buyee.jp/item/crosssearch/query/{mot-clé}?lang=en
```

et la page se décrit ainsi, mot pour mot :

> **Supported Sites: JDirectItems Auction; JDirectItems Fleamarket; Rakuma;
> Mercari; LuxeWholeSale.**
> *You can search for products across multiple secondhand sites.*

Conséquence directe : **la couche multi-marketplace n'est pas à inventer,
elle est la plateforme.** Une requête, cinq sources. C'est le meilleur
rapport couverture/budget du système, et c'est la source activée par
défaut dans `radar.yaml`.

C'est aussi Buyee qui nous apprend l'existence de **LuxeWholeSale**, une
cinquième marketplace d'occasion qu'aucune autre URL indexée ne révèle.

## 3. Ce qui est établi : les URL

URL réellement présentes dans l'index, reproduites telles quelles :

```
https://buyee.jp/item/crosssearch/query/{mot-clé}?lang=en
https://buyee.jp/item/search/query/photocards?lang=en
https://buyee.jp/item/search/query/z/category/2084063431?sort=bids&order=asc&page=1
https://buyee.jp/item/search/query/{kw}/category/{id}?sort=end&order=…
https://buyee.jp/item/search/advanced/yahoo/auction
https://buyee.jp/jdirectitems/auction
https://buyee.jp/jdirectitems/shopping/store/top/c-well3?lang=en
https://buyee.jp/mercari/search?keyword=Gibraltar%20mail&lang=en
https://buyee.jp/mercari/search?keyword=…&status=on_sale
https://www.buyee.jp/mercari/search?seller=968116560&page=2
https://buyee.jp/rakuma/item/372996ff8780f443c723133905af67d4
https://buyee.jp/paypayfleamarket/item/z488929156
https://shop.buyee.jp/bookoff/shopping/search/category/a50182?lang=en
```

Ce qu'elles démontrent :

- Buyee **segmente par namespace de marketplace** : `/mercari/`,
  `/rakuma/`, `/paypayfleamarket/`, `/item/` et `/jdirectitems/` pour les
  enchères, `/item/crosssearch/` pour la recherche transversale.
- `/mercari/search` accepte bien **`keyword=`** — attesté par de nombreuses
  URL, avec `page=` et `status=on_sale`.
- `/item/search/query/{mot-clé}` est la forme canonique de la recherche
  côté enchères, avec `page=`, `sort=bids|bidorbuy|end`, `order=`,
  `/category/{id}` et `closed=1`.
- Les URL d'**annonce** de Rakuma (`/rakuma/item/{hex32}`) et du
  Fleamarket (`/paypayfleamarket/item/z{chiffres}`) sont confirmées.
- `shop.buyee.jp` est un **sous-domaine différent**, organisé par boutique
  partenaire et par catégorie.

## 4. Ce qui n'est PAS établi

1. **Le balisage HTML des pages de résultats.** Aucune classe CSS, aucune
   structure DOM, aucun attribut. Rien. Ni sur les namespaces dédiés, ni
   sur le cross-search.
2. **Le sélecteur qui étiquette la source** de chaque résultat du
   cross-search. Contournement retenu : l'attribution se fait sur le
   **namespace de l'URL de l'annonce** (`/mercari/item/…` → Mercari), ce
   que Buyee utilise lui-même pour router. C'est plus robuste qu'un
   libellé affiché, qui change avec la langue de la page.
3. **L'existence de `/rakuma/search` et `/paypayfleamarket/search`** :
   déduite par symétrie avec `/mercari/search`, jamais observée — et
   l'asymétrie est frappante, l'index contient des dizaines d'URL de
   recherche Mercari et **zéro** pour ces deux-là.
4. **Le paramètre de tri par date décroissante**, essentiel pour du
   monitoring temps réel. `sort=end&order=` existe côté enchères, mais la
   valeur qui donne « les plus récentes » n'est pas attestée. Les valeurs
   du code sont des hypothèses, surchargeables via `extra_params`.
5. **Les paramètres de pagination du cross-search.** Inconnus.

## 5. Pourquoi ne pas avoir écrit les sélecteurs « au jugé »

Écrire `node.css_first("li.itemCard")` sans avoir vu la page produirait du
code **d'apparence crédible et faux**. Et un scraper faux ne lève pas
d'exception : il renvoie zéro annonce, en silence, indéfiniment. Le bot
tournerait, le dashboard afficherait « en ligne », les compteurs
resteraient à zéro, et rien n'indiquerait pourquoi.

C'est le pire mode de panne possible pour un outil dont le seul travail est
de ne rien rater.

## 6. La conception qui en découle : l'extraction est une donnée

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

## 7. Statut par source

| Source | Identifiant | Niveau | Motif |
|---|---|---|---|
| **Buyee Cross-Search** | `crosssearch` | **URL_VERIFIED** | Endpoint déclaré par Buyee, avec sa liste de sites supportés. Une requête → cinq marketplaces. Sélecteurs à calibrer. |
| **JDirectItems Auction** | `jdirectitems_auction` | **URL_VERIFIED** | `/item/search/query/{mot-clé}` attesté par des dizaines d'URL, avec `page=`, `sort=`, `order=`, `/category/{id}`. |
| **Mercari** | `mercari` | **URL_VERIFIED** | `keyword=` attesté par de nombreuses URL indexées, avec `page=` et `status=on_sale`. Une source parmi d'autres. |
| **Rakuma** | `rakuma` | NEEDS_SELECTORS | URL d'annonce confirmée ; **aucune** URL `/rakuma/search` dans l'index — chemin déduit par symétrie. Couverte par le cross-search en attendant. |
| **JDirectItems Fleamarket** | `jdirectitems_fleamarket` | NEEDS_SELECTORS | Idem : URL d'annonce confirmée, chemin de recherche déduit. |
| **LuxeWholeSale** | `luxewholesale` | NEEDS_SELECTORS | Connue seulement par la liste cross-search de Buyee. **Aucune URL n'a été inventée** pour l'interroger directement : elle arrive par le cross-search, ou pas du tout. |
| **JDirectItems Shopping** | `jdirectitems_shopping` | **UNSUPPORTED** | Organisé par boutique (`/jdirectitems/shopping/store/top/{boutique}`), absent du cross-search. Catalogue, pas flux. |
| **Rakuten** | `rakuten` | **UNSUPPORTED** | `shop.buyee.jp`, par boutique et catégorie, absent du cross-search. Catalogue marchand : fiches durables et réapprovisionnées. |
| **Amazon** | `amazon` | **UNSUPPORTED** | Même famille que Rakuten. |
| **ZOZOTOWN** | `zozotown` | **UNSUPPORTED** | Catalogue de mode neuve (`shop_zozotown`), absent du cross-search. *No usable public keyword-search interface established.* |

**Buyee sépare lui-même les deux mondes** : les cinq sites d'occasion
alimentent le cross-search, les catalogues vivent sur un autre
sous-domaine et n'y figurent pas. Ce n'est pas notre interprétation, c'est
la structure de la plateforme.

Les quatre sources `UNSUPPORTED` sont **refusées au démarrage** même si elles
sont activées dans `radar.yaml`, avec le motif en clair dans les logs et
dans l'API. Elles ne sont pas simulées, pas remplacées, pas masquées.

---

## 8. Ce qui remplace les sources réelles pour la démonstration

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

## 9. Respect des plateformes

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

## 10. Comment cet audit peut être refait

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
