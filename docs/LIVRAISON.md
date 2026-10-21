# Livraison

Depuis **Mon compte → Boutique → Livraison** (`/gestion/livraison/`), les réglages
sont séparés en trois parties : règles générales, transporteur et zones.

## Préparer les réglages

1. Dans **Règles de livraison**, renseigner les délais minimum et maximum de
   préparation en **jours ouvrés**. Activer ou désactiver la livraison offerte
   et choisir son seuil, initialement **99 € d’articles avant code client**.
   Zéro signifie sans minimum. Une précision publique peut compléter ces règles.
2. Dans **Transporteur**, indiquer son nom, le nom du service et, éventuellement,
   son site HTTPS. Tant qu’il n’est pas choisi, laisser le nom vide : le réglage
   reste à compléter. Aucun tarif ni délai n’est inventé.
3. Ajouter une **zone** pour chaque groupe de pays partageant les mêmes frais et
   délais d’acheminement. Sélectionner les pays, saisir le tarif en euros et les
   délais minimum et maximum. Cocher l’application du seuil gratuit seulement
   pour les zones concernées, puis activer la zone lorsqu’elle est complète.

Un pays ne peut appartenir qu’à **une seule zone**, même désactivée. Pour le
changer de zone, le retirer de la première avant de l’ajouter à la suivante.
Une zone peut rester en brouillon, avec tarif ou délais vides ; un tarif de
**0 € signifie gratuit**, tandis qu’un tarif vide signifie **à compléter**.
Les pays sans zone active et complète ne sont pas proposés aux clients.

Les délais annoncés additionnent préparation et acheminement : par exemple,
1 à 2 jours ouvrés de préparation et 2 à 4 d’acheminement donnent 3 à 6 jours
ouvrés au total. Il s’agit de fourchettes, sans calendrier de dates de livraison
ni connexion aux prévisions d’un transporteur.

## Ce que voit le client

Le client choisit son pays avant le paiement. Le site calcule les frais selon
sa zone et affiche le montant avant Stripe, qui limite ensuite la destination
au pays choisi. Le seuil gratuit utilise les prix des articles en vigueur,
après leurs éventuelles réductions de catalogue, mais **avant le code d’avoir
ou de réduction personnel**. Le code ne réduit pas les frais de livraison.
Une zone exclue de l’offre gratuite garde son tarif même au-dessus du seuil.

Les règles publiées sont reprises dans la page **Livraison et délais**
(`/livraison/`) et les conditions de vente. Le transporteur, les délais et les
zones doivent être renseignés avant que ce module soit prêt ; cela n’active pas
à lui seul les paiements ni les autres validations d’ouverture des ventes.

Tant qu’aucun réglage de livraison n’a été enregistré, le fonctionnement
antérieur est conservé : `FREE_SHIPPING_THRESHOLD_EUR` et `STRIPE_SHIPPING_RATE`
restent utilisés. Le premier enregistrement dans ce nouvel espace remplace
ce fonctionnement pour les nouvelles commandes. Compléter alors le
transporteur, la préparation et au moins une zone avant de livrer en ligne.

## Modifications et limites

Les changements s’appliquent aux nouveaux devis. Une tentative de paiement déjà
préparée conserve son tarif et ses conditions enregistrés ; un changement de
destination nécessite la vérification de cette tentative avant son remplacement.
Les anciennes commandes, factures, confirmations et montants remboursables ne
sont pas recalculés depuis les nouveaux tarifs. Les informations de livraison
des e-mails déjà préparés ou envoyés ne sont pas réécrites.

Ce module gère **un tarif standard par pays**. Il ne distingue pas les codes
postaux, la Corse ou les exceptions territoriales au sein d’un même pays : ne
pas activer une destination si le tarif saisi ne couvre pas les adresses qui
peuvent y être renseignées. Il ne calcule pas le prix selon le poids, ne propose
pas de point relais ou de service express et ne crée pas d’étiquette. Le site du
transporteur s’ouvre manuellement ; aucun compte transporteur, API ou suivi
aller automatique n’est connecté. Le bon de livraison imprimable reste un
récapitulatif du contenu du colis, sans affranchissement.

Les noms français de pays proviennent de [Unicode CLDR](https://github.com/unicode-org/cldr-json).
La notice d’attribution et la licence sont conservées dans
[licenses/UNICODE.txt](licenses/UNICODE.txt).
