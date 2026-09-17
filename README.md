# La Caverne des Monts

Boutique Django en français, avec catalogue administrable, panier en session et intégration Stripe Checkout hébergée.

## Démarrer en une commande

Depuis le dossier du projet :

```sh
./start
```

Le script prépare `.venv` au besoin, installe uniquement les dépendances manquantes ou incompatibles, vérifie Django, applique les migrations et importe le catalogue sans écraser les modifications existantes. Il lance ensuite le serveur et ouvre le navigateur quand la page répond. Garder le terminal ouvert ; **Ctrl+C** arrête le serveur et ses sous-processus.

Adresse locale : **http://127.0.0.1:8000/** (HTTP, sans « s »). Une URL commençant par `https://` provoque `ERR_SSL_PROTOCOL_ERROR`, car ce serveur de développement ne fournit pas de TLS. Le lanceur force uniquement son environnement local en mode développement, sans modifier `.env` ni les réglages de production.

Options : `./start --no-browser` et `./start --port 8001`. Un port occupé est signalé sans arrêter le processus existant. Le serveur écoute uniquement sur votre ordinateur.

## Installation manuelle

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
# Configurer .env à partir de .env.example, sans écraser un .env existant.
.venv/bin/python manage.py migrate
.venv/bin/python manage.py import_catalogue
.venv/bin/python manage.py runserver 127.0.0.1:8000
```

Aperçu : http://127.0.0.1:8000/ — administration : http://127.0.0.1:8000/admin/.
Créer son accès administrateur avec `manage.py createsuperuser` si nécessaire.

## Catalogue

Les 16 articles initiaux, prix et images proviennent de la version locale initiale. Le catalogue a ensuite été enrichi des collections bébé et Femme du site public. Aucune image n’a été remplacée. `import_catalogue` est relançable sans écraser les changements faits en administration. Les anciennes URL locales de fiches redirigent vers les fiches canoniques. Les données de prix envoyées dans une URL ou un formulaire ne font jamais autorité.

Dans l’administration, renseigner les tailles réellement vendables (séparées par des virgules) et vérifier les prix, descriptions, catégories et disponibilités. Une taille vide laisse préparer le panier mais bloque son encaissement. La seule plage de tailles initialisée provient de la fiche softshell homme existante. Les articles sans genre explicite ont une catégorie provisoire à vérifier. Les photos secondaires existantes sont conservées dans les galeries.

## Activer Stripe

1. La propriétaire crée/valide son compte Stripe et ajoute son IBAN directement dans Stripe. Ne jamais ajouter d’IBAN ni de carte bancaire dans le code.
2. Commencer en environnement test. Définir `STRIPE_SECRET_KEY=sk_test_...`.
3. Créer un tarif de livraison Stripe en EUR correspondant aux conditions de la boutique, puis renseigner `STRIPE_SHIPPING_RATE=shr_...`. La version actuelle accepte les adresses françaises et un tarif fixe ; la gratuité à partir de 99 € n’est pas automatiquement appliquée.
4. Configurer le webhook vers `/paiement/webhook/` pour `checkout.session.completed` et `checkout.session.async_payment_succeeded`. Renseigner son secret `STRIPE_WEBHOOK_SECRET=whsec_...`. Pour les essais locaux : `stripe listen --forward-to localhost:8000/paiement/webhook/`.
5. Vérifier les tailles/disponibilités, prix TTC, frais/délais de livraison et retours. Compléter et faire valider les mentions légales, CGV, confidentialité et leur présentation/acceptation avant vente ; ces documents ne sont pas inventés dans cette refonte. `SHOP_READY` est une confirmation manuelle de ces préparatifs, pas une validation juridique automatique.
6. Définir `SHOP_READY=True` et `PAYMENTS_ENABLED=True` seulement pour les essais lorsque ces préparatifs sont terminés.
7. Tester via la page Stripe avec la carte test officielle `4242 4242 4242 4242`, une expiration future et un CVC de test. Vérifier la commande payée en administration, l’adresse et les frais. Tester annulation et refus. Aucun essai réel Stripe n’a été effectué par la refonte, faute de clés marchand.
8. Pour la production, remplacer les trois identifiants Stripe par leurs équivalents réels, utiliser `SITE_URL=https://...`, `DEBUG=False`, une clé Django secrète et `ALLOWED_HOSTS` corrects. Configurer le serveur HTTPS et les fichiers statiques (`collectstatic`). Ne pas utiliser le serveur de développement sur Internet.

