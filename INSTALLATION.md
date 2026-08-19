# Installer Mercari Sniper sur un PC neuf

Guide complet, depuis une machine Windows vierge jusqu'au bot qui tourne.
Compter **10 à 15 minutes**, dont 2 minutes d'attente pendant l'installation.

Il te faut : Windows 10 ou 11, une connexion internet, environ 300 Mo d'espace
disque. Aucune connaissance technique n'est nécessaire.

---

## Étape 1 — Installer Python

Le bot est écrit en Python. Windows ne le fournit pas, il faut donc l'ajouter.

1. Va sur **https://www.python.org/downloads/**
2. Clique sur le gros bouton jaune **« Download Python »** (version 3.12 ou 3.13)
3. Lance le fichier téléchargé

> **⚠ Le point le plus important de tout ce guide.**
> Sur le premier écran de l'installateur, **coche la case
> « Add python.exe to PATH »** en bas de la fenêtre, *avant* de cliquer sur
> Install. Sans elle, Windows ne saura pas où trouver Python et le bot ne
> démarrera pas.

4. Clique sur **Install Now**, puis attends la fin
5. Ferme l'installateur

### Vérifier que ça a marché

Ouvre le menu Démarrer, tape `cmd`, ouvre **Invite de commandes**, puis saisis :

```
python --version
```

Tu dois voir quelque chose comme `Python 3.13.1`.

Si tu obtiens `'python' n'est pas reconnu...`, la case PATH n'a pas été cochée.
Relance l'installateur, choisis **Modify**, puis **Repair** en cochant bien la
case cette fois.

---

## Étape 2 — Décompresser le bot

1. Récupère le fichier `Bot-Mercari-v2.1.zip`
2. **Clic droit dessus → « Extraire tout… »**
3. Choisis un dossier simple, par exemple `C:\Bot-Mercari`
4. Clique sur **Extraire**

> **⚠ Décompresse vraiment le dossier.**
> Windows laisse ouvrir un `.zip` comme s'il s'agissait d'un dossier normal.
> Si tu lances le bot depuis l'intérieur du zip, il échouera : les fichiers ne
> sont pas réellement sur le disque. Après extraction, tu dois voir un dossier
> `Bot-Mercari` contenant `run.bat`, `main.py`, `src`, `requirements.txt`.

---

## Étape 3 — Premier lancement

Ouvre le dossier extrait et **double-clique sur `run.bat`**.

Windows peut afficher un avertissement bleu **« Windows a protégé votre
ordinateur »**. C'est normal pour un fichier téléchargé : clique sur
**Informations complémentaires**, puis **Exécuter quand même**.

Une fenêtre noire s'ouvre. Au premier lancement, elle va :

1. créer un environnement isolé (`.venv`) — quelques secondes
2. télécharger les dépendances — **1 à 2 minutes**, c'est le plus long
3. créer la configuration (`config.yaml`)
4. démarrer le bot et **ouvrir ton navigateur** sur le dashboard

Tu dois voir défiler, dans l'ordre :

```
  ==========================================
    Mercari Sniper - demarrage
  ==========================================

  [i] Premiere utilisation, preparation en cours...
  [i] Creation de l'environnement virtuel (.venv)...
  [OK] Environnement cree.
  [i] Installation des dependances...
  [OK] Installation terminee.

  ➜  Dashboard : http://127.0.0.1:8420
  ➜  Ctrl+C pour arrêter
```

Le navigateur s'ouvre tout seul sur `http://127.0.0.1:8420`. S'il ne le fait
pas, ouvre ton navigateur et colle cette adresse à la main.

> **La fenêtre noire doit rester ouverte.** C'est le bot lui-même. La fermer
> arrête la surveillance. Tu peux la réduire dans la barre des tâches.

### Vérifier que l'API Mercari répond

Avant d'aller plus loin, teste que ta connexion atteint bien Mercari. Ouvre une
**seconde** invite de commandes, puis :

```
cd C:\Bot-Mercari
.venv\Scripts\python main.py once "nike acg"
```

Tu dois voir une liste d'annonces réelles avec leurs prix et leurs liens. Si tu
obtiens une erreur ou une liste vide, va voir la section Dépannage.

---

## Étape 4 — Ajouter tes mots-clés

Le bot démarre **vierge** : c'est à toi de lui dire quoi chercher.

Dans le dashboard, panneau de gauche, champ **Mots-clés** : saisis un terme et
appuie sur Entrée. Par exemple :

```
ナイキ トレイル
nike acg
アンダーアーマー
```

À chaque ajout, le bot :

