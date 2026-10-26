# Clients et SAV

Dans **Mon compte → Administration → Service client → Clients et SAV**, deux pages séparées donnent accès à l’annuaire et aux dossiers SAV.

## Retrouver un client

La recherche accepte le nom ou une partie de l’adresse e-mail, sans tenir compte des accents ou majuscules. Une fiche rassemble commandes, factures déjà émises, retours, documents d’avoir, codes personnels et dossiers SAV. Les simulations sont identifiées et exclues des montants payés. Un montant historique non confirmé n’est pas inventé.

Les liens ouvrent les documents et les outils dédiés : expédition, retour ou création d’un avoir. Consulter une fiche ne modifie jamais une commande et n’émet pas de facture.

## Noter un appel

1. Chercher le client, ouvrir sa fiche puis **Ouvrir un dossier SAV**. Pour un nouveau client, **Noter une demande** permet de renseigner ses coordonnées sans créer de compte.
2. Indiquer l’objet, la demande, son canal et éventuellement une commande du client. Au moins un téléphone ou un e-mail est demandé.
3. Choisir une priorité et, si utile, une date **À reprendre le**.
4. Enregistrer. Le dossier reçoit une référence SAV et apparaît dans la page Dossiers SAV.

Le suivi utilise quatre états : **À traiter**, **En cours**, **En attente du client**, **Résolu**. Chaque modification et note interne est datée et attribuée. La résolution demande une note expliquant la solution. Un dossier résolu peut être rouvert. Les dates servent à organiser les rappels dans le filtre **À reprendre** ; elles n’envoient aucun message automatiquement.

## Protection et limites

- L’espace est réservé au personnel disposant du droit de gestion des retours. Les clients n’accèdent ni à l’annuaire ni aux notes internes.
- Les coordonnées, demandes et notes sont chiffrées. Les réponses privées ne sont pas mises en cache.
- Les comptes déjà liés sont prioritaires. Les achats invités sont regroupés par e-mail exact ; un nom ou téléphone identique ne fusionne pas automatiquement deux personnes. Pour un client connu seulement par téléphone, ouvrir les demandes suivantes depuis sa fiche conserve leur rattachement. Deux demandes saisies séparément ne sont pas fusionnées par leur seul numéro.
- Les identités sont revérifiées à l’enregistrement. Un double clic ne crée pas un deuxième dossier, et une modification simultanée est signalée au lieu d’écraser le suivi.
- Le SAV n’envoie pas d’e-mail, ne rembourse pas et ne change pas les stocks. Ces actions restent dans les outils dédiés.
- L’export personnel inclut les demandes explicitement liées au compte ou à ses commandes, sans l’historique interne du personnel. Les dossiers ouverts suspendent la revue de suppression des comptes et commandes associés. Aucune suppression automatique n’est ajoutée.

## Installation et vérification

Appliquer les migrations de l’application `clients`, puis les droits du rôle PostgreSQL de l’application. Couverture ciblée : `clients.tests_domain`, `clients.tests_selectors`, `clients.tests_views`, `espace.test_privacy_sav`.
