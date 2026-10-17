# Stock manuel — 17 octobre 2026

Accès : Mon compte → Administration → Stock. Réservé aux personnes actives de gestion possédant la permission catalogue et l’accès e-mail requis. Les clients ne voient pas ces outils.

1. Rechercher un produit dans l’inventaire.
2. Choisir la taille/couleur, puis « Ajouter / retirer ».
3. Choisir l’action, saisir le nombre d’unités et éventuellement le motif.
4. Enregistrer ; la quantité avant/après est confirmée et l’ajustement apparaît dans l’historique du produit.

- **Ajouter** : réassort, par exemple 8 + 3 = 11.
- **Retirer** : vente en boutique, casse ou sortie manuelle, par exemple 8 − 2 = 6.
- **Corriger le comptage** : remplace la quantité physique totale par la quantité réellement comptée. Cette quantité inclut les réservations.
- Un stock inconnu doit d’abord être compté ; il ne devient pas zéro par défaut. Zéro renseigné signifie une rupture.

Les écrans distinguent le stock physique, les unités réservées en ligne et les unités disponibles. La quantité physique ne peut jamais devenir négative ou inférieure aux réservations. Ajouter/retirer exige un entier strictement positif ; un comptage peut être zéro. Limite technique : 1 000 000 d’unités par variante.

La transaction verrouille produit puis variante. Toute modification concurrente de stock, réservation ou paiement change la révision : une page ancienne ou une double validation ne peut pas appliquer un second mouvement. Recharger puis vérifier avant de recommencer. La variante n’est pas renommée par un ajustement.

Les modifications sont tracées dans le journal de gestion existant : auteur, date, opération, quantité avant/après, taille/couleur, réservations et motif. L’écran affiche les 15 dernières saisies de gestion du produit ; commandes et retours ont leur historique propre. Les corrections de libellé ont également un historique dédié, ajouté par la migration stocks.0003.

## Corriger une taille ou une couleur saisie par erreur

Depuis la variante, « Corriger taille / couleur » ouvre le formulaire prérempli. Une variante peut être corrigée sur la même ligne, même après une vente : identifiant, quantité et référence restent conservés si ces valeurs ne sont pas modifiées. Le journal garde les anciens/nouveaux libellés. Vérifier également la photo de couleur dans la fiche produit ; les photos partagées avec d’autres tailles ne sont pas renommées automatiquement.

Les commandes, factures émises, bons imprimables et retours conservent leurs propres libellés d’achat. Une correction intentionnelle enregistre l’ancien et le nouveau libellé sans réécrire ces documents. Les anciens retours peuvent ainsi être remis sur la même variante corrigée ; la gestion affiche séparément le libellé actuel du stock. Plusieurs corrections successives restent reconnues, mais un renommage hors de cette procédure ou un changement de produit bloque toujours la remise en stock. Une réservation encore active bloque temporairement la correction : attendre le paiement confirmé ou sa libération confirmée. Les contrôles sont réexécutés sous verrou après validation du formulaire. Les anciens paniers ne sont jamais renommés silencieusement : une sélection devenue obsolète doit être choisie de nouveau avant de payer. L’import CSV conserve son propre refus des changements de taille/couleur existants.

## Essai d’achat et de rupture

Le scénario isolé utilise le panier et le paiement du site avec un prestataire simulé et des notifications signées de test : 5 unités → achat de 1 → 4 unités → achat des 4 restantes → 0. La réception répétée du même événement ne double pas la décrémentation. À zéro, sélection et bouton de la fiche sont désactivés, l’ajout panier est refusé et un panier falsifié ne peut pas ouvrir une nouvelle session de paiement. Une dernière unité déjà réservée est également inaccessible au second client. Aucun achat bancaire ni stock réel n’est utilisé par ces tests.

**Éviter les doubles mouvements** : les ventes web payées sont déjà décomptées automatiquement ; les quantités remises en stock à la clôture d’un retour sont déjà ajoutées. Les ventes en magasin restent manuelles, sans connexion à une caisse.

Validation : 42 tests du stock, puis 347 tests du projet réussis, notamment achat jusqu’à rupture, confirmations de paiement répétées, concurrence sur le dernier article, correction avant/après achat, commandes et factures inchangées, retours après corrections successives, récapitulatif invalidé si le libellé change, double validation, stock inconnu, bornes, réservations, droits, CSRF et journalisation atomique. Aucun stock réel n’a été modifié pour ces essais. Contrôle visuel avec des données fictives ; l’accès réel reste soumis à connexion.
