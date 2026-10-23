# Préparation et expédition

Depuis **Mon compte → Service client → Préparation et expédition**, la boutique
retrouve les commandes payées à préparer, les commandes prêtes et les commandes
expédiées. La recherche utilise la référence de commande. Les simulations ont
leur propre onglet en développement et ne sont pas accessibles en production.

## Parcours quotidien

1. Ouvrir la commande dans **À préparer**. Contrôler le destinataire, l’adresse,
   les articles, leurs tailles, couleurs et quantités enregistrées lors de
   l’achat. Imprimer le bon de livraison à glisser dans le carton. Les liens
   bon de commande et facture restent disponibles ; le parcours ne déclenche
   pas une nouvelle facture.
2. Cocher les contrôles et **marquer la commande prête**. L’avis de préparation
   au client est facultatif. Une commande prête peut être remise en préparation
   si un contrôle doit être repris.
3. Créer l’étiquette chez Colissimo, puis déposer/remettre physiquement le colis.
   Recopier son numéro de suivi et confirmer la remise au transporteur avant
   de **confirmer l’expédition**. Le client reçoit un avis avec son suivi.

Les messages utilisent uniquement l’adresse e-mail enregistrée avec la commande.
L’adresse actuelle du compte client ne peut pas remplacer silencieusement ce
destinataire. Si le suivi est erroné, le corriger avec un motif interne : le
nouveau suivi apparaît au client et un avis de correction est préparé. Les
anciens avis encore en attente sont écartés ; les messages déjà envoyés restent
dans l’historique. Le bouton de renvoi ne duplique pas un avis déjà en attente.

Le client voit l’état et le suivi dans **Mes commandes**, le détail de la
commande et son lien privé. Les notes internes et les identités du personnel
n’apparaissent pas dans ce suivi client. La préparation et l’expédition ne
changent ni le paiement, ni les prix historiques, ni le stock déjà décompté à
l’achat. Les factures et conditions de vente enregistrées restent inchangées.

## Messages et suivi

Les avis passent par la file transactionnelle existante, traitée par
`manage.py reconcile_reservations --loop` ou `manage.py process_returns`.
La page distingue l’attente, un échec à réessayer, une capture dans la boîte
locale de test et la remise au service d’envoi. Un envoi accepté ne garantit
pas une arrivée dans la boîte de réception. Les essais actuels restent locaux
tant que la messagerie professionnelle n’est pas configurée. Une commande de
démonstration ne peut jamais utiliser SMTP : seule la capture locale privée
est autorisée pour ces commandes.

Le numéro Colissimo est contrôlé sur son format, pas sur son existence. Le lien
de suivi ouvre le site officiel La Poste ; le site de la boutique n’interroge
pas d’API et ne déduit pas qu’un colis est livré. Les commandes avec un dossier
de retour ouvert ou un remboursement confirmé demandent une vérification avant
préparation/expédition. Ce premier parcours traite la commande entière avec un
seul numéro de suivi ; il ne gère pas les expéditions partielles ou multi-colis.

## Colissimo et accès professionnel à venir

Le 23 octobre 2026, Colissimo / La Poste est confirmé par la boutique. Le
nouveau contrat et l’accès aux Web Services restent attendus. L’étiquette
affranchie, son code-barres et son numéro sont générés chez le transporteur ;
le bon de livraison imprimable du site ne les remplace pas.

La [documentation officielle Colissimo](https://www.applications.colissimo.entreprise.laposte.fr/doc-colissimo/redoc-sls/fr)
décrit une clé de connexion aux Web Services obtenue dans le profil Colissimo
Box. Il faudra l’accès autorisé au contrat d’Élodie et ses services activés,
puis confirmer les formats d’étiquettes, options, poids et règles du contrat.
La clé doit être installée côté serveur par un canal sécurisé, sans être
communiquée dans le chat, stockée dans le navigateur ou ajoutée au dépôt Git.
Aucun compte ni affranchissement payant n’est connecté par cette fonctionnalité.
