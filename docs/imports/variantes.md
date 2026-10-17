# Import des variantes et de l’inventaire

Dans **Mon compte → Administration → Stocks par taille et couleur → Importer / exporter les stocks**, téléchargez le modèle contenant le catalogue actuel. Il s’ouvre dans Excel ou LibreOffice ; enregistrez-le au format **CSV UTF-8, séparateur point-virgule**.

Ce module crée ou met à jour les variantes des **produits déjà présents**. Il ne crée pas de produits, ne change pas leurs prix et ne constitue pas encore l’import de la base de l’ancien site.

| Colonne | À renseigner |
| --- | --- |
| produit_id | Identifiant fourni dans l’export, obligatoire. Ne pas utiliser l’identifiant de l’ancien site sans correspondance vérifiée. |
| produit | Nom fourni pour se repérer ; il ne sert pas à identifier ou renommer le produit. |
| variante_id | Garder l’identifiant pour une variante existante. Vide pour une nouvelle variante. |
| revision | Garder la valeur exportée. Mettre 0 pour une nouvelle variante. |
| taille | Obligatoire, par exemple M ou Taille unique. |
| couleur | Obligatoire, par exemple Bleu ou Coloris unique. |
| reference | Référence interne facultative, 80 caractères maximum. |
| quantite | Stock physique total, réservations incluses. Entier de 0 à 1 000 000. Vide = inconnu, 0 = rupture. |

Une ligne par combinaison taille/couleur. Dupliquez une ligne produit pour créer plusieurs variantes, en laissant variante_id vide et revision à 0. Retirez les lignes des produits que vous ne souhaitez pas renseigner ; aucune suppression n’est effectuée par l’import. La quantité **remplace** le stock, elle ne s’ajoute pas à celui-ci. Faites donc un inventaire daté et coordonnez les ventes magasin pendant sa préparation.

Chargez le fichier, vérifiez l’aperçu puis confirmez. L’aperçu expire après 30 minutes. Le système recontrôle toutes les lignes au moment de confirmer. Une erreur bloque tout le fichier : doublon, produit inconnu, variante appartenant à un autre produit, quantité invalide, stock modifié depuis l’export ou quantité inférieure aux réservations en cours. Les changements sont journalisés avec le compte responsable. Les opérations sont atomiques et utilisent les mêmes verrous PostgreSQL que les réservations de paiement.

Ne modifiez pas les tailles/couleurs existantes dans le CSV : utilisez la page du produit. Les valeurs texte pouvant être interprétées comme des formules sont neutralisées dans l’export. Conservez les valeurs exportées pour les variantes existantes.

Maximum 1 000 lignes et 2 Mo par fichier. Pour un catalogue plus grand, répartissez les lignes dans plusieurs fichiers en conservant les en-têtes. Chaque fichier constitue un import distinct.

Le fichier `modele-variantes.csv` ci-joint fournit les en-têtes vierges. Le téléchargement depuis l’administration fournit les bons identifiants et les révisions actuelles : utilisez-le de préférence, et retéléchargez-le si des ventes ou corrections ont eu lieu.
