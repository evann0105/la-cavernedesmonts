# Catalogue : duplication, modifications groupées et rubriques

Les outils se trouvent dans **Mon compte → Catalogue**. Ils sont réservés aux gestionnaires du catalogue. Les enregistrements sont tracés dans le journal d’administration.

## Dupliquer une fiche

Dans **Gérer les produits**, cliquer **Dupliquer** sur l’article voulu. Une page récapitule ce qui sera repris. **Créer la copie en brouillon** ouvre le nouvel article dans l’éditeur.

La copie reprend les textes, les rubriques principales et complémentaires, les photos, les couleurs, les tailles et leurs prix habituels. Les photos sont copiées indépendamment. Son nom commence par « Copie de », elle possède sa propre adresse et reste invisible aux clients. Les stocks sont remis à zéro, les références internes vidées et la disponibilité laissée à confirmer. Les promotions, réservations, commandes et anciens historiques de prix ne sont pas copiés. La mise en avant est désactivée.

Renommer et relire la fiche, ajuster les photos et renseigner les stocks avant de la publier. Un double clic sur la confirmation retrouve la même copie.

## Modifier plusieurs articles ensemble

1. Filtrer le catalogue puis cocher les articles souhaités. **Cocher cette page** agit seulement sur les articles affichés ; **Sélectionner les résultats, toutes pages confondues** ajoute tous les articles correspondant aux filtres. Les doublons archivés sont exclus. La sélection reste conservée dans le même onglet, pour le même compte, lors des changements de page ou de filtre. Le compteur indique aussi les articles cochés hors de la page ; **Tout décocher** vide la sélection complète.
2. Cliquer **Modifier la sélection**, puis cocher les champs à changer. Les autres informations restent inchangées.
3. **Vérifier les changements** présente chaque produit avec les valeurs avant et après. **Ajuster mes changements** revient à la préparation ; **Appliquer** enregistre tout ensemble.

Les champs proposés sont la rubrique principale, la marque, le type d’article, la publication, la mise en avant, la disponibilité indicative et le prix général. Les rubriques complémentaires peuvent être ajoutées ou retirées sans remplacer toutes les affectations existantes.

Le nouveau prix général est commun aux articles sélectionnés. Les prix spécifiques de tailles/couleurs restent inchangés ; les déclinaisons sans prix spécifique suivent le nouveau prix général. Les promotions déjà programmées restent en place. Pour une réduction temporaire ou un pourcentage, utiliser **Soldes**.

La publication vérifie la description, la photo principale, la rubrique et le prix de chaque article. Une erreur ou un changement concurrent empêche toute l’opération : aucun lot partiellement modifié. La préparation signée expire après deux heures et accepte au maximum 500 articles (la liste affiche 20 articles par page). Aucun stock n’est modifié.

Après un enregistrement groupé réussi, les articles traités quittent la sélection. Une annulation, une erreur ou une préparation abandonnée conserve les choix. Les identifiants sont revérifiés sur le serveur : un article supprimé ou archivé entre-temps bloque le lot avec une explication.

## Retrouver sa position et les fiches à compléter

Les liens **Modifier**, **Dupliquer**, **Aperçu** et **Ajouter un produit** conservent la recherche, les filtres, le tri et la page. Après enregistrement ou retour à la liste, le défilement est rétabli dans le même onglet ; un repère sur la fiche sert de repli. Si les informations modifiées ne correspondent plus aux filtres, l’article quitte normalement les résultats.

Le filtre **À vérifier** propose :

- **Sans photo principale** : aucune photo principale importée ou statique ; une simple photo de galerie ne suffit pas.
- **Sans marque** : champ vide ou ne contenant que des espaces.
- **Stock à renseigner** : aucune déclinaison enregistrée ou au moins une quantité inconnue.
- **En rupture de stock** : toutes les quantités sont connues et aucune unité n’est disponible après déduction des réservations. Une quantité inconnue n’est jamais assimilée à zéro.

Ces critères se combinent à la recherche, à la rubrique et à la visibilité. Le compteur donne le nombre total de résultats. Le tri propose les nouveautés, le nom dans les deux sens et le prix affiché croissant ou décroissant, à partir du tarif le plus bas des tailles et couleurs (promotions comprises).

## Rubriques et menu

La page dédiée **Rubriques et menu** permet de créer et renommer les collections, choisir un libellé court pour le menu et les ranger avec **Monter / Descendre**. Les nouvelles rubriques sont placées à la fin. Le même ordre est utilisé dans les choix du catalogue.

**Afficher dans le menu / Masquer dans le menu** agit sur les menus ordinateur et mobile. Masquer une rubrique ne retire ni ses produits ni sa page publique. Au-delà de quatre rubriques, les suivantes sont proposées dans le volet **Collections** sur ordinateur ; toutes figurent dans le menu mobile.

Les adresses des rubriques restent stables après un changement de nom. Les choix initiaux reprennent Femme, Homme, Enfant et Petits bonheurs ; les imports suivants ne remplacent pas les réglages de la vendeuse. Le lien **Notre boutique** reste présent.

## Déploiement

Appliquer la migration `core.0019_category_management` avant de démarrer la nouvelle version. Les fichiers des photos restent dans le stockage médias existant et doivent faire partie des sauvegardes, indépendamment de Git. Le menu est lu depuis la base à chaque requête ; aucun redémarrage n’est nécessaire après une modification par la vendeuse.
