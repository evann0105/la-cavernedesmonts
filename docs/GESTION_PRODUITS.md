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

### Protection des données locales

Le lancement `./start` conserve désormais la base dans le dossier voisin `la-cavernedesmonts-data/db.sqlite3`, hors du dépôt Git. Si une ancienne base existe encore dans le projet, elle est copiée une seule fois, sans remplacer une base externe existante. Une sauvegarde SQLite cohérente est créée dans `la-cavernedesmonts-data/backups/` avant les migrations et l’import à chaque démarrage. Les sauvegardes ne sont pas supprimées automatiquement.

Sauvegardez aussi ce dossier sur un support distinct : cette protection évite les pertes liées aux branches Git, mais ne remplace pas une sauvegarde contre une panne de disque. Les photos ajoutées sont dans `media/` et la configuration privée dans `.env` ; conservez-les également. En production, le chemin existant est conservé ; `DATABASE_PATH` permet de le définir explicitement.

### Pages de découverte et espace client

Le pied de page regroupe les collections, les informations et les liens « Mon compte ». Les nouveautés présentent les 24 derniers ajouts au catalogue. La page Promotions reprend les produits publiés rattachés à « Offres spéciales » ; aucun pourcentage ni ancien prix n’est inventé. Les meilleures ventes sont classées selon les quantités des commandes réellement payées sur le nouveau site.

Les nouvelles commandes passées en étant connecté sont rattachées au compte côté serveur. Aucun historique n’est attribué à partir d’une simple correspondance d’adresse e-mail. Les commandes anciennes ou invitées nécessitent un traitement séparé, et les anciennes données du site PrestaShop ne sont pas importées par cette fonctionnalité.

Le client peut ajouter, modifier et supprimer ses adresses, modifier ses nom/prénom/e-mail après confirmation de son mot de passe, retrouver ses commandes et consulter ses avoirs et bons. Le carnet d’adresses ne préremplit pas Stripe : le client confirme l’adresse de livraison sur la page de paiement. Les pages privées sont protégées contre la consultation par un autre client et ne sont pas mises en cache. La double authentification reste obligatoire pour l’administration.

Dans l’administration Django sécurisée, la rubrique Espace permet de consigner un **avoir déjà émis**, lié à une commande payée et à son propriétaire. Cette saisie ne déclenche aucun remboursement et ne constitue pas à elle seule une facture d’avoir comptable. Les remboursements et documents comptables doivent être traités par la gérante dans ses outils habituels.

Pour les **bons de réduction**, créer et configurer d’abord le code promotionnel dans Stripe (montant, durée, restrictions, limite d’utilisation et client si nécessaire), puis renseigner sa copie dans Espace > Vouchers avec le bénéficiaire et ses conditions. Le champ de saisie des codes est activé dans Stripe Checkout ; Stripe vérifie leur éligibilité et leurs limites. Le site ne crée pas de coupon Stripe et n’en synchronise pas la consommation : désactiver également la fiche locale lorsqu’un code n’est plus disponible.

### Conditions existantes à actualiser avant mise en vente

Source lue le 18 septembre 2026 : https://la-cavernedesmonts.fr/content/3-conditions-utilisation. Le texte français est repris dans une page avec sommaire. Il mentionne notamment CIC, le paiement par chèque, des e-mails automatiques et des modalités qui ne correspondent pas toutes au nouveau parcours. Ce n’est pas une validation juridique ni une nouvelle rédaction des CGV. Faire valider une version adaptée avant de passer SHOP_READY et PAYMENTS_ENABLED à True. Les traductions concernent l’interface ; le texte contractuel reste identifié comme la version originale française.

Adresse et horaires repris de https://la-cavernedesmonts.fr/content/4-a-propos : 225 rue des Monts-Jura, Résidence Les Gentianes, 01410 Lélex ; mercredi–samedi 9h30–12h et 14h30–18h30, dimanche 9h30–12h, ouverture quotidienne annoncée en haute saison. Le site invite à confirmer par téléphone avant la visite.
