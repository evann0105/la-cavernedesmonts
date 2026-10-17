# Retours et remboursements

## Accès
- Public : `/retours/`, lien de déclaration et suivi dans le mail de commande payée.
- Compte client : « Mes retours et remboursements », et lien depuis chaque commande.
- Gestion : `/gestion/retours/`, permission `retours.manage_returns` avec compte actif et staff. Les superadministrateurs disposent de cet accès.
- Les achats sans compte sont accessibles par lien signé privé. Le formulaire public renvoie une réponse générique et limite l’envoi par adresse IP et par commande.

## Cycle
1. Paiement confirmé par Stripe : mail de commande et lien de rétractation.
2. Déclaration client : dossier et quantités figés, mail avec l’adresse de retour. Depuis le 17 octobre 2026, l’accusé reprend aussi le nom/prénom confirmé dans la déclaration. Son unique destinataire est fixé depuis l’adresse de la commande, jamais depuis un champ modifiable du formulaire. En cas de changement de cette adresse avant envoi, l’accusé reste en attente pour vérification. Aucun e-mail supplémentaire à la boutique ni ancien accusé réexpédié par cette modification. Le motif est facultatif. Le site conserve la déclaration sans la faire dépendre d’une autorisation préalable de la gérante.
3. Remboursement : montant réellement payé pour chaque ligne, après ses propres remises, enregistré depuis Stripe dans `Order.paid_line_totals`. Une remise ciblée ne réduit pas le remboursement d’un autre produit. Pour plusieurs unités de la même ligne, les centimes sont répartis cumulativement entre les demandes sans dépasser le montant payé. Le dernier dossier couvrant tous les articles inclut les frais de livraison standard forfaitaires une fois. La boutique doit vérifier les règles si elle change de mode de facturation du transport.
4. Stripe confirme le remboursement. Les états en attente, échoués ou nécessitant une vérification ne sont pas assimilés à un remboursement réussi.
5. La gérante clique sur « Terminé » et indique les quantités reçues et revendables. Seules ces dernières sont remises en stock. Les articles endommagés ou non reçus restent à zéro.

Le remboursement peut intervenir avant réception si le client fournit une preuve d’expédition : le délai légal de remboursement ne doit pas attendre arbitrairement la clôture du dossier. La date et les instructions restent conservées dans les e-mails et événements.

## Protections
- Verrou PostgreSQL par commande : deux demandes simultanées ne peuvent pas retourner la même quantité au-delà de l’achat.
- Identifiant unique de soumission : répétition d’un POST ne crée pas un second dossier.
- Montants de remboursement figés côté serveur, plafond global par commande.
- Clé Stripe stable par dossier : les erreurs réseau ne créent pas une nouvelle tentative financière.
- Tentatives anciennes (>23 h) : recherche du remboursement existant, jamais recréation automatique après expiration possible de la clé Stripe.
- Remboursement externe non rapproché : blocage et vérification requise.
- Double clic sur « Terminé » : clôture et ajout de stock une fois, même entre plusieurs connexions.
- Instructions personnelles et détails des e-mails chiffrés en base. Pages privées non mises en cache, sans transmission du lien par Referer.

## Exploitation
- Adresse actuelle : La Caverne des Monts, 225 Rue des Monts Jura, 01410 Lélex.
- `RETURN_ADDRESS` et `RETURN_COSTS_TEXT` permettent d’adapter les instructions aux CGV validées. Les frais de retour ne sont pas arbitrairement imposés par le module.
- Aucun dépôt en magasin n’est annoncé sans accord de la gérante.
- Les anciennes commandes sans frais de livraison enregistrés doivent être rapprochées avec leur paiement avant remboursement.
- Stripe : configurer les événements Checkout existants et `refund.created`, `refund.updated`, `refund.failed` sur `/paiement/webhook/`.
- Planifier `python manage.py process_returns` chaque minute en production. Le worker lancé par `./start` inclut aussi le rapprochement des retours et les envois en attente ; relancer `./start` après mise à jour du worker.
- Les notifications non envoyées sont conservées en base et retentées. Comme pour tout SMTP, un accusé perdu après envoi peut occasionner un e-mail répété, sans répéter le remboursement.
- Un remboursement échoué ou externe nécessite un rapprochement avec Stripe ; il ne faut jamais forcer l’état « réussi » ni contourner le plafond. Cette version n’offre pas de deuxième tentative financière automatique après échec définitif.
- Les frais de retour d’un dossier SAV ou une indemnisation complémentaire doivent être vérifiés par la boutique. Le bouton SAV demande confirmation du montant ; aucun versement supplémentaire arbitraire n’est déclenché.
- Le tableau de bord sépare les remboursements confirmés à leur date, des encaissements bruts. Il ne calcule pas les frais Stripe ou le bénéfice.

