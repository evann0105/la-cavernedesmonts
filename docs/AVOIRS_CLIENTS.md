# Avoirs clients et bons personnels

Dans l’administration, ouvrir **Mon compte → Service client → Avoirs clients**
(`/gestion/avoirs-clients/`). L’accès exige le rôle de gestion des retours et la
vérification du compte, comme les autres actions du service client.

## Utilisation par la boutique

1. Créer un avoir ou un bon, rechercher un client par nom / e-mail exact, ou
   renseigner une adresse dont le destinataire n’a pas encore de compte.
2. Choisir le type : avoir commercial en euros, réduction en euros ou pourcentage.
   Exemple : **15 %**, minimum d’articles **70 €**.
3. Choisir éventuellement un début et une fin, à la minute, en heure de Paris.
   Le début est inclus, la fin exclue. Sans dates, le code est disponible tant
   qu’il reste actif et inutilisé.
4. Laisser « Informer le client par e-mail » coché pour préparer et envoyer la
   notification. La fiche permet de suivre l’envoi, le relancer, désactiver le
   code et corriger ses conditions avant sa réservation ou son utilisation.

Les codes sont générés automatiquement, uniques en base et jamais supprimés ou
réattribués implicitement. Deux clics sur un même formulaire de création ou
renvoi ne produisent pas deux codes / messages. Une correction explicite du
destinataire est historisée ; elle est refusée pendant un paiement ou après
utilisation. Le compte d’origine reste mémorisé même s’il est supprimé : un
nouveau compte avec la même adresse ne récupère pas automatiquement le code.

## Utilisation par le client

Le client retrouve ses avantages dans **Mon compte → Mes avoirs** (`/avoirs/`),
après confirmation de son adresse e-mail. Il copie le code puis le saisit dans
le récapitulatif de commande, avant le paiement. La remise et le montant des
articles après remise sont affichés avant de partir vers Stripe.

- Un seul code personnel par commande, utilisable une seule fois.
- Les conditions portent sur le montant effectif des articles, après les
  réductions du catalogue, hors livraison. Le seuil de livraison offerte reste
  calculé avant le code, comme indiqué dans le panier.
- Un avoir commercial s’utilise intégralement : le panier doit atteindre au
  moins son montant. Cela évite de perdre un reliquat. Un bon promotionnel en
  euros est plafonné au montant des articles, sans reliquat.
- Un panier ne consomme et ne réserve pas le code. Le départ vers le paiement
  réserve ensemble les articles et le code. Le paiement confirmé les consomme
  ensemble ; un événement répété ne les consomme pas une seconde fois.
- Une expiration vérifiée auprès de Stripe libère le code et le stock. Un
  simple délai, une fermeture du navigateur ou une erreur réseau ne suffisent
  pas. Retirer / changer le code annule d’abord le paiement précédent auprès de
  Stripe ; si le paiement a gagné la course, la commande payée est conservée.
- La ventilation exacte des remises Stripe est conservée par article pour les
  factures et retours. Les anciennes commandes et documents d’avoir ne changent
  pas. Aucun remboursement bancaire n’est remplacé par un code.

Ces avantages commerciaux sont distincts des documents comptables d’avoir
existants. Ils ne représentent pas un portefeuille de fonds déposés ni un
remboursement légal automatique.

## Installation et exploitation

Appliquer les migrations Django habituelles, puis relancer le serveur **et le
worker**. Aucun code n’est créé ou envoyé par la migration. Le worker existant
`reconcile_reservations --loop`, lancé par `./start`, traite aussi les
notifications d’avoirs en attente. En hébergement, exécuter régulièrement :

```
python manage.py reconcile_reservations
```

La commande ciblée `python manage.py process_customer_benefits` permet aussi de
reprendre uniquement les notifications d’avoirs. Les échecs restent en file ;
les reprises attendent au moins cinq minutes. Les anciens messages non envoyés
sont annulés si leur destinataire ou leurs conditions changent. Comme tout envoi
SMTP sans identifiant d’idempotence prestataire, une coupure après acceptation
par le serveur mais avant enregistrement local peut exceptionnellement créer
un doublon de message, sans créer ni consommer un second code.

Le mode e-mail de test existant capture les messages dans la boîte locale. En
production, utiliser le prestataire de messagerie configuré et les clés Stripe
prévues ; aucune clé ni adresse de vrai client n’est intégrée aux tests.

## Validation avant mise en service

Les tests fonctionnels couvrent création / édition / accès, dates et seuils,
arrondis, destinataires, notification, paiement interrompu, annulation,
réutilisation, expiration, total nul, cohérence des montants et export personnel.
`paiement.test_customer_benefits.ConcurrentCustomerBenefitTests` vérifie les
réservations concurrentes sur PostgreSQL ; il est explicitement ignoré sur
SQLite. Exécuter les tests sur la base PostgreSQL de test dédiée, puis un essai
Stripe en mode test et une réception e-mail réelle avant ouverture des ventes.
