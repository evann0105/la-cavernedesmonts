# Historique et récupération

L’espace vendeuse propose `/gestion/historique/` et des raccourcis dans les fiches produit et l’éditeur de l’accueil. Le catalogue et les pages publiées conservent également leurs parcours antérieurs de récupération, accessibles depuis cet écran.

## Versions conservées

- Produit : textes, marque et informations vérifiées, traductions, notes privées, prix habituel général et par déclinaison, publication, classement, tailles annoncées, photos principale, galerie et couleurs. La galerie statique importée est figée.
- Accueil : textes et destinations des boutons, visibilité de tous les blocs existants, sept photos, deux diaporamas avec leurs cadrages et réglages, ordre des quatre produits mis en avant et trois cartes de collections, y compris leurs modes automatiques.
- Chaque mutation éditoriale prise en charge conserve l’état précédent et l’état final dans la même transaction. Les imports, modifications groupées, corrections de tailles/couleurs et administration produit passent aussi par ces captures. Une quantité seule ne crée pas une version de contenu.

La commande `python manage.py conserver_versions_initiales` conserve l’état actuel avant utilisation. Elle est réexécutable et ne prétend pas récupérer des états antérieurs jamais enregistrés. Elle doit être lancée après les migrations sur chaque environnement ; les consultations GET ne créent jamais d’historique.

## Restauration

La comparaison et l’aperçu sont privés, réservés à la gestion du catalogue, sans mise en cache ni indexation. Le rendu utilise les photos conservées. L’aperçu produit montre les prix habituels de la version, avec les stocks actuels ; les soldes restent gérées séparément. Les coordonnées et autres éléments communs au site restent actuels.

Une confirmation explicite et signée est liée au vendeur, à la version et à l’empreinte courante. Elle expire après trente minutes. Au moment du POST, le service reprend les verrous et revalide les références. Un autre onglet ayant changé le contenu impose une nouvelle comparaison. Les doubles soumissions d’une même confirmation renvoient le même résultat sans seconde restauration.

Une restauration crée une nouvelle version avec un lien vers l’état rétabli. L’état qui vient d’être remplacé reste récupérable ; restaurer une restauration est donc possible. L’identité du produit et son URL ne sont pas modifiées.

## Données opérationnelles protégées

Les quantités, réservations, commandes, paiements, remboursements, factures, soldes et anciennes observations de prix ne sont jamais rembobinés. Les changements de prix ajoutent une observation datée du jour. Les nouvelles déclinaisons postérieures à une version restent présentes avec leurs stocks et prix actuels. Une déclinaison ancienne disparue, un conflit de combinaison, un changement d’identité pendant un paiement, une rubrique archivée ou un produit d’accueil devenu indisponible bloque la restauration complète avec un motif lisible.

## Conservation et sauvegardes

Les snapshots sont chiffrés avec `EncryptedJSONField` et la clé applicative existante. La clé de chiffrement doit être sauvegardée séparément de la base ; sans elle, les versions et les autres données chiffrées du site ne peuvent pas être lues.

Les photos sont copiées dans `MEDIA_ROOT/historique-assets/` sous une empreinte SHA-256 et réutilisées entre versions lorsque leur contenu est identique. Aucun téléchargement d’URL externe n’a lieu. Avant une restauration, le contenu du fichier d’origine ou de sa copie est vérifié. Une ancienne photo déjà manquante à la mise en place est signalée ; aucune image de remplacement n’est inventée. Aucune purge automatique des versions ou copies photo n’est installée.

Une sauvegarde complète doit donc conserver ensemble la base, l’ensemble de `MEDIA_ROOT` (y compris `historique-assets`) et les clés séparées. L’historique d’édition complète une sauvegarde ; il ne remplace pas une sauvegarde après panne ou suppression de base.