## Validation
Tests PostgreSQL sur base isolée : déclarations qui se chevauchent, demandes et clôtures concurrentes, remboursements partiels, remises et arrondis, tentatives Stripe répétées/ambiguës, remboursements externes, accès client/invité/admin, CSRF, e-mails privés et reprise après erreur d’envoi.

La réception dans une vraie boîte et le remboursement réel nécessitent l’activation du service d’e-mail et de Stripe. Les tests utilisent une boîte locale et des réponses Stripe simulées, sans opération financière réelle.

## Démonstration locale
`python manage.py demo_return --username evannTest --email adresse@example.test` crée un dossier reproductible clairement marqué SIMULATION. La commande a le statut `demo`, est exclue des ventes et remboursements réels du tableau de bord et du classement des meilleures ventes. Elle ne contacte jamais Stripe, même si des clés sont configurées. « Terminé » enregistre les quantités fictives sans modifier le stock réel. La simulation est refusée en production. Les e-mails sont capturés dans la boîte locale avec le préfixe TEST.

## Deux suivis distincts
- `return_state` suit les articles : déclaré → expédié → reçu → contrôlé. La gérante peut enregistrer une réception sans expédition préalable ; cette étape est alors affichée « Non renseigné ».
- `refund_state` suit uniquement le paiement : à effectuer, en cours, confirmé, échoué ou à vérifier. Un remboursement confirmé ne prouve pas la réception des articles.
- Les boutons d’expédition, de réception et de contrôle sont réservés à la gestion, avec contrôle explicite à confirmer. Ils sont idempotents, historisés et ne touchent ni au paiement ni au stock.
- Les quantités reçues et revendables restent validées à la clôture ; le formulaire de clôture confirme aussi le contrôle final. La remise en stock reste réservée au clic « Terminé ».
- La migration reprend les dossiers existants sans créer de remboursement ni modifier les stocks. Les dates d’expédition ou de réception inconnues ne sont pas inventées.

## Expéditions et justificatifs privés
- Depuis le dossier de commande ou « Mes retours », le client ouvre « Ajouter un suivi ou un justificatif ». Il peut renseigner le transporteur, le numéro de suivi et/ou une photo de son reçu (JPEG, PNG, WebP, 5 Mo maximum). Une confirmation explicite d’expédition est demandée.
- Une déclaration encore au stade « Déclaré » passe à « Expédié ». Une réception ou un contrôle déjà enregistré ne régresse jamais. Le dépôt ne déclenche ni remboursement ni mouvement de stock.
- Plusieurs colis/corrections sont conservés sous forme de nouveaux envois historisés, jusqu’à dix par dossier. Un identifiant de dépôt protège contre le double clic. Les dossiers terminés n’acceptent plus de nouveaux dépôts.
- Les justificatifs sont normalisés en WebP, puis stockés chiffrés en base, avec les transporteurs et numéros de suivi. Ils ne sont jamais publiés dans MEDIA_ROOT ni envoyés en pièce jointe.
- Le téléchargement privé exige le propriétaire connecté et vérifié, son lien signé de commande, ou un gestionnaire des retours vérifié. Réponse sans cache, sans Referer, avec téléchargement forcé et type de contenu fixe.
- La liste et le détail de gestion signalent les nouveaux envois. La gérante peut les marquer comme consultés sans effacer les pièces ni l’historique.
- `RETURN_NOTIFICATION_EMAIL` permet en complément un e-mail à la gérante via la file d’envoi existante, dès que son adresse et le service d’envoi sont configurés. Le mail contient uniquement un lien de gestion nécessitant connexion, sans justificatif ni lien signé client. La notification dans la gestion fonctionne sans SMTP.

