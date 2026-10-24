# Personnaliser l’accueil

Dans **Mon compte → Boutique → Personnaliser l’accueil**, quatre entrées séparent les réglages :

1. **Bannière principale** : titre, descriptions, photos pour ordinateur et téléphone, texte du bouton et sa destination.
2. **Sections et contenus** : afficher ou masquer chaque bloc, puis modifier ses textes, photos et boutons dans son propre formulaire. La bannière principale reste présente. Les avis eux-mêmes ne sont pas modifiables.
3. **Produits mis en avant** : choisir jusqu’à quatre articles publiés avec une photo, dans leur ordre d’affichage. Vider les quatre choix rétablit la sélection automatique. Un article retiré de la vente cesse d’être affiché ; si aucun choix manuel ne reste utilisable, la sélection automatique reprend.
4. **Cartes Femme, Homme et Enfant** : choisir l’article qui représente chacune des trois collections.

Le bouton **Voir l’accueil** affiche les modifications déjà enregistrées. Enregistrer un bloc ne modifie pas les autres blocs. Les textes personnalisés sont saisis en français et ne sont pas traduits automatiquement.

Les boutons proposent les destinations du site et les coordonnées de la boutique. Les photos JPEG, PNG ou WebP sont vérifiées, orientées et optimisées automatiquement. **Revenir à la photo d’origine** rétablit le visuel initial. Sans photo spécifique au téléphone, la photo principale personnalisée est également affichée sur mobile.

Si deux onglets modifient l’accueil, le plus ancien formulaire demande de recharger la page avant d’enregistrer : il ne remplace pas silencieusement les changements les plus récents.

## Exploitation

- Les réglages et positions sont stockés dans les modèles de l’application `accueil`. La migration initiale doit être appliquée avant le démarrage de la version.
- Les nouvelles photos sont enregistrées dans `MEDIA_ROOT/homepage/`. Sauvegarder ce dossier avec les autres médias et la base de données. Les anciennes photos ne sont pas supprimées lors d’un remplacement pour préserver les sauvegardes et les retours en arrière.
- Une simple consultation ne crée aucun réglage. En l’absence de personnalisation, les textes, liens, photos responsives et sélection automatique antérieurs restent utilisés.
- La permission catalogue, la confirmation d’adresse du personnel et la protection CSRF sont nécessaires pour modifier l’accueil. Les pages privées ne sont pas indexables.
- La grande photo personnalisée est également utilisée dans l’aperçu de partage de la page d’accueil.