Les commandes ne deviennent payées qu’après réception d’un webhook signé et vérification de la session auprès de Stripe (statut, devise, montant des articles, référence). Un simple retour sur la page de confirmation ne marque jamais une commande payée. Les répétitions du webhook ne créent pas une deuxième commande. La confirmation est accessible uniquement depuis la session cliente d’origine. Le panier est conservé lors d’une annulation ou erreur et les quantités achetées sont retirées après confirmation.

## Exploitation et limites

Les commandes, coordonnées client et adresses sont visibles dans l’administration. La préparation/expédition, les remboursements et la disponibilité par taille restent à gérer par la boutique ; il n’y a pas de réservation de stock automatique. Activer les reçus dans Stripe si souhaité : aucun e-mail de commande n’est envoyé directement par Django. Les taxes sont incluses dans les prix saisis, sans calcul fiscal automatique. La récupération automatique du mot de passe et l’historique de commandes client restent à prévoir si souhaités. Prévoir sauvegardes et surveillance sur le serveur de production ; la limitation des connexions est décrite ci-dessous.

Aucun déploiement ni changement du site public n’a été effectué. Les collections bébé et les 50 références Femme du site public sont importées ; les autres rubriques et les clients ne sont pas migrés. Prévoir une correspondance des anciennes URL publiques avant bascule du domaine.

## Vérifier

```sh
.venv/bin/python manage.py check
.venv/bin/python manage.py test connexion core paiement
.venv/bin/python manage.py makemigrations --check --dry-run
```

Documentation Stripe : https://docs.stripe.com/checkout/fulfillment

## Accessoires bébé, polaires bébé et offres spéciales

Les trois bannières de l’accueil ouvrent les collections locales correspondantes. Le relevé du 17 septembre 2026 comprend 10 accessoires bébé et 5 polaires bébé ; les 3 modèles de chaussettes existants sont partagés sans duplication, soit 28 articles avant l’import Femme. Les 12 nouveaux articles et leurs photos sont disponibles localement, sans appel au site d’origine lors du démarrage. Les tailles et disponibilités restent à confirmer avant encaissement.

La bannière originale « Offres spéciales » pointe vers une catégorie supprimée (404) ; la page Promotions ne propose aucune offre à la date du relevé. La rubrique locale affiche donc un état vide explicite. Dans l’administration d’un produit, le champ **Collections** permet de l’associer à « Offres spéciales » (ou à une collection bébé) tout en conservant sa catégorie principale. Aucune réduction fictive n’est appliquée.

`./start` et `import_catalogue` importent aussi ces collections via `import_collections`, sans écraser les prix, descriptions ni associations modifiés par la boutique. Les données source sont dans `core/collection_seed.json`.

## Sécurité des comptes (17 septembre 2026)

- Django 5.2.17 et dépendances mises à jour, versions directes et transitives figées dans `requirements.txt`. Analyse de l’environnement avec pip-audit : aucune vulnérabilité connue détectée au moment du contrôle. Ce résultat doit être renouvelé régulièrement.
- Clé secrète renouvelée avec un générateur cryptographique ; `.env` lisible/inscriptible uniquement par son propriétaire (0600), ignoré par Git. Les anciennes sessions doivent se reconnecter. Le site refuse les clés absentes, courtes ou les valeurs d’exemple. Une clé différente doit être générée pour la production.
- `./start` génère une clé privée pour une nouvelle installation sans `.env`. Il ne remplace jamais un fichier existant. Si vous copiez `.env.example`, remplacez sa clé d’exemple avant de lancer.
- Cinq échecs de mot de passe depuis une adresse IP entraînent un blocage temporaire de quinze minutes, partagé entre les deux URL de connexion et persistant dans la base. Les inscriptions sont limitées à cinq tentatives par IP et par fenêtre de trente minutes. Les codes OTP ont aussi un ralentissement après échec et ne peuvent pas être rejoués.
- L’administration exige une session vérifiée par double authentification, y compris si l’utilisateur est connecté depuis la boutique. Un administrateur sans appareil doit d’abord activer son authentificateur. Les clients sans double authentification conservent une connexion simple ; ils peuvent l’activer depuis « Mon compte ».
- Les protections utilisent l’IP de connexion réelle (`REMOTE_ADDR`), pas les en-têtes librement fournis par le navigateur. Lors du déploiement derrière un proxy, configurer explicitement les proxys de confiance et la transmission de l’IP ; sinon les visiteurs partageront le quota du proxy. Ne pas accepter aveuglément `X-Forwarded-For`.

