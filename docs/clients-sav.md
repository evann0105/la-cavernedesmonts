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

Le suivi utilise quatre états : **À traiter**, **En cours**, **En attente du client**, **Résolu**. Chaque modification et note interne est datée et attribuée. La résolution demande une note expliquant la solution. Un dossier résolu peut être rouvert.

Le formulaire **Nouveau dossier SAV** propose aussi une recherche directe par nom, e-mail ou téléphone. Cliquer sur **Choisir cette fiche** préremplit les coordonnées et propose uniquement les commandes du client choisi. La demande, la priorité et la date déjà saisies sont conservées. Le bouton **Saisir un nouveau client** libère les coordonnées sans effacer la demande. La recherche fonctionne également sans JavaScript et n’enregistre aucun dossier à elle seule.

Une fiche invitée reste rattachée à sa commande ou demande d’origine même avec **Sans commande précise**. Une ancienne fiche d’avoir associée à un compte supprimé ne peut pas être réattribuée silencieusement à un nouveau compte ayant repris son e-mail : sélectionner la fiche actuelle du client.

## Relier les retours et les avoirs

Dans le dossier, **Retours et avoirs liés → Choisir les liens** ouvre une page dédiée. Cocher les retours, codes personnels ou documents d’avoir concernés, puis indiquer le motif du rattachement. Plusieurs opérations et plusieurs commandes du même client peuvent être retenues. Les références des commandes, états et montants permettent de les distinguer.

Les opérations liées sont ensuite accessibles directement depuis le dossier. Décocher une opération retire son lien sans supprimer l’opération ; l’auteur, la date et le motif restent dans l’historique. Un conflit de modification est signalé. Un code réattribué à une autre personne n’est plus affiché dans le dossier d’origine.

Ce rattachement n’émet aucun code ni remboursement, et n’envoie aucun e-mail. Les montants, dates et états restent gérés dans les pages dédiées aux retours et aux avoirs.

## Les rappels sur le tableau de bord

Le bloc **SAV : les rappels à prévoir** distingue les rappels du jour, les retards et les demandes urgentes. Chaque compteur ouvre la liste correspondante. Les cinq dossiers prioritaires sont accessibles directement. Les dossiers résolus sont exclus ; un dossier urgent peut aussi être compté dans les rappels. Ces rappels restent indépendants de la période choisie pour les ventes. Aucune notification n’est envoyée automatiquement.

Dans **Dossiers SAV**, le filtre **Échéance** permet d’afficher aujourd’hui, les retards, les deux, ou toutes les dates. Les jours suivent l’heure de Paris.

## Corriger une demande

Depuis un dossier, **Corriger le dossier** ouvre une page séparée : nom, téléphone, objet, demande, canal, catégorie et commande concernée. Le compte et l’e-mail ne sont pas réattribués. Seules les commandes de cette fiche client sont proposées. Retirer le lien vers une commande conserve le dossier dans la fiche d’origine.

Un motif est demandé. L’historique conserve l’auteur, la date et les informations avant/après. Une modification concurrente est signalée au lieu d’écraser le travail d’un autre membre de l’équipe.

## Photos et justificatifs

La section **Pièces jointes** accepte les photos JPEG, PNG, WebP et les PDF, avec une description facultative. Limites : 5 Mo par fichier, 20 fichiers et 25 Mo par dossier, documents retirés compris. Les images sont normalisées en WebP ; les PDF sont proposés au téléchargement.

Les fichiers et leurs noms sont chiffrés dans la base, sans URL publique. Seul le personnel autorisé au SAV peut les télécharger. Le retrait nécessite une confirmation, révoque le téléchargement et laisse une trace dans l’historique ; ce n’est pas un effacement définitif du document. Aucun fichier n’est envoyé au client ni affiché dans son compte.

## Protection et limites

- L’espace est réservé au personnel disposant du droit de gestion des retours. Les clients n’accèdent ni à l’annuaire ni aux notes internes.
- Les coordonnées, demandes et notes sont chiffrées. Les réponses privées ne sont pas mises en cache.
- Les comptes déjà liés sont prioritaires. Les achats invités sont regroupés par e-mail exact ; un nom ou téléphone identique ne fusionne pas automatiquement deux personnes. Pour un client connu seulement par téléphone, ouvrir les demandes suivantes depuis sa fiche conserve leur rattachement. Deux demandes saisies séparément ne sont pas fusionnées par leur seul numéro.
- Les identités sont revérifiées à l’enregistrement. Un double clic ne crée pas un deuxième dossier, et une modification simultanée est signalée au lieu d’écraser le suivi.
- Le SAV n’envoie pas d’e-mail, ne rembourse pas et ne change pas les stocks. Ces actions restent dans les outils dédiés.
- L’export personnel inclut les demandes explicitement liées au compte ou à ses commandes, sans l’historique interne du personnel. Les dossiers ouverts suspendent la revue de suppression des comptes et commandes associés. Aucune suppression automatique n’est ajoutée.

## Installation et vérification

Appliquer les migrations de l’application `clients`, puis les droits du rôle PostgreSQL de l’application. Couverture ciblée : `clients`, `tableau`, `espace.test_privacy_sav`, `espace.test_privacy_sav_links` et `core.test_navigation`.
