# Comptabilité et exports

La page `/gestion/comptabilite/` est accessible depuis Mon compte, la rubrique
Comptabilité du menu, le tableau de bord et Commandes et factures. Elle utilise
les mêmes droits que la gestion des retours et des factures. Un client ou un
gestionnaire du catalogue seul ne peut ni consulter les montants ni télécharger
les fichiers.

## Choisir une période

La vendeuse choisit un mois et une année. Les bornes suivent le calendrier
Europe/Paris, changements d’heure inclus. Les simulations et les commandes en
attente sont exclues.

- Encaissements : paiements confirmés pendant le mois, au montant réellement
  enregistré lors du paiement.
- Remboursements : remboursements confirmés pendant le mois, y compris ceux
  d’une vente plus ancienne. La livraison remboursée est déjà incluse dans le
  montant du remboursement, elle n’est pas ajoutée une seconde fois.
- Solde des mouvements : encaissements moins remboursements, avant commissions
  du prestataire de paiement.
- Factures : documents dont la date d’émission appartient au mois. Cette période
  peut différer de celle du paiement.

Les paiements sans montant confirmé ne sont pas remplacés par le prix du panier.
Les opérations sans date ne sont pas affectées arbitrairement à un mois. Ces
informations manquantes sont signalées au-dessus des téléchargements : il faut
les vérifier avant de transmettre un dossier présenté comme complet.

## Télécharger le dossier du mois

Le bouton principal télécharge `dossier-comptable-AAAA-MM.zip` avec les PDF des
factures émises, `index-factures.csv`, `ventes-AAAA-MM.csv`, `RECAPITULATIF.txt` et
`A-LIRE.txt`. Les remboursements sont compris dans le fichier des ventes.

Le récapitulatif distingue les encaissements des totaux des factures émises,
indique la date de génération à Paris et les pièces ou informations manquantes.
Un mois contenant des paiements sans facture, des remboursements seuls ou des
montants à vérifier reste exportable, avec la mention « À compléter » appropriée.
Une facture liée à un mouvement mais émise un autre mois est référencée dans le
récapitulatif ; son PDF se télécharge depuis son mois d’émission ou sa commande.
Les téléchargements séparés restent disponibles dans le volet du même nom.

## Rechercher et vérifier

La recherche porte sur le nom ou l’e-mail historique du client, la référence de
commande ou le numéro de facture, sans distinction de casse ou d’accent. Le
filtre Paiements / Remboursements concerne seulement les mouvements. Les totaux
et tous les téléchargements restent ceux du mois entier, indépendamment de la
recherche. Effacer les filtres conserve la période choisie.

Les commandes payées sans facture, les mouvements et les factures sont paginés
par 25, avec accès à toutes les entrées. Chaque pagination conserve les filtres
et les pages des autres listes. Une recherche sans résultat propose de retrouver
la liste complète du mois. L’export ne dépend jamais de la page affichée.

## Exporter les ventes

`ventes-AAAA-MM.csv` comporte une ligne par paiement ou remboursement confirmé.
Il utilise UTF-8 avec BOM, des points-virgules et des virgules décimales pour
l’ouverture dans Excel. Les références, dates, client, facture, articles avant
remise, remise, livraison et montant du mouvement permettent les rapprochements.
Les remboursements sont des mouvements négatifs. Les codes promotionnels et
avoirs commerciaux ne créent pas un deuxième mouvement d’encaissement.

Les bases HT, TVA et taux proviennent uniquement d’une facture émise conservant
les informations fiscales de la vente. Les données inconnues ou incohérentes
restent vides avec une observation. Les remboursements ne reçoivent pas de
ventilation fiscale estimée : le comptable doit les rapprocher des avoirs
comptables. Le fichier n’utilise ni les prix actuels ni le profil actuel d’un
client ou de la boutique pour réécrire une ancienne vente.

Les textes pouvant être interprétés comme une formule par un tableur sont
neutralisés. Les montants restent de vraies valeurs numériques.

## Télécharger les factures ensemble

`factures-AAAA-MM.zip` contient :

- `factures/` : un PDF par facture déjà émise ;
- `index-factures.csv` : numéro, date, commande, client et montants ;
- `A-LIRE.txt` : explication de la période et du contenu.

Les PDF reprennent le snapshot immuable de la facture, avec son numéro d’origine,
ses coordonnées, ses articles et ses totaux. Le téléchargement ne crée aucune
facture et n’attribue aucun nouveau numéro. Les commandes payées du mois sans
facture sont listées séparément, avec un lien vers leur aperçu. L’émission reste
une action explicite dans la fiche de commande, après vérification.

Un document incohérent bloque l’archive entière avec une explication et un lien
vers la facture précise à vérifier ; aucun ZIP partiel n’est téléchargé. Un lien
permet de revenir au même mois. La limite est de 500 factures et 50 Mo de contenu
non compressé par archive, CSV et récapitulatif compris. Au-delà, les documents restent accessibles individuellement
depuis Commandes et factures. Les fichiers temporaires sont privés et fermés
après la réponse, sans copie dans les médias publics.

## Périmètre et exploitation

Ces fichiers portent sur l’activité enregistrée sur le site. Ils ne remplacent
ni un FEC, ni les relevés bancaires, ni les relevés du prestataire de paiement
(commissions et virements). Aucun e-mail, paiement, remboursement ou changement
fiscal n’est déclenché par la consultation ou le téléchargement.

La page et les téléchargements sont privés, non indexables et non mis en cache.
Les exports contiennent des données clients : les transmettre uniquement aux
personnes concernées par la comptabilité.

Le rendu PDF utilise ReportLab 5.1.0 et ses polices Vera intégrées, sans chargement
de ressources externes. L’installation habituelle de `requirements.txt` suffit.
Aucune migration de données n’est nécessaire pour cette fonctionnalité.

Les tests `facturation.test_accounting_data`, `facturation.test_accounting_views`,
`facturation.test_accounting_search`, `facturation.test_accounting_bundle`,
`facturation.test_accounting_polish` et `facturation.test_pdf` couvrent les périodes, montants, remboursements,
permissions, archives, documents historiques et erreurs. Les PDF de vérification
utilisent des données fictives dans une base de test, sans émission de facture
réelle.