### Première connexion administrateur

Aucun compte administrateur n’a été créé automatiquement. Le propriétaire choisit lui-même son mot de passe :

```sh
.venv/bin/python manage.py createsuperuser
```

Puis ouvrir http://127.0.0.1:8000/admin/, saisir ses identifiants, suivre l’assistant d’activation, scanner le QR code dans son application d’authentification et saisir le code temporaire. Après activation, générer et conserver les codes de secours dans un endroit privé. Ne pas partager le QR code, la clé d’authentification ou les codes de secours.

L’accès admin reste bloqué tant que cet appairage n’est pas terminé. Les appareils réels et les codes de secours de l’utilisateur ne sont pas créés par les tests. Un code de secours permet une connexion si le téléphone est perdu. Si téléphone et codes sont tous perdus, le propriétaire devra récupérer l’accès depuis le serveur après vérification de son identité ; ne pas désactiver globalement la protection.

En cas de blocage local par essais répétés, attendre quinze minutes. L’opérateur ayant accès au terminal peut utiliser `manage.py axes_reset_ip 127.0.0.1` pour débloquer uniquement cette IP.

### Base de données et Git

`db.sqlite3` a été retiré de l’index Git avec `git rm --cached` : les données locales restent intactes. La suppression du suivi est préparée dans l’index et sera enregistrée au prochain commit. Les bases, sauvegardes et secrets sont ignorés. Aucun commit ni envoi distant n’a été réalisé.

L’ancien commit `4f894d7` conserve une copie historique de la base ; les références distantes locales contiennent ce commit. La copie vérifiée ne contient aucun utilisateur ni aucune session, et `.env` n’apparaît pas dans l’historique consulté. Retirer un fichier du suivi n’efface jamais ses anciennes versions. Aucune réécriture de l’historique partagé n’a été faite.

### Relancer les contrôles

```sh
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pip_audit
.venv/bin/python -m pip check
.venv/bin/python manage.py test connexion core paiement
DEBUG=False .venv/bin/python manage.py check --deploy
```

Le contrôle de déploiement conserve l’avertissement HSTS : son activation dépend de la configuration HTTPS finale (hors de ces quatre corrections). Le paiement réel, les stocks, sauvegardes et alertes restent les étapes suivantes déjà identifiées.


## Collection Femme

Relevé du 17 septembre 2026 : les **50 références** de [la catégorie Femme](https://la-cavernedesmonts.fr/3-femmes) sont enregistrées dans `core/femmes_seed.json`, avec leurs adresses source, prix, descriptions, tailles proposées et **175 photos locales**. Les identifiants source permettent de conserver les références distinctes même lorsqu’elles ont le même titre.

L’import ajoute 44 fiches et enrichit 6 fiches initiales identifiées par leurs photos et coloris, en conservant leur identifiant, leur URL locale et leur image initiale. Les 4 autres anciennes fiches classées Femme sont conservées : **54 fiches dans la rubrique, 72 dans la boutique**. Les galeries source complètent les images locales.

`import_catalogue` appelle désormais `import_femmes`, également exécutable seul après `migrate`. La première association enrichit uniquement les valeurs issues du catalogue initial ; les modifications personnalisées sont conservées. Les imports suivants ne modifient pas les fiches déjà associées. Aucun accès réseau n’est nécessaire au démarrage.

Il s’agit d’un instantané, pas d’une synchronisation de stock. Les tailles source figurent dans la description ; le champ des tailles vendables reste vide pour les nouvelles fiches jusqu’à validation par la boutique. Les fichiers HTML source ne sont ni servis ni exécutés : seules les descriptions en texte et les photos sont intégrées.