- crée automatiquement la requête correspondante vers Mercari ;
- **repasse les annonces déjà scannées** et remonte aussitôt celles qui
  correspondent (elles apparaissent avec l'étiquette « rattrapage ») ;
- enregistre le mot-clé dans `config.yaml`, donc il survit au redémarrage.

### Comment fonctionne le matching

Un mot-clé touche une annonce si **tous ses mots** apparaissent dans le titre,
dans n'importe quel ordre. La casse et la largeur des caractères japonais
n'ont pas d'importance : `ﾅｲｷ`, `ナイキ` et `NIKE` sont traités pareil.

- `ナイキ トレイル` → touche « ナイキ トレイル ジャケット » et « トレイル ナイキ ベスト »
- `nike acg` → touche « NIKE ACG Vest » mais pas « Nike Air Max »

Plus le mot-clé est précis, moins tu auras de bruit.

> **Le premier cycle est silencieux, c'est voulu.** Au démarrage d'une source,
> le bot mémorise les annonces déjà en ligne sans les notifier — sinon tu
> recevrais 120 alertes pour des annonces vieilles de plusieurs jours. Les
> vraies nouveautés arrivent ensuite.

---

## Étape 5 — Notifications Discord (facultatif)

Pour recevoir les trouvailles dans un salon Discord :

1. Dans Discord : **Paramètres du salon → Intégrations → Webhooks → Nouveau
   webhook → Copier l'URL**
2. Dans le dossier du bot, copie le fichier `.env.example` et renomme la copie
   en `.env`
3. Ouvre `.env` avec le Bloc-notes et colle ton URL :

```
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
```

4. Enregistre, ferme la fenêtre du bot, relance `run.bat`

En haut à droite du dashboard, l'indicateur doit passer à **« Discord actif »**.

> **Ne partage jamais cette URL.** Quiconque la possède peut écrire dans ton
> salon. Le fichier `.env` est exclu du dépôt Git pour cette raison.

---

## Utilisation au quotidien

| Action | Comment |
|---|---|
| Démarrer | Double-clic sur `run.bat` |
| Arrêter | `Ctrl+C` dans la fenêtre noire, ou la fermer |
| Ouvrir le dashboard | `http://127.0.0.1:8420` |
| Diagnostiquer un problème | Double-clic sur `diagnostic.bat` |
| Voir le journal détaillé | `logs\sniper.log` |
| Historique des annonces | `data\sniper.db` |

Les lancements suivants sont **immédiats** : l'environnement est déjà installé.

---

## Réglages utiles

Ouvre `config.yaml` avec le Bloc-notes. Les réglages qui comptent :

```yaml
poll:
  interval: 2.0            # secondes entre deux scans d'une même source
  global_rate_limit: 5.0   # requêtes/seconde au total

filters:
  min_price: null          # en yens, null = pas de limite
  max_price: null
  exclude_words: []        # ex. [ジャンク, 訳あり]

notify:
  min_rarity: PREMIUM      # PREMIUM (tout) | RARE | ULTRA RARE
```

**Pour aller plus vite :** baisse `interval` à `1.5` et monte
`global_rate_limit` à `6`. Au-delà d'environ 8 requêtes/seconde, Mercari
répond des erreurs 429 ; le bot ralentit alors tout seul, mais tu perds en
réactivité.

Ferme et relance `run.bat` après toute modification.

---

## Dépannage

| Symptôme | Cause | Solution |
|---|---|---|
| La fenêtre se ferme aussitôt | Python absent ou case PATH non cochée | Double-clic sur `diagnostic.bat` — il dit exactement quoi corriger |
| `'python' n'est pas reconnu` | Case PATH non cochée | Relancer l'installateur Python → Modify → Repair, en cochant la case |
| `[X] Python est introuvable` | Idem | Idem |
| L'installation des dépendances échoue | Pas de connexion, ou antivirus | Vérifier internet, autoriser le dossier dans l'antivirus, relancer |
| Le dashboard ne s'ouvre pas | Port 8420 déjà pris | Dans `config.yaml`, mettre `port: 9000`, relancer |
| Aucune annonce ne remonte | Premier cycle silencieux | Attendre 1 à 2 minutes |
| Toujours aucune annonce | Mots-clés trop précis | En essayer un plus large, ex. `nike` |
| `once` renvoie une erreur 403 | VPN, proxy, ou réseau d'entreprise | Tester sans VPN ; vérifier avec `main.py run --demo` que le reste fonctionne |
| Beaucoup d'erreurs 429 | Cadence trop agressive | Baisser `global_rate_limit` dans `config.yaml` |
| Pas de notification Discord | Webhook absent ou invalide | Vérifier `.env`, puis relancer |

Le fichier `logs\sniper.log` contient toujours le détail complet.

### Tester sans réseau

Pour vérifier que l'interface fonctionne indépendamment de Mercari :

```
.venv\Scripts\python main.py run --demo
```

Le bot génère de fausses annonces et n'envoie aucune requête à Mercari. Si le
dashboard s'anime dans ce mode mais reste vide en mode normal, le problème
vient de l'accès réseau à Mercari, pas du bot.

---

## macOS et Linux

Python 3.10+ est généralement déjà présent. Dans un terminal :

```bash
cd ~/Bot-Mercari
chmod +x run.sh
./run.sh
```

Le script crée l'environnement, installe les dépendances et démarre le bot,
exactement comme `run.bat` sous Windows.

---

## Désinstaller

Le bot n'écrit rien en dehors de son propre dossier et ne modifie pas le
registre Windows. Pour tout supprimer : ferme la fenêtre, puis supprime le
dossier `C:\Bot-Mercari`.

Python reste installé ; désinstalle-le depuis **Paramètres → Applications** si
tu n'en as pas d'autre usage.