## Détail des montants
- Chaque nouvelle ligne de retour conserve le prix unitaire au moment de l’achat (`unit_amount`), en plus du montant net déjà figé. Aucun affichage ne dépend du prix courant du catalogue.
- Les dossiers clients, la gestion et les nouveaux e-mails détaillent les quantités, montants avant remises, remises réellement appliquées aux lignes retournées, montants nets, livraison incluse et total du dossier.
- La ventilation conserve les arrondis du calcul existant : plusieurs demandes partielles totalisent exactement la somme payée, livraison comprise une seule fois. Aucun montant de remboursement existant n’est modifié par la migration.
- Les anciennes lignes récupèrent le prix seulement lorsqu’il est identifiable dans les articles figés de la commande. Une ventilation incomplète ou incohérente est signalée explicitement ; les montants manquants ne sont pas inventés.
- Les e-mails déjà envoyés ou en attente conservent leur contenu initial ; le détail s’applique aux nouvelles notifications générées après mise à jour.

## Actions à prévoir et clôture vérifiée
- Le tableau de bord propose les nouveaux retours, réceptions à contrôler, remboursements à effectuer/en attente/en échec ou à vérifier, nouveaux justificatifs, dossiers prêts à clôturer et dossiers anciens. Les compteurs sont indépendants de la période des ventes, excluent les simulations et les dossiers terminés, et sont réservés aux gestionnaires des retours.
- Chaque compteur ouvre la liste filtrée correspondante. La liste prioritaire met les échecs/vérifications puis les dossiers anciens et réceptions à contrôler en tête. `RETURN_ACTION_AGE_DAYS` règle l’ancienneté opérationnelle, sept jours par défaut.
- La gérante saisit les quantités reçues et revendables, puis consulte un récapitulatif sans effet sur le stock. Elle peut corriger les quantités ou confirmer explicitement la clôture.
- La confirmation est signée, liée au dossier et à l’administrateur, valable quinze minutes. Les quantités et l’état du dossier sont revérifiés sous verrou avant clôture. Un dossier modifié exige une nouvelle vérification ; un double clic après clôture ne rejoue ni le stock ni le mail.
- Le mail de clôture détaille le remboursement, les quantités reçues, remises en stock ou non remises, ainsi que le détail financier. Il est préparé une seule fois dans la file d’envoi ; sa livraison dépend du service e-mail configuré.

### Remises ciblées et anciennes commandes

- Les nouvelles sessions identifient chaque ligne par la référence de commande et son index dans les métadonnées du produit Stripe. La liste complète des lignes payées est lue, y compris ses pages suivantes ; les montants, quantités, devise, remises et total sont contrôlés avant confirmation.
- Les commandes sans remise conservent leurs prix d’achat exacts. Une commande ramenée à zéro par une remise est confirmée seulement si Stripe indique une session terminée sans paiement nécessaire. Aucun remboursement bancaire n’est créé pour une ligne gratuite.
- Une ancienne commande remisée sans ventilation est rapprochée de Stripe avant calcul. Sans métadonnées, seul un rapprochement unique par description, quantité et prix d’achat est accepté. Une réponse absente ou ambiguë bloque le calcul approximatif.
- Les anciens dossiers ne sont jamais réécrits automatiquement. Avant un nouvel envoi bancaire, leur montant est comparé à la ventilation exacte ; un écart exige une vérification par la boutique. Les tentatives déjà identifiées auprès de Stripe gardent leur historique et leur rapprochement bancaire.
