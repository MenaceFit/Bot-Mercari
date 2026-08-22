# Le bot sur ton téléphone

Deux façons d'avoir Mercari Sniper comme application sur ton mobile. La
première ne demande aucune compilation et fonctionne sur Android **et** iOS.

> **Le bot reste sur le PC.** Le téléphone en est la fenêtre, pas le moteur.
> Android arrête les processus en arrière-plan (Doze) : une boucle de scan à
> 2 secondes y serait tuée en quelques minutes et viderait la batterie. Le PC
> scanne 24 h/24, le téléphone affiche et permet de commander.

---

## Étape commune — rendre le bot visible sur ton réseau

Par défaut, le bot n'écoute que sur le PC lui-même. Pour que le téléphone
puisse s'y connecter, lance-le avec `--lan` :

```
run-mobile.bat         (Windows — double-clic, rien à taper)
run.bat --lan          (Windows, en ligne de commande)
./run.sh --lan         (Linux / macOS)
```

Le bot affiche alors les deux adresses :

```
  ➜  Dashboard : http://127.0.0.1:8420
  ➜  Depuis ton téléphone : http://192.168.1.20:8420
```

C'est la **deuxième** que tu saisis dans l'application. La première ne vaut
que sur le PC.

> **Ne saisis jamais `0.0.0.0`.** C'est l'adresse sur laquelle le bot *écoute*,
> pas une adresse joignable : un navigateur répond `ERR_ADDRESS_INVALID`, et
> l'application affiche « Bot injoignable ». De même, `127.0.0.1` saisi sur le
> téléphone désigne le téléphone, pas le PC.

Pour rendre le réglage permanent, mets `host: 0.0.0.0` dans la section
`server` de `config.yaml` — `--lan` ne fait rien d'autre.

Le téléphone et le PC doivent être sur **le même Wi-Fi**. Si la page ne
s'ouvre pas, autorise Python dans le pare-feu Windows (une fenêtre le propose
au premier lancement — coche « Réseaux privés »).

---

## Option 1 — Installer le dashboard comme application (recommandé)

Aucune compilation, fonctionne immédiatement.

**Android (Chrome)**
1. Ouvre `http://192.168.1.20:8420` dans Chrome
2. Menu ⋮ → **Ajouter à l'écran d'accueil**
3. L'icône apparaît comme une vraie application, en plein écran

**iPhone (Safari)**
1. Ouvre la même adresse dans Safari
2. Bouton Partager → **Sur l'écran d'accueil**

L'application garde son thème, fonctionne en plein écran, et affiche un écran
propre quand le PC est éteint plutôt qu'une erreur de navigateur.

---

## Option 2 — L'application Android

Une vraie application native : écrans dédiés, listes fluides, navigation par
onglets. Le projet complet est dans le dossier `android/`.

### Ce qu'elle contient

| Onglet | Ce qu'on y fait |
|---|---|
| **Flux** | Les annonces détectées : vignette, palier de rareté, prix, latence de détection, bouton **Commander** vers Buyee. Tirer vers le bas pour rafraîchir. |
| **Mots-clés** | Ajouter et retirer, voir le nombre de trouvailles par mot-clé, repérer ceux qu'aucune source ne couvre encore. |
| **Réglages** | Adresse du bot, état de la connexion, compteurs, accès au dashboard complet. |

Au premier lancement, l'application demande l'adresse du bot et **teste la
connexion** avant de l'enregistrer : si le bot ne répond pas, elle le dit
tout de suite, avec la marche à suivre.

Les liens Buyee et Mercari s'ouvrent dans ton **navigateur habituel**, jamais
dans l'application : la commande a besoin de ta session et de tes moyens de
paiement déjà enregistrés.

### La récupérer, compilée par GitHub

Le SDK Android n'est pas nécessaire de ton côté : un workflow GitHub Actions
compile l'APK à chaque modification.

1. Va sur l'onglet **Actions** de ton dépôt
2. Ouvre le dernier run **Build APK** (ou lance-le avec **Run workflow**)
3. En bas de la page, section **Artifacts** → télécharge **mercari-sniper-apk**
4. Décompresse, transfère `app-debug.apk` sur ton téléphone, ouvre-le
5. Android demandera d'autoriser l'installation depuis cette source — accepte

L'artefact reste disponible 90 jours.

### La compiler toi-même

Avec Android Studio : ouvrir le dossier `android/`, puis **Build → Build APK**.

En ligne de commande, avec le SDK Android installé :

```bash
cd android
gradle assembleDebug
# → app/build/outputs/apk/debug/app-debug.apk
```

> L'APK produit est le variant **debug**, signé avec la clé de debug — c'est
> volontaire : un APK *release* non signé refuse de s'installer.

---

## Dépannage

| Symptôme | Solution |
|---|---|
| « Bot injoignable » dans l'app | Le bot est-il lancé avec `--lan` ? As-tu saisi l'adresse `192.168.…` (pas `0.0.0.0`, pas `127.0.0.1`) ? Même Wi-Fi ? |
| `ERR_ADDRESS_INVALID` dans le navigateur | Tu as ouvert `0.0.0.0:8420`. Sur le PC, ouvre `127.0.0.1:8420` ; depuis le téléphone, l'adresse `192.168.…`. |
| La page ne charge pas dans Chrome | Autoriser Python dans le pare-feu Windows, réseaux privés |
| « Ajouter à l'écran d'accueil » absent | Recharge la page une fois ; Chrome a besoin du service worker |
| L'adresse du PC a changé | Dans l'app : **Changer d'adresse**. Mieux : réserver l'IP dans ta box |
| Les liens Buyee s'ouvrent dans l'app | Ils sont censés s'ouvrir dans ton navigateur — signale-le si ce n'est pas le cas |
| Le flux reste vide | Onglet Mots-clés : en as-tu ajouté au moins un ? |
| Bandeau rouge « Hors ligne » | Le PC est éteint, en veille, ou tu as changé de Wi-Fi. L'app se reconnecte seule dès qu'il répond. |

Une IP fixe évite d'avoir à ressaisir l'adresse : dans l'interface de ta box,
réserve l'adresse du PC par son adresse MAC (« bail DHCP statique »).
