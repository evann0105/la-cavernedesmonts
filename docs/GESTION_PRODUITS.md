# Gérer les produits

## Accès

Connectez-vous puis choisissez **Gérer les produits** (dans le menu sur mobile), ou ouvrez `/gestion/produits/`.
Le compte local **evannTest** est déjà superadministrateur. Il dispose donc de l’accès, après vérification de sa double authentification.

Pour la gérante, utiliser son propre compte. Depuis l’administration Django, ouvrir son utilisateur, activer **Statut équipe** et lui attribuer le groupe **Gestion du catalogue**. Il n’est pas nécessaire de lui donner le statut superutilisateur. Elle configure ensuite sa double authentification à la connexion. Le groupe donne uniquement accès à cet espace catalogue, pas à la gestion des comptes, des paiements ni des réglages du site.

## Ajouter ou modifier

- Rechercher ou filtrer un produit, puis cliquer sur **Modifier**, ou sur **Ajouter un produit**.
- Renseigner le nom, le prix TTC, la description et les tailles réellement disponibles, séparées par des virgules. Pour un accessoire : `Taille unique`.
- Choisir une rubrique principale et, si nécessaire, cocher des rubriques supplémentaires. Exemple : Enfants + Polaires bébé. Les catégories existantes couvrent Femme, Homme, Enfant, accessoires, bébé et offres spéciales.
- Ajouter une photo principale et des photos complémentaires : JPEG, PNG ou WebP, 10 Mo et 24 millions de pixels maximum par photo. Elles sont réencodées en WebP, orientées correctement, débarrassées des métadonnées et limitées à 2400 pixels pour le site.
- Jusqu’à 12 nouvelles photos par enregistrement, 50 photos dans la galerie. Les photos ajoutées peuvent être remplacées, décrites, retirées ou réordonnées avec le champ **Ordre d’affichage** (le plus petit numéro vient en premier).
- Pour remplacer la galerie d’un produit importé, décocher **Conserver la galerie de photos d’origine**. Les fichiers d’origine restent conservés et peuvent être réactivés. La photo principale d’origine peut être retirée séparément après ajout d’une nouvelle photo.
- Enregistrer puis ouvrir **Voir l’aperçu enregistré**. Les brouillons ont un aperçu privé accessible seulement aux gestionnaires vérifiés.
- Cocher **Visible dans la boutique** pour publier. Une description, une rubrique, un prix positif et une photo principale sont requis. Décocher cette case pour retirer temporairement l’article ; il reste dans l’espace de gestion, sans être achetable. Il n’y a pas de suppression définitive depuis cet espace.
- Cocher **Mettre en avant sur l’accueil** pour la sélection de la page d’accueil : les quatre premiers articles mis en avant y apparaissent. Décocher ceux à remplacer.

Le prix et les rubriques sont ceux utilisés par le catalogue, le panier et le paiement. Renommer un produit conserve son adresse. Les importations au démarrage préservent les modifications des fiches existantes. Les enregistrements sont tracés dans le journal d’administration.

## Limites et mise en production

Ce module gère le catalogue et les tailles disponibles, pas un stock quantitatif par taille ni des réservations de stock. Les paiements restent soumis aux conditions de configuration déjà présentes.
Les photos téléversées sont stockées dans `media/`, qui est volontairement exclu de Git. En production, prévoir un stockage persistant, son service HTTP et des sauvegardes de `media/` et de la base de données. Le serveur local les sert déjà. Les migrations s’appliquent via `./start`.

Références techniques : [sécurité des fichiers Django](https://docs.djangoproject.com/en/5.2/topics/security/#user-uploaded-content) et [authentification OTP](https://django-otp-official.readthedocs.io/en/latest/auth.html).

## Choisir les trois cartes « Votre envie du moment »

Depuis **Gérer les produits**, cliquez sur **Choisir les produits de l’accueil** (`/gestion/accueil/`). Sélectionnez un produit pour Femme, Homme et Enfant. Chaque liste contient les produits publiés, avec photo, rattachés à cette rubrique principale ou complémentaire. L’aperçu évolue immédiatement ; **Enregistrer les trois cartes** applique le choix sur l’accueil.

Les cartes conservent leurs textes et leurs liens vers les collections. Leur photo est la photo principale actuelle de la fiche choisie. Si un produit sélectionné devient inéligible, le premier produit éligible par nom le remplace automatiquement ; en l’absence de produit, la carte reste un lien vers la collection sans photo. Le choix automatique peut aussi être sélectionné volontairement. Les choix sont conservés en base et ne sont pas remplacés par les imports.

Ce réglage est indépendant de la case **Afficher dans « Les complices des beaux jours »**, qui concerne les quatre articles présentés plus bas sur l’accueil.

## Langues de la boutique

Dans **Mon compte**, un sélecteur propose Français, English, Deutsch, Italiano et Español. Validez avec **OK**. Ce réglage est disponible aux clients et aux administrateurs, ainsi qu’avant connexion depuis la page Mon compte. Le choix est conservé pendant un an dans un cookie de préférence ; il reste actif lors des visites et des changements de page. Les filtres et le panier sont conservés. Sans choix explicite, la langue du navigateur est utilisée si elle est prise en charge, sinon le français.

Les textes de l’accueil, de navigation, des collections, du panier, du contact et de la commande sont traduits. Le paiement Stripe utilise aussi la langue sélectionnée. L’espace de gestion conserve ses libellés français.

Dans **Modifier un produit → Traductions de la fiche produit**, renseigner le nom et la description pour chacune des quatre autres langues. Le français reste la source et les champs vides conservent le texte français. Une description non traduite est explicitement signalée sur la fiche. Les 110 fiches importées n’ont pas été traduites automatiquement : leurs textes commerciaux doivent être renseignés et validés par la boutique. La recherche retrouve aussi les noms et descriptions traduits.

Les traductions d’interface sont dans `locale/<langue>/LC_MESSAGES/django.po`. Après modification, exécuter `python manage.py compilemessages`. Les fichiers compilés `.mo` sont versionnés pour que le site démarre sans installation de gettext sur l’hébergement. Redémarrer le serveur après une mise à jour de ces fichiers.

## Zone d’avis en préparation

Une maquette discrète apparaît uniquement sur l’accueil pour un gestionnaire connecté avec une double authentification validée. Elle porte la mention **Exemples fictifs, non publiés aux visiteurs**. Les exemples ne sont associés à aucun client, aucune note ni aucune source réelle. Les visiteurs et comptes clients ne voient pas cette zone. Avant toute publication, les remplacer par des témoignages authentiques avec leur source et l’autorisation nécessaire.
