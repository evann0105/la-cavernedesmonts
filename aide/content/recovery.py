"""Content recovery deliberately excludes operational and financial ledgers."""
ARTICLES = (
    {'id': 'retrouver-version-produit', 'category': 'catalogue', 'permission': 'catalog',
     'title': 'Comment annuler une mauvaise modification de produit ?',
     'summary': 'Comparez une ancienne fiche avec la fiche actuelle avant de la rétablir.',
     'steps': (
         'Ouvrez Historique et récupération, puis retrouvez le produit par son nom. Son formulaire contient aussi un lien Historique.',
         'Choisissez la date et la version souhaitées. Relisez les textes, photos, rubriques et prix comparés avec la fiche actuelle.',
         'Ouvrez l’aperçu pour vérifier le résultat sur ordinateur et téléphone.',
         'Cliquez sur Vérifier la restauration, lisez les différences, puis cochez la confirmation et restaurez.',
         'La restauration est conservée comme une nouvelle version. Pour revenir sur ce choix, restaurez la version située juste avant elle.',
     ),
     'notes': (
         'Les quantités en stock, réservations, commandes, remboursements et soldes programmées gardent leurs valeurs actuelles. Les prix habituels par taille et couleur peuvent être rétablis.',
         'Les déclinaisons ajoutées depuis l’ancienne version sont conservées. Une déclinaison supprimée, une rubrique archivée ou un paiement en cours peut empêcher une restauration complète ; le motif est affiché.',
         'L’historique commence à sa mise en place. Il ne peut pas reconstituer une version plus ancienne qui n’avait jamais été conservée.',
     ),
     'actions': ({'label': 'Historique et récupération', 'view': 'historique:index', 'permission': 'catalog'},)},
    {'id': 'retrouver-version-accueil', 'category': 'accueil', 'permission': 'catalog',
     'title': 'Comment retrouver une ancienne version de l’accueil ?',
     'summary': 'Les textes, photos, deux diaporamas et choix de produits sont conservés ensemble.',
     'steps': (
         'Dans Personnaliser l’accueil, ouvrez son historique, puis choisissez une date.',
         'Comparez les sections affichées, les textes et boutons, les photos et les produits choisis.',
         'Ouvrez l’aperçu privé et vérifiez les formats ordinateur et téléphone.',
         'Vérifiez la restauration et confirmez pour appliquer cette version à l’accueil.',
         'Si le résultat ne vous convient pas, revenez dans l’historique et restaurez l’état précédent.',
     ),
     'notes': (
         'Restaurer l’accueil rétablit ses choix de produits. Les prix, stocks et fiches de ces produits restent ceux du catalogue actuel.',
         'Si un produit choisi a été supprimé ou retiré de la vente, la restauration est bloquée avec une explication, sans remplacer ce produit silencieusement.',
         'Un autre onglet modifié depuis l’aperçu ne peut pas écraser la nouvelle version sans nouvelle vérification.',
     ),
     'actions': ({'label': 'Historique de l’accueil', 'view': 'historique:homepage', 'permission': 'catalog'},)},
    {'id': 'restauration-bloquee', 'category': 'catalogue', 'permission': 'catalog',
     'title': 'Pourquoi une restauration est-elle bloquée ?',
     'summary': 'La vérification évite d’écraser un autre travail ou de recréer des articles vendus.',
     'steps': (
         'Lisez le motif affiché : contenu modifié dans un autre onglet, ancienne photo manquante, rubrique archivée ou déclinaison devenue incompatible.',
         'Si le contenu a changé, rechargez la comparaison et vérifiez à nouveau avant de confirmer.',
         'Si une rubrique, une photo ou une déclinaison manque, choisissez une autre version ou corrigez uniquement les champs utiles dans la fiche actuelle.',
         'Une confirmation expire après trente minutes. Vérifiez de nouveau les différences pour obtenir une confirmation à jour.',
     ),
     'notes': ('Les aperçus ne changent rien sur la boutique. Une restauration refusée ne modifie aucune fiche.',),
     'actions': ({'label': 'Revenir à l’historique', 'view': 'historique:index', 'permission': 'catalog'},)},
)

PAGE_HELP = {
    'historique:index': ('retrouver-version-produit', 'retrouver-version-accueil'),
    'historique:product': ('retrouver-version-produit', 'restauration-bloquee'),
    'historique:homepage': ('retrouver-version-accueil', 'restauration-bloquee'),
    'historique:detail': ('retrouver-version-produit', 'retrouver-version-accueil', 'restauration-bloquee'),
    'historique:preview': ('retrouver-version-produit', 'retrouver-version-accueil'),
    'historique:restore': ('restauration-bloquee', 'retrouver-version-produit', 'retrouver-version-accueil'),
}

NON_PAGE_ROUTES = {
    'historique:frame': 'Aperçu privé isolé d’une ancienne version, intégré dans l’écran de prévisualisation guidé.',
}
