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
