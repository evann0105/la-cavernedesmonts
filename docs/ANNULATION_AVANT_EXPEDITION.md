# Annulation avant expédition

Depuis **Préparer et expédier**, ouvrir une commande payée qui n’a pas été remise au transporteur, puis **Annuler avant expédition**. Le récapitulatif présente les montants réellement payés après remise, livraison comprise, et toutes les quantités. Le motif est communiqué au client. La confirmation bloque définitivement la préparation de cette commande.

Le remboursement complet utilise le moyen de paiement initial. Les articles reviennent au stock une seule fois, après confirmation du remboursement. En cas d’attente ou d’erreur, le dossier reste dans l’onglet **Annulées**. **Vérifier le remboursement** rapproche la même tentative ; aucun deuxième remboursement indépendant n’est créé. **Réessayer la remise en stock** est une opération distincte disponible si le remboursement a réussi mais que le stock demande une correction.

Le client consulte le motif, l’annulation et l’état réel du remboursement depuis sa commande. Deux notifications sont préparées : annulation enregistrée, puis remboursement confirmé. Elles utilisent les réglages et la boîte de test habituels. Une simulation n’envoie aucun argent et ne change pas le stock réel.

## Historique et protections

- Le paiement d’origine et sa facture restent conservés. Le statut financier `paid` est inchangé ; le statut d’expédition devient `cancelled`.
- Les remboursements utilisent le registre existant `ReturnRequest`, avec le type `cancellation`. Ils restent inclus dans les exports comptables, sans apparaître comme un retour physique.
- Les réservations vendues restent `sold`, et les codes consommés restent utilisés. Une nouvelle notification de paiement ne peut donc pas débiter de nouveau le stock ou libérer un code.
- Le récapitulatif signé expire après 30 minutes. Une modification de la commande ou du stock exige une nouvelle lecture avant confirmation.
- Annulation, expédition et déclaration de retour verrouillent d’abord la même commande. Les tests de concurrence PostgreSQL vérifient leur exclusion mutuelle et la remise en stock unique.
- Les anciens achats sans ventilation fiable, stock identifiable ou paiement lié sont bloqués avec une explication. Une commande déjà expédiée ou possédant un dossier de retour conserve son parcours existant.
- Les erreurs de stock sont isolées par une transaction interne : une confirmation de remboursement reçue ne doit pas être effacée par l’échec de la remise en stock.

## Installation

Appliquer toutes les migrations, notamment `retours.0008`, `expeditions.0003`, `annulations.0001` et `annulations.0002`. Les contrôles fonctionnels utilisent Stripe simulé et une base de test distincte ; ne pas rembourser une véritable commande pour vérifier l’interface.

## Suivi quotidien

L’accueil vendeuse présente **Annulations à vérifier** aux comptes disposant de l’accès service client. La liste `/gestion/annulations/` distingue les remboursements à vérifier, les confirmations en attente, le stock à réintégrer et les opérations terminées. Les problèmes à corriger passent en premier, puis l’ancienneté. Les simulations sont accessibles séparément en développement et ne gonflent pas les priorités de la boutique.

Chaque dossier affiche ses deux notifications exactes : annulation enregistrée et confirmation du remboursement. Leur état et leur destinataire sont lus depuis le message conservé. Une relance cible uniquement le message concerné, avec les contrôles d’accès et de concurrence du suivi des e-mails existant. Les pages de consultation ne déclenchent ni remboursement, ni envoi, ni remise en stock.

Les nouvelles remises en stock conservent un mouvement par déclinaison : nom, taille, couleur, quantité totale avant, quantité ajoutée, quantité totale après et unités réservées. Le mouvement est enregistré dans la même transaction que la remise en stock. Un échec de son enregistrement annule le changement de stock, tout en conservant le remboursement confirmé. Les valeurs sont historiques et ne représentent pas forcément le stock actuel. Les reprises par la vendeuse et les traitements automatiques sont identifiés. Les anciennes annulations ne sont pas reconstituées ; les simulations ne créent aucun mouvement réel.

## Motifs et notes internes

Le formulaire propose quatre motifs rapides : demande du client, erreur de commande, article indisponible et autre. Le texte proposé reste personnalisable. Un choix de motif ne remplace pas silencieusement un texte déjà personnalisé ; l’interface propose de l’appliquer explicitement.

La note interne facultative est chiffrée et réservée aux gestionnaires du service client. Elle peut être renseignée à l’annulation, puis modifiée avec son propre bouton d’enregistrement. Le dernier auteur, la date et la révision sont conservés. Le contrôle de révision empêche deux onglets d’écraser des notes différentes sans avertissement. Le motif public, les e-mails, le suivi client et les exports destinés au client ne contiennent pas la note interne.

## Actualisation du dossier

Le suivi utilise une lecture privée des seules informations déjà enregistrées : remboursement, remise en stock et notifications. L’écran se met à jour par régions, sans recharger la page ni remplacer la note en cours de saisie. Le contrôle périodique ne contacte pas Stripe, ne déclenche pas de remboursement et n’envoie aucun e-mail.

Le suivi se met en pause lorsque l’onglet est masqué ou le réseau indisponible. Les erreurs espacées, l’expiration de session et la fin du suivi automatique restent expliquées par un message et une action de vérification manuelle. Les motifs, les notes et les confirmations gardent leurs propres actions explicites.

Ces ajouts utilisent aussi `annulations.0003_cancellation_reasons_internal_note`.
