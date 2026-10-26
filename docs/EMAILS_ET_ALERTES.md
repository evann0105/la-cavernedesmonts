# E-mails et alertes

**Mon compte → Administration → Service client → E-mails et alertes** ouvre le suivi. Les stocks à surveiller sont également accessibles depuis le catalogue et le tableau de bord.

## Trois pages séparées

- **Suivi des e-mails** : commandes, expéditions, retours, avoirs et alertes de la boutique, avec les onglets À vérifier, En attente, Traités et Tous. Chaque message indique son destinataire, son état et ses tentatives. Ouvrir le suivi, puis **Relancer ce message**, ne traite que cet envoi. Les messages déjà traités, remplacés, annulés ou devenus obsolètes ne sont pas réexpédiés.
- **Stocks à surveiller** : quantités disponibles, par taille et couleur, après déduction des réservations. Le seuil par défaut est 3. Les quantités inconnues et les produits sans aucune déclinaison de stock figurent à part dans **À compter**, avec un accès à leur saisie. Une page dédiée permet de choisir un seuil spécifique ou de désactiver une déclinaison.
- **Réglages des alertes** : adresse de la vendeuse, notification des nouvelles commandes payées, seuil général et activation des e-mails de stock. Aucun mot de passe de messagerie ne se saisit ici.

Les alertes e-mail de la vendeuse sont désactivées tant qu’elle n’a pas choisi leur destinataire et leur activation. La surveillance visible des stocks reste disponible. Le seuil 0 avertit seulement à la rupture. Les changements sont enregistrés avec leur auteur et une protection contre les modifications concurrentes.

## Déclenchement

Une nouvelle commande déclenche une seule alerte après confirmation autoritative du paiement. Les simulations et anciennes ventes ne génèrent pas de rattrapage. La vendeuse reçoit la référence, le contenu acheté et un lien vers la préparation de la commande, accessible uniquement après connexion.

Le contrôle automatique des stocks utilise `reconcile_reservations`, déjà lancé chaque minute par le serveur local. En production, maintenir ce worker ou planifier cette commande chaque minute. La liste à l’écran lit toujours les quantités actuelles, sans envoyer de messages à l’ouverture.

Le contrôle compare les quantités disponibles au seuil. Il ne répète pas le même avertissement à chaque minute ; un réapprovisionnement au-dessus du seuil permet une nouvelle alerte lors d’une prochaine baisse. Les alertes en attente sont revérifiées avant l’envoi : un stock rétabli, un article retiré ou un destinataire changé ne doit pas recevoir un ancien avertissement.

## Envoi et limites

Le système réutilise le service d’e-mail existant. En mode test, les messages sont conservés localement et clairement étiquetés. Un message traité en mode test n’est pas renvoyé automatiquement quand le vrai service est activé.

**Transmis au service d’envoi** signifie que le service a accepté le message, sans garantir l’arrivée en boîte de réception ni la lecture. Les anciens messages dont le mode n’avait pas été enregistré sont indiqués comme tels. Les erreurs affichées n’exposent pas les réponses techniques du fournisseur ou des secrets.

Pour certains anciens messages clients dont le destinataire n’avait pas été conservé, la vendeuse doit vérifier puis confirmer l’adresse affichée avant une relance. Une modification de l’adresse ou du message invalide cette confirmation.

Les mots de passe et confirmations d’adresse utilisent leurs écrans habituels pour demander un nouveau lien sécurisé ; les anciens liens ne sont pas relancés depuis ce suivi.

Les réglages généraux nécessitent les droits de gestion du catalogue et du service client. Les e-mails clients restent réservés au service client ; les seuils de stock au catalogue. Destinataires, contenu des alertes et historique interne sont chiffrés et les pages ne sont pas publiques.

## Installation

Appliquer la migration `alertes/0001_initial`, rétablir les droits habituels du rôle PostgreSQL de l’application et redémarrer le worker existant après mise à jour du code. Ne pas démarrer un deuxième worker pour le même site. Les contrôles ciblés sont `alertes`, `courriels.tests_monitor`, puis les suites de paiement, avoirs, expéditions et retours.
